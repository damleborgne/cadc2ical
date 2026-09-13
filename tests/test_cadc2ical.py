from datetime import date

from cadc2ical import Meeting, extract_html, parse_response, render_ics


def test_json_response():
    payload = '''{"meetings":[{"number":7516,"title":"Building Galaxies from Scratch","startDate":"2026-09-07","endDate":"2026-09-11","location":"Heidelberg, Germany","url":"https://example.org/bugs2026"}]}'''
    rows = parse_response(payload, "application/json", "https://cadc.example/meetings?new=30")
    assert len(rows) == 1
    m = rows[0]
    assert m.number == "7516"
    assert m.start_date == "2026-09-07"
    assert m.end_date == "2026-09-11"


def test_rss_extracts_meeting_id():
    rss = '''<?xml version="1.0"?><rss><channel><item><title>Galaxy meeting</title><link>https://www.cadc.example/en/meetings/getMeetings.html?number=9880</link></item></channel></rss>'''
    rows = parse_response(rss, "application/rss+xml")
    assert rows[0].number == "9880"
    assert rows[0].title == "Galaxy meeting"


def test_html_detail_parser():
    page = '''
    <html><body><h1>View meeting</h1><h3>Building Galaxies from Scratch: Advances and Challenges</h3>
    <p>Monday, 7 September 2026 - Friday, 11 September 2026</p>
    <p>University of Heidelberg, Heidelberg, Germany</p>
    <a href="https://example.org/meeting">website</a>
    <dl><dt>Keywords</dt><dd>galaxy evolution</dd><dt>Number</dt><dd>7516</dd></dl>
    </body></html>'''
    rows = extract_html(page, "https://www.cadc.example/en/meetings/getMeetings.html?number=7516")
    m = next(x for x in rows if x.number == "7516")
    assert m.start_date == "2026-09-07"
    assert m.end_date == "2026-09-11"
    assert "Heidelberg" in m.location
    assert m.website == "https://example.org/meeting"


def test_render_ics_all_day_end_is_exclusive():
    m = Meeting(
        number="7516",
        title="Galaxy, evolution; workshop",
        start_date="2026-09-07",
        end_date="2026-09-11",
        location="Heidelberg, Germany",
        website="https://example.org",
    )
    ics = render_ics([m], date(2026, 9, 1), 730, 30)
    assert "UID:cadc-7516@cadc2ical" in ics
    assert "DTSTART;VALUE=DATE:20260907" in ics
    assert "DTEND;VALUE=DATE:20260912" in ics
    assert "SUMMARY:Galaxy\\, evolution\\; workshop" in ics
