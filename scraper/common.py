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
                      block_contains: str | None = None, with_block: bool = False) -> list[Slot]:
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
                # 「17時～」「9」「30」のように時刻だけの見出しは時刻表記に置き換える
                plain = re.fullmatch(r"\d{1,2}時?[～~]?", name)
                slot = times if plain else f"{name} {times}"
            else:
                slot = name
            if with_block:
                slot = f"{block_name.lstrip('●').strip()}|{slot}"
            slots.append(Slot(field_id, d.isoformat(), slot, status, raw))
    return slots


def require_all_blocks(slots: list[dict], blocks: int) -> list[dict]:
    """複数面の同時使用が条件の球場向け。全面が空いている時間帯だけを「空き」にする。

    slot は「面の名前|時間帯」の形で渡す。同じ日・時間帯の面がそろっていなければ
    （表示されない面がある＝読めていない）空きとは扱わない。
    """
    groups: dict[tuple, list[dict]] = {}
    for s in slots:
        block, _, time = s["slot"].partition("|")
        groups.setdefault((s["date"], time or block), []).append(s)
    out = []
    for (d, time), items in sorted(groups.items()):
        statuses = [i["status"] for i in items]
        if len(items) >= blocks and all(st == "空き" for st in statuses):
            status = "空き"
        elif all(st == "利用不可" for st in statuses):
            status = "利用不可"
        elif any(st == "抽選" for st in statuses):
            status = "抽選"
        else:
            status = "予約済"
        free = sum(st == "空き" for st in statuses)
        out.append({"field": items[0]["field"], "date": d, "slot": f"{blocks}面 {time}",
                    "status": status, "raw": f"{free}/{len(items)}面空き"})
    return out


_RANGE = re.compile(r"^(?P<pre>.*?)(?P<s>\d{1,2}:\d{2})-(?P<e>\d{1,2}:\d{2})$")


def merge_adjacent(slots: list[dict]) -> list[dict]:
    """30分・1時間刻みの枠を、同じ状態が続く範囲にまとめる（例: 9:00-12:00 空き）。

    時刻の前に面の名前などが付いている場合は、同じ名前どうしでまとめる。
    時刻の形でない枠はそのまま残す。
    """
    def minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    out, groups = [], {}
    for s in slots:
        m = _RANGE.match(s["slot"])
        if not m:
            out.append(s)
            continue
        groups.setdefault((s["field"], s["date"], m["pre"]), []).append((minutes(m["s"]), minutes(m["e"]), s))
    for (field, d, pre), items in groups.items():
        items.sort(key=lambda x: x[0])
        cur = None
        for st, en, s in items:
            if cur and cur["status"] == s["status"] and cur["_end"] == st:
                cur["_end"] = en
            else:
                if cur:
                    out.append(cur)
                cur = {**s, "_start": st, "_end": en}
        if cur:
            out.append(cur)
    for s in out:
        if "_start" in s:
            st, en = s.pop("_start"), s.pop("_end")
            pre = _RANGE.match(s["slot"])["pre"]
            s["slot"] = f"{pre}{st // 60:02d}:{st % 60:02d}-{en // 60:02d}:{en % 60:02d}"
    return sorted(out, key=lambda s: (s["date"], s["slot"]))


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


def parse_hokusei(html: str, field_id: str, today: date, row_label: str = "野球場",
                  max_hour: int = 17) -> list[Slot]:
    """北勢中央公園「予約状況の確認」の1日表示を読む。

    施設ごとの行に 6〜22時の1時間ごとのセルが並び、予約があるセルだけ背景色が付く。
    連続する同じ状態の時間をまとめて1枠にする（例: 6:00-8:00 空き）。
    夜間（照明使用）は軟式のみのため、既定では17時までを対象にする。
    """
    soup = BeautifulSoup(html, "lxml")
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", soup.get_text(" "))
    th = soup.find("th", class_="calendar_title")
    if not m or not th:
        return []
    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    table = th.find_parent("table")
    rows = table.find_all("tr")
    hours = [int(c.get_text(strip=True)) for c in rows[1].find_all(["th", "td"]) if c.get_text(strip=True).isdigit()]
    target = next((tr for tr in rows if tr.find("td") and tr.find("td").get_text(strip=True) == row_label), None)
    if not target or not hours:
        return []
    cells = target.find_all("td")[1:]
    states = []
    for h, td in zip(hours, cells):
        if h >= max_hour:
            break
        booked = "background-color" in (td.get("style") or "")
        states.append((h, "予約済" if booked else "空き"))
    slots: list[Slot] = []
    start = 0
    for i in range(1, len(states) + 1):
        if i == len(states) or states[i][1] != states[start][1]:
            h0, st = states[start]
            h1 = states[i - 1][0] + 1
            slots.append(Slot(field_id, d.isoformat(), f"{h0:02d}:00-{h1:02d}:00", st,
                              "予約あり" if st == "予約済" else "予約なし"))
            start = i
    return slots


PARSERS["hokusei"] = parse_hokusei


EHARP_STATUS = {
    "利用可能": "空き", "空き状況のみ": "空き", "電話受付": "空き", "窓口受付": "空き",
    "空きなし": "予約済",
    "抽選申込可": "抽選", "抽選待ち": "抽選",
}


def parse_eharp(html: str, field_id: str, today: date) -> list[Slot]:
    """e-harp（四日市市など）の空き状況カレンダーを読む。

    日ごとのボタンに datetime と「10月17日土曜日 空きなし」のような aria-label がある。
    時間帯ごとの状況は日付を開かないと出ないため、ここでは日単位の目安として扱う。
    """
    soup = BeautifulSoup(html, "lxml")
    slots: list[Slot] = []
    for btn in soup.select("button.AvailabilityTable_item"):
        t = btn.find("time")
        label = (btn.get("aria-label") or "").strip()
        if not t or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", t.get("datetime", "")) or not label:
            continue
        word = label.split()[-1]
        status = EHARP_STATUS.get(word, "利用不可")
        slots.append(Slot(field_id, t["datetime"], "終日（日単位の目安）", status, word))
    return slots


PARSERS["eharp"] = parse_eharp


PF489_STATUS = {"○": "空き", "△": "一部空き", "×": "予約済"}


def parse_pf489(html: str, field_id: str, today: date, block_contains: str | None = None) -> list[Slot]:
    """pf489 系（小牧・多治見・一宮）の「施設別空き状況」を読む。

    施設ごとの行に日付ごとのセルがあり、セル内の checkbox の value 先頭8桁が日付、
    label が記号（○空き / △一部空き / ×空きなし / －申込期間外 / ＊公開対象外）。
    """
    soup = BeautifulSoup(html, "lxml")
    slots: list[Slot] = []
    for tr in soup.find_all("tr"):
        name_td = tr.find("td", class_="shisetsu")
        if not name_td:
            continue
        name = unicodedata.normalize("NFKC", name_td.get_text(" ", strip=True))
        if block_contains and unicodedata.normalize("NFKC", block_contains) not in name:
            continue
        for td in tr.find_all("td"):
            cb = td.find("input", attrs={"name": "checkdate"})
            lab = td.find("label")
            if not cb or not lab:
                continue
            v = (cb.get("value") or "")[:8]
            if not re.fullmatch(r"\d{8}", v):
                continue
            mark = lab.get_text(strip=True)
            status = PF489_STATUS.get(mark, "利用不可")
            d = date(int(v[:4]), int(v[4:6]), int(v[6:8]))
            slots.append(Slot(field_id, d.isoformat(), "終日（日単位の目安）", status, mark))
    return slots


PARSERS["pf489"] = parse_pf489


def parse_kani(html: str, field_id: str, today: date, block_contains: str | None = None) -> list[Slot]:
    """可児市施設予約Webサイトの1日表示を読む。

    施設行（tr.ScheduleFacilityRow）に時間のセルが colspan 付きで並ぶ。空いている枠には
    予約ボタン（input.ScheduleCellButtonLink）があり、予約済などは色付きの帯になる。
    見出しの時刻と colspan の合計から1単位の分数を求め、時刻に直す。
    """
    soup = BeautifulSoup(html, "lxml")
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", soup.get_text(" "))
    if not m:
        return []
    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    hours = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"], recursive=False)]
        if cells and cells[0] == "時間":
            hours = [int(c) for c in cells[1:] if c.isdigit()]
            break
    if not hours:
        return []
    slots: list[Slot] = []
    for tr in soup.select("tr.ScheduleFacilityRow"):
        first = tr.find("td")
        name = first.get_text(strip=True) if first else ""
        if block_contains and block_contains not in name:
            continue
        cells = tr.find_all("td", class_="ScheduleCell")
        total = sum(int(c.get("colspan", 1) or 1) for c in cells)
        if not total:
            continue
        unit = 60 * len(hours) / total  # 1単位あたりの分
        t = hours[0] * 60
        for c in cells:
            span = int(c.get("colspan", 1) or 1)
            end = t + span * unit
            free = c.find("input", class_="ScheduleCellButtonLink") is not None
            st, en = int(t), int(end)
            slots.append(Slot(field_id, d.isoformat(), f"{st // 60:02d}:{st % 60:02d}-{en // 60:02d}:{en % 60:02d}",
                              "空き" if free else "予約済", c.get("title", "") or ("空き" if free else "")))
            t = end
        break
    return slots


PARSERS["kani"] = parse_kani
