"""空き状況を取得して docs/data/ に書き出す。

使い方:
    python -m scraper.run                 # 有効な全システムを取得
    python -m scraper.run --only aichi_kyodo
    python -m scraper.run --fields-only   # 球場一覧 (fields.json) だけ作り直す
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "scraper" / "config"
FIELDS_CSV = ROOT / "data" / "fields.csv"
OUT_DIR = ROOT / "docs" / "data"
DEBUG_DIR = ROOT / "debug"
JST = timezone(timedelta(hours=9))


def load_fields() -> list[dict]:
    with FIELDS_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_fields_json(fields: list[dict]) -> None:
    """閲覧ページ用の球場一覧。硬式不可の行は除く。"""
    out = []
    for r in fields:
        if r["硬式"] == "不可":
            continue
        out.append({
            "id": r["id"],
            "name": r["球場名"],
            "pref": r["県"],
            "city": r["市町"],
            "address": r["所在地"],
            "system": r["予約システム"],
            "online": r["ネット空き確認"] == "○" or r["ネット空き確認"] == "△",
            "drive_min": int(r["車_目安分"]) if r["車_目安分"].isdigit() else None,
            "note": r["メモ"],
            "tel": r["電話"],
            "url": r["予約URL"],
            "adapter": r["取得アダプタ"],
        })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "fields.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def load_configs(only: str | None) -> dict[str, dict]:
    configs = {}
    for p in sorted(CONFIG_DIR.glob("*.json")):
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if only and p.stem != only:
            continue
        if not cfg.get("enabled") and not only:
            continue
        configs[p.stem] = cfg
    return configs


def target_dates(today, days: int, weekends_only: bool, holidays: set[str],
                 until: str | None = None) -> list:
    """取得する日。相手の負荷を抑えるため、既定では土日祝だけにする。"""
    out = []
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        if until and d.isoformat() > until:
            break
        if weekends_only and d.weekday() < 5 and d.isoformat() not in holidays:
            continue
        out.append(d)
    return out


def scrape(configs: dict[str, dict]) -> tuple[list[dict], dict]:
    # Playwright は取得時だけ必要なので、ここで読み込む
    from playwright.sync_api import sync_playwright

    from scraper.browser import USER_AGENT, Recorder, collect_by_dates, collect_pages, robots_allows
    from scraper.common import PARSERS, require_all_blocks

    today = datetime.now(JST).date()
    holidays = set(json.loads((OUT_DIR / "holidays.json").read_text(encoding="utf-8"))["dates"])
    slots: list[dict] = []
    status: dict[str, dict] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, cfg in configs.items():
            if cfg.get("start_url") and not robots_allows(cfg["start_url"]):
                status[name] = {"ok": False, "error": "robots.txt で禁止されているため取得しない"}
                continue
            for field_id, fcfg in cfg["fields"].items():
                key = f"{name}:{field_id}"
                rec = Recorder(DEBUG_DIR / name / field_id)
                context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP",
                                              viewport={"width": 1280, "height": 900})
                page = context.new_page()
                try:
                    steps = cfg.get("common_steps", []) + fcfg.get("steps", [])
                    walk = cfg.get("walk")
                    if walk:
                        dates = target_dates(today, int(walk.get("days", 60)),
                                             walk.get("weekends_only", True), holidays,
                                             fcfg.get("until"))
                        htmls = collect_by_dates(page, steps, walk.get("js") or walk["url"], dates, rec)
                    else:
                        htmls = collect_pages(page, steps, cfg.get("next"),
                                              int(cfg.get("pages", 1)), rec)
                    parser = PARSERS[cfg.get("parser", "generic")]
                    kwargs = {"block_contains": fcfg["block"]} if fcfg.get("block") else {}
                    if fcfg.get("all_blocks"):
                        kwargs["with_block"] = True
                    found = []
                    for html in htmls:
                        found += [s.to_dict() for s in parser(html, field_id, today, **kwargs)]
                    if fcfg.get("all_blocks"):
                        found = require_all_blocks(found, int(fcfg["all_blocks"]))
                    # 同じ日・枠が複数ページに出たら後勝ち
                    dedup = {(s["date"], s["slot"]): s for s in found}
                    slots += list(dedup.values())
                    status[key] = {"ok": bool(dedup), "count": len(dedup),
                                   "error": None if dedup else "空き表を読み取れず（debug/ を確認）"}
                except Exception as e:  # 1球場の失敗で全体を止めない
                    try:
                        rec.save(page, "error")
                    except Exception:
                        pass
                    status[key] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    traceback.print_exc()
                finally:
                    context.close()
        browser.close()
    return slots, status


def merge_and_write(slots: list[dict], status: dict) -> dict:
    """取得に失敗した球場は前回の結果を残し、古いデータだと分かるようにする。"""
    now = datetime.now(JST).isoformat(timespec="minutes")
    path = OUT_DIR / "availability.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    fresh_fields = {k.split(":", 1)[1] for k, v in status.items() if ":" in k and v["ok"]}
    kept = [s for s in prev.get("slots", []) if s["field"] not in fresh_fields]
    prev_fetched = prev.get("fetched_at", {})

    fetched_at = {f: prev_fetched.get(f) for f in {s["field"] for s in kept}}
    for f in fresh_fields:
        fetched_at[f] = now

    today = datetime.now(JST).date().isoformat()
    merged = [s for s in kept + slots if s["date"] >= today]
    merged.sort(key=lambda s: (s["date"], s["field"], s["slot"]))

    data = {"generated_at": now, "fetched_at": fetched_at, "status": status, "slots": merged}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="取得するシステム名（config のファイル名）")
    ap.add_argument("--fields-only", action="store_true")
    args = ap.parse_args(argv)

    write_fields_json(load_fields())
    if args.fields_only:
        return 0

    slots, status = scrape(load_configs(args.only))
    data = merge_and_write(slots, status)
    for k, v in status.items():
        mark = "OK " if v["ok"] else "NG "
        print(mark, k, v.get("count", ""), v.get("error") or "")
    print(f"合計 {len(data['slots'])} 枠を書き出しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
