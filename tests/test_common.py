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
