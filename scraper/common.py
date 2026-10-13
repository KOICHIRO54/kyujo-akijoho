"""空き状況の表を読み取る共通処理。

自治体の予約システムは「日付×時間帯」の表に ○ / × などの記号を並べる形が多い。
ここではページ構造に依存しないよう、HTML中の <table> を総当たりで調べ、
日付らしき見出しと空き記号らしきセルの組み合わせを拾う。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date, timedelta

from bs4 import BeautifulSoup

# 記号 → 状態。画像の alt や title に入っている文言も拾えるように語句も含める。
STATUS_MAP = [
    (re.compile(r"^(○|◯|〇|空き?|空有|予約可|可)$"), "空き"),
    (re.compile(r"^(△|一部空き?|残りわずか)$"), "一部空き"),
    (re.compile(r"^(×|✕|✖|予約|予約済み?|満|不可|使用中|貸出中)$"), "予約済"),
    (re.compile(r"^(抽|抽選|抽選中|抽選受付中?)$"), "抽選"),
    (re.compile(r"^(休|休館|休場|閉|閉館|点検|保守|雨天|一般開放|時間帯なし|受付期間外|"
                r"－|-|―|ー|/|対象外|受付外|期間外)$"), "利用不可"),
]

DATE_PATTERNS = [
    # 2026/10/18, 2026-10-18, 2026年10月18日
    re.compile(r"(?P<y>20\d{2})\s*[/\-年.]\s*(?P<m>\d{1,2})\s*[/\-月.]\s*(?P<d>\d{1,2})"),
    # 10/18, 10月18日
    re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*[/月]\s*(?P<d>\d{1,2})(?!\d)"),
]


@dataclass
class Slot:
    field: str
    date: str          # YYYY-MM-DD
    slot: str          # 時間帯の表記（午前・9:00-11:00 など）
    status: str        # 空き / 一部空き / 予約済 / 抽選 / 利用不可
    raw: str           # 元の記号

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_status(text: str) -> str | None:
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return None
    for pattern, status in STATUS_MAP:
        if pattern.match(t):
            return status
    return None


def infer_date(text: str, today: date) -> date | None:
    """表の見出しから日付を読む。年がなければ today に近い年を当てる。"""
    t = (text or "").replace("　", " ")
    for pat in DATE_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        month, day = int(m.group("m")), int(m.group("d"))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        years = [int(m.group("y"))] if "y" in m.groupdict() and m.group("y") else [today.year - 1, today.year, today.year + 1]
        best = None
        for y in years:
            try:
                d = date(y, month, day)
            except ValueError:
                continue
            if best is None or abs((d - today).days) < abs((best - today).days):
                best = d
        if best and (today - timedelta(days=31)) <= best <= (today + timedelta(days=400)):
            return best
    return None


def cell_text(cell) -> str:
    """セル文字列。記号が画像のときは alt / title を使う。"""
    text = cell.get_text(" ", strip=True)
    if text:
        return text
    for img in cell.find_all("img"):
        for attr in ("alt", "title"):
            if img.get(attr):
                return img[attr].strip()
    return cell.get("title", "").strip()


def _grid(table) -> list[list[str]]:
    """rowspan / colspan を展開した二次元配列にする。"""
    grid: list[list[str | None]] = []
    for r, tr in enumerate(table.find_all("tr")):
        while len(grid) <= r:
            grid.append([])
        row = grid[r]
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while c < len(row) and row[c] is not None:
                c += 1
            text = cell_text(cell)
            rs = int(cell.get("rowspan", 1) or 1)
            cs = int(cell.get("colspan", 1) or 1)
            for dr in range(rs):
                while len(grid) <= r + dr:
                    grid.append([])
                target = grid[r + dr]
                for dc in range(cs):
                    while len(target) <= c + dc:
                        target.append(None)
                    target[c + dc] = text
            c += cs
    return [[x or "" for x in row] for row in grid if row]


def parse_tables(html: str, field_id: str, today: date) -> list[Slot]:
    """ページ中の表から空き枠を抜き出す。

    2つの向きに対応する:
      A) 見出し行に日付、行頭に時間帯（または施設名）
      B) 行頭に日付、見出し行に時間帯
    """
    soup = BeautifulSoup(html, "lxml")
    slots: dict[tuple, Slot] = {}

    for table in soup.find_all("table"):
        if table.find("table"):
            continue  # 入れ子の外側はスキップ（内側で処理される）
        grid = _grid(table)
        if len(grid) < 2:
            continue

        # A) 見出し行（上から3行以内）に日付が2つ以上ある
        for hr in range(min(3, len(grid))):
            header = grid[hr]
            date_cols = {i: infer_date(h, today) for i, h in enumerate(header)}
            date_cols = {i: d for i, d in date_cols.items() if d}
            if len(date_cols) < 2:
                continue
            for row in grid[hr + 1:]:
                label = next((x for x in row if x and not normalize_status(x) and not infer_date(x, today)), "")
                for i, d in date_cols.items():
                    if i < len(row):
                        st = normalize_status(row[i])
                        if st:
                            key = (d.isoformat(), label)
                            slots[key] = Slot(field_id, d.isoformat(), label or "終日", st, row[i])
            break
        else:
            # B) 1列目に日付が2行以上ある
            date_rows = [(r, infer_date(row[0], today)) for r, row in enumerate(grid) if row]
            date_rows = [(r, d) for r, d in date_rows if d]
            if len(date_rows) < 2:
                continue
            header_idx = max(0, date_rows[0][0] - 1)
            header = grid[header_idx]
            for r, d in date_rows:
                row = grid[r]
                for i in range(1, len(row)):
                    st = normalize_status(row[i])
                    if st:
                        label = header[i] if i < len(header) and header[i] else f"枠{i}"
                        slots[(d.isoformat(), label)] = Slot(field_id, d.isoformat(), label, st, row[i])

    return sorted(slots.values(), key=lambda s: (s.date, s.slot))


def parse_aichi_kyodo(html: str, field_id: str, today: date,
                      block_contains: str | None = None) -> list[Slot]:
    """あいち共同利用型施設予約システムの「利用可能な施設と空き状況」画面を読む。

    各枠のセルに hidden の selectInfo（館:施設:YYYYMMDD:枠:開始:終了:…）があり、
    状態はアイコン画像の alt に入っている。見出し行（time-table1）が枠の名前。
    1つの館に複数の施設（グラウンド・室内練習場など）があるときは、
    block_contains を含む名前の施設だけを読む。
    """
    soup = BeautifulSoup(html, "lxml")
    slots: list[Slot] = []
    for status_row in soup.select("tr"):
        cells = status_row.find_all("td", class_="time-table2", recursive=False)
        if not cells:
            continue
        block = status_row.find_parent("table")
        label_td = block.find("td", id="blabel") if block else None
        block_name = label_td.get_text(" ", strip=True) if label_td else ""
        if block_contains and block_contains not in block_name:
            continue
        header_row = status_row.find_previous_sibling("tr")
        labels = [td.get_text(strip=True) for td in header_row.find_all("td", class_="time-table1")] if header_row else []
        for i, td in enumerate(cells):
            info = td.find("input", attrs={"name": "selectInfo"})
            img = td.find("img")
            if not info or not img:
                continue
            parts = (info.get("value") or "").split(":")
            if len(parts) < 6 or not re.fullmatch(r"\d{8}", parts[2]):
                continue
            d = date(int(parts[2][:4]), int(parts[2][4:6]), int(parts[2][6:8]))
            raw = img.get("alt", "").strip()
            status = normalize_status(raw)
            if not status:
                continue
            start, end = parts[4], parts[5]
            name = unicodedata.normalize("NFKC", labels[i]) if i < len(labels) else f"枠{i + 1}"
            if start.isdigit() and end.isdigit():
                times = f"{start[:2]}:{start[2:]}-{end[:2]}:{end[2:]}"
                # 「17時～」のように時刻だけの見出しは時刻表記に置き換える
                slot = times if re.fullmatch(r"\d{1,2}時[～~]?", name) else f"{name} {times}"
            else:
                slot = name
            slots.append(Slot(field_id, d.isoformat(), slot, status, raw))
    return slots


PARSERS = {"generic": parse_tables, "aichi_kyodo": parse_aichi_kyodo}


def parse_nagoya_sporec(html: str, field_id: str, today: date) -> list[Slot]:
    """名古屋市スポレクの「空き照会結果」（table.AKITABLE）を読む。

    列ごとに hidden の rimd（YYYYMMDD）があるので、その並び順を列の日付とする。
    セルは 数字=空き面数 / ×=空きなし / －=施設利用不可 / ＝=未公開。
    """
    soup = BeautifulSoup(html, "lxml")
    slots: list[Slot] = []
    for table in soup.select("table.AKITABLE"):
        dates = []
        for inp in table.find_all("input", attrs={"name": "rimd"}):
            v = inp.get("value", "")
            if re.fullmatch(r"\d{8}", v):
                d = date(int(v[:4]), int(v[4:6]), int(v[6:8]))
                if d not in dates:
                    dates.append(d)
        if not dates:
            continue
        for tr in table.find_all("tr"):
            cells = [cell_text(td) for td in tr.find_all(["td", "th"], recursive=False)]
            if len(cells) < 2:
                continue
            label = unicodedata.normalize("NFKC", cells[0])
            if not re.search(r"\d{1,2}:\d{2}", label):
                continue  # 時間帯の行だけ読む
            values = cells[-len(dates):] if len(cells) - 1 >= len(dates) else cells[1:]
            for d, raw in zip(dates, values):
                v = unicodedata.normalize("NFKC", raw).strip()
                if v.isdigit():
                    status = "空き" if int(v) > 0 else "予約済"
                elif v in ("×", "x", "X"):
                    status = "予約済"
                elif v in ("-", "－", "―", "ー", "=", "＝"):
                    status = "利用不可"
                else:
                    continue
                slots.append(Slot(field_id, d.isoformat(), label.replace(" ", ""), status, raw))
    return slots


PARSERS["nagoya_sporec"] = parse_nagoya_sporec
