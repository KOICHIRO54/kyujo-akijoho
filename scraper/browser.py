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


def _expand(value: str) -> str:
    """設定値の {MM} {DD} {YYYY} を今日の日付（日本時間）に置き換える。"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    return (value.replace("{YYYY}", f"{now.year}")
                 .replace("{MM}", f"{now.month:02d}")
                 .replace("{DD}", f"{now.day:02d}"))


def _select(scope, step: dict) -> None:
    """プルダウンを選ぶ。label は完全一致、contains は部分一致（複数語はすべて含むもの）。"""
    sel = scope.locator(step["selector"]).first
    if not sel.count():
        raise StepError(f"プルダウンが見つからない: {step['selector']}")
    # 選択肢の後ろに全角空白が詰まっていることが多いので、空白を除いて比べる
    norm = lambda s: re.sub(r"[\s　]+", "", s or "")
    options = sel.evaluate("e => Array.from(e.options).map(o => [o.value, o.text])")
    if "label" in step:
        want = norm(_expand(step["label"]))
        match = next((v for v, t in options if norm(t) == want), None)
        desc = want
    else:
        words = step["contains"] if isinstance(step["contains"], list) else [step["contains"]]
        match = next((v for v, t in options if all(w in norm(t) for w in words)), None)
        desc = words
    if match is None:
        raise StepError(f"選択肢が見つからない: {desc}（候補: {[norm(t) for _, t in options][:40]}）")
    sel.select_option(value=match, timeout=10000)


def _click_in_row(scope, step: dict) -> None:
    """表の中で row_text を含む行にある、text のボタン／リンクを押す。"""
    rows = scope.locator("tr", has_text=step["row_text"])
    if not rows.count():
        raise StepError(f"行が見つからない: {step['row_text']}")
    row = rows.last  # 入れ子の表では一番内側の行が最後に来る
    text = step["text"]
    for loc in (row.get_by_role("button", name=text), row.get_by_role("link", name=text),
                row.locator(f"input[value*='{text}']"), row.get_by_text(text)):
        if loc.count():
            loc.first.click()
            return
    raise StepError(f"行内に見つからない: {step['row_text']} / {text}")


def run_step(page: Page, step: dict, rec: Recorder) -> None:
    kind = step["do"]
    scope = _target(page) if step.get("in_frame", True) else page
    try:
        if kind == "goto":
            page.goto(step["url"], wait_until="domcontentloaded")
        elif kind == "click":
            _locator(scope, step).click()
        elif kind == "click_row":
            _click_in_row(scope, step)
        elif kind == "fill":
            _locator(scope, step).fill(_expand(step["value"]))
        elif kind == "select":
            _select(scope, step)
        elif kind == "check":
            _locator(scope, step).check()
        elif kind == "wait":
            time.sleep(float(step.get("seconds", 1)))
        else:
            raise StepError(f"不明な手順: {kind}")
    except StepError:
        if step.get("optional"):
            return
        raise
    _settle(page, step, rec)


def _settle(page: Page, step: dict, rec: Recorder) -> None:
    """画面の読み込みを待ち、相手サーバーのために間を空けてから画面を保存する。"""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PWTimeout:
        pass
    time.sleep(WAIT_BETWEEN_ACTIONS)
    if step.get("snapshot", True):
        label = (step.get("row_text") or step.get("text") or step.get("label")
                 or str(step.get("contains") or "") or step.get("url") or step["do"])
        rec.save(page, label)


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


def collect_by_dates(page: Page, steps: list[dict], js_template: str,
                     dates: list, rec: Recorder) -> list[str]:
    """手順で施設の空き画面まで進んだあと、日付ごとに画面を切り替えて集める。

    js_template はサイト側のカレンダーが呼ぶ関数（例: selectCalendarDate({Y},{M},{D})）。
    クリックと同じ関数を呼ぶだけなので、画面上で日付を押すのと同じ動きになる。
    """
    for step in steps:
        run_step(page, step, rec)
    htmls = []
    for d in dates:
        scope = _target(page)
        fmt = dict(Y=d.year, M=d.month, D=d.day, MM=f"{d.month:02d}", DD=f"{d.day:02d}")
        if js_template.startswith("http"):
            # 日付をURLで指定できるサイトは、そのページを直接開く
            page.goto(js_template.format(**fmt), wait_until="domcontentloaded")
            time.sleep(WAIT_BETWEEN_ACTIONS)
            htmls.append(rec.save(page, d.isoformat()) if d == dates[0] else _target(page).content())
            continue
        js = js_template.format(**fmt)
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=20000):
                scope.evaluate(js)
        except PWTimeout:
            pass  # 画面遷移しない作りの場合もあるので、そのまま読む
        except Exception as e:
            # 遷移中に評価の文脈が消えるのは正常
            if "context was destroyed" not in str(e) and "navigat" not in str(e):
                raise
        time.sleep(WAIT_BETWEEN_ACTIONS)
        htmls.append(rec.save(page, d.isoformat()) if d == dates[0] else _target(page).content())
    return htmls
