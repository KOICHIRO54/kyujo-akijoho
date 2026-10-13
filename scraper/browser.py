"""設定ファイルに書いた手順どおりにブラウザを動かし、空き表のページを集める。

予約システムの画面遷移は自治体ごとに違い、作り直しも多い。
そのため遷移手順はコードではなく scraper/config/*.json に書き、
画面が変わったら設定だけ直せばよいようにしている。

各ページは debug/<アダプタ名>/ に HTML とスクリーンショットで保存する。
GitHub Actions の実行結果からダウンロードして、手順の調整に使う。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PWTimeout

USER_AGENT = (
    "kyujo-akijoho/0.1 (team-internal availability checker; "
    "+https://github.com/KOICHIRO54/kyujo-akijoho)"
)
WAIT_BETWEEN_ACTIONS = 2.0  # 秒。相手サーバーへの負荷を抑えるため必ず待つ


class StepError(RuntimeError):
    pass


def robots_allows(url: str) -> bool:
    """robots.txt で禁止されていないか確認する。取得できない場合は許可扱い。"""
    parts = urlparse(url)
    rp = robotparser.RobotFileParser()
    rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True
    return rp.can_fetch(USER_AGENT, url)


class Recorder:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def save(self, page: Page, label: str) -> str:
        self.n += 1
        safe = re.sub(r"[^\w\-]+", "_", label)[:40]
        stem = self.out_dir / f"{self.n:02d}_{safe}"
        html = page.content()
        stem.with_suffix(".html").write_text(html, encoding="utf-8")
        try:
            page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        except Exception:
            pass
        (self.out_dir / "pages.txt").open("a", encoding="utf-8").write(
            f"{self.n:02d}\t{label}\t{page.url}\n"
        )
        return html


def _target(page: Page):
    """フレーム構成のサイトにも対応するため、最も中身の多いフレームを返す。"""
    frames = page.frames
    if len(frames) == 1:
        return page
    return max(frames, key=lambda f: len(f.content() or ""))


def _locator(scope, step: dict):
    if "selector" in step:
        return scope.locator(step["selector"]).first
    text = step["text"]
    exact = step.get("exact", False)
    for role in ("link", "button"):
        loc = scope.get_by_role(role, name=text, exact=exact)
        if loc.count():
            return loc.first
    loc = scope.get_by_text(text, exact=exact)
    if loc.count():
        return loc.first
    # input type=submit/button の value は get_by_text で拾えない
    loc = scope.locator(f"input[value*='{text}']")
    if loc.count():
        return loc.first
    raise StepError(f"見つからない: {text}")


def run_step(page: Page, step: dict, rec: Recorder) -> None:
    kind = step["do"]
    scope = _target(page) if step.get("in_frame", True) else page
    if kind == "goto":
        page.goto(step["url"], wait_until="domcontentloaded")
    elif kind == "click":
        try:
            _locator(scope, step).click()
        except StepError:
            if step.get("optional"):
                return
            raise
    elif kind == "fill":
        _locator(scope, step).fill(step["value"])
    elif kind == "select":
        _locator(scope, step).select_option(label=step["label"])
    elif kind == "check":
        _locator(scope, step).check()
    elif kind == "wait":
        time.sleep(float(step.get("seconds", 1)))
    else:
        raise StepError(f"不明な手順: {kind}")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PWTimeout:
        pass
    time.sleep(WAIT_BETWEEN_ACTIONS)
    if step.get("snapshot", True):
        rec.save(page, step.get("text") or step.get("url") or kind)


def collect_pages(page: Page, steps: list[dict], next_step: dict | None,
                  pages: int, rec: Recorder) -> list[str]:
    """手順を実行し、空き表のページを next_step で送りながら pages 枚集める。"""
    for step in steps:
        run_step(page, step, rec)
    htmls = [_target(page).content()]
    for i in range(1, pages):
        if not next_step:
            break
        try:
            run_step(page, {**next_step, "snapshot": True}, rec)
        except StepError:
            break
        htmls.append(_target(page).content())
    return htmls
