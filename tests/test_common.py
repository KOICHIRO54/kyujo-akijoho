from datetime import date

from scraper.common import infer_date, normalize_status, parse_tables

TODAY = date(2026, 10, 13)


def test_infer_date_rolls_over_year():
    assert infer_date("1/5(火)", TODAY) == date(2027, 1, 5)
    assert infer_date("10月18日(日)", TODAY) == date(2026, 10, 18)
    assert infer_date("2026/11/03", TODAY) == date(2026, 11, 3)
    assert infer_date("午前", TODAY) is None


def test_normalize_status():
    assert normalize_status("○") == "空き"
    assert normalize_status(" × ") == "予約済"
    assert normalize_status("休館") == "利用不可"
    assert normalize_status("抽選") == "抽選"
    assert normalize_status("市民球場") is None


def test_dates_in_header_with_images():
    html = """
    <table>
      <tr><th>時間帯</th><th>10/17(土)</th><th>10/18(日)</th></tr>
      <tr><td>午前</td><td><img alt="空き"></td><td>×</td></tr>
      <tr><td>午後</td><td>○</td><td>△</td></tr>
    </table>"""
    slots = {(s.date, s.slot): s.status for s in parse_tables(html, "x", TODAY)}
    assert slots == {
        ("2026-10-17", "午前"): "空き",
        ("2026-10-18", "午前"): "予約済",
        ("2026-10-17", "午後"): "空き",
        ("2026-10-18", "午後"): "一部空き",
    }


def test_dates_in_first_column_with_rowspan():
    html = """
    <table>
      <tr><th>日付</th><th>9:00-11:00</th><th>11:00-13:00</th></tr>
      <tr><td>10月17日</td><td>○</td><td>○</td></tr>
      <tr><td>10月18日</td><td colspan="2">休場</td></tr>
    </table>"""
    slots = {(s.date, s.slot): s.status for s in parse_tables(html, "x", TODAY)}
    assert slots[("2026-10-17", "9:00-11:00")] == "空き"
    assert slots[("2026-10-18", "11:00-13:00")] == "利用不可"
    assert len(slots) == 4


def test_ignores_layout_tables():
    html = "<table><tr><td>お知らせ</td><td>10/17 メンテナンス</td></tr></table>"
    assert parse_tables(html, "x", TODAY) == []


def test_aichi_kyodo_day_view():
    from scraper.common import parse_aichi_kyodo
    html = """<table><tbody>
    <tr><td class="time-table1"><div>午前</div></td><td class="time-table1"><div>午後</div></td><td class="time-table1"><div>夕方</div></td></tr>
    <tr>
     <td class="time-table2"><div><img alt="空き"><input type="hidden" name="selectInfo" value="A7085:A7085005:20261017:00010:0850:1120:A7085:1:0"></div></td>
     <td class="time-table2"><div><img alt="予約"><input type="hidden" name="selectInfo" value="A7085:A7085005:20261017:00030:1410:1640:A7085:1:0"></div></td>
     <td class="time-table2"><div><img alt="時間帯なし"><input type="hidden" name="selectInfo" value="A7085:A7085005:20261017:00040:1700:1830:A7085:1:0"></div></td>
    </tr></tbody></table>"""
    got = [(s.date, s.slot, s.status) for s in parse_aichi_kyodo(html, "kasugai", TODAY)]
    assert got == [
        ("2026-10-17", "午前 08:50-11:20", "空き"),
        ("2026-10-17", "午後 14:10-16:40", "予約済"),
        ("2026-10-17", "夕方 17:00-18:30", "利用不可"),
    ]


def test_nagoya_sporec_result():
    from scraper.common import parse_nagoya_sporec
    html = """<table class="AKITABLE">
    <input type="hidden" name="rimd" value="20261017"><input type="hidden" name="rimd" value="20261018">
    <input type="hidden" name="rimd" value="20261103">
    <tr><td>10月</td><td>17</td><td>18</td><td>11/3</td></tr>
    <tr><td>１７：３０－１９：３０（夜間１）</td><td>×</td><td>１</td><td>－</td></tr></table>"""
    got = [(s.date, s.status) for s in parse_nagoya_sporec(html, "mizuho", TODAY)]
    assert got == [("2026-10-17", "予約済"), ("2026-10-18", "空き"), ("2026-11-03", "利用不可")]
