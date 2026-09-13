from datetime import date

from cadc2ical import Meeting, cadc_id, parse_rss, render_ics


def realistic_rss(guid="https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/9889"):
    return f'''<?xml version="1.0"?><rss><channel><item>
      <title>the Pathfinder HI SCience Community (PHISCC) Workshop 2026</title>
      <link>https://phiscc2026.github.io/</link>
      <description>&lt;TABLE&gt;&lt;TR&gt;&lt;TD&gt;Date&lt;/TD&gt;&lt;TD&gt;Monday 12, October 2026 to Friday 16, October 2026&lt;/TD&gt;&lt;/TR&gt;&lt;TR&gt;&lt;TD&gt;Location&lt;/TD&gt;&lt;TD&gt;KIAA, Beijing, China&lt;/TD&gt;&lt;/TR&gt;&lt;TR&gt;&lt;TD&gt;Web Site&lt;/TD&gt;&lt;TD&gt;&lt;A HREF="https://phiscc2026.github.io/"&gt;website&lt;/A&gt;&lt;/TD&gt;&lt;/TR&gt;&lt;/TABLE&gt;</description>
      <guid>{guid}</guid>
      <pubDate>Thu, 18 Dec 2025 19:32:52 GMT</pubDate>
    </item></channel></rss>'''


def test_real_cadc_rss_shape():
    rows = parse_rss(realistic_rss())
    assert len(rows) == 1
    m = rows[0]
    assert m.number == "9889"
    assert m.start_date == "2026-10-12"
    assert m.end_date == "2026-10-16"
    assert m.location == "KIAA, Beijing, China"
    assert m.website == "https://phiscc2026.github.io/"
    assert m.cadc_url.endswith("/meetings/9889")
    assert m.pub_date == "2025-12-18T19:32:52Z"


def test_external_meetings_year_is_not_a_cadc_id():
    assert cadc_id("https://www.eso.org/sci/meetings/2027/example.html") == ""
    assert cadc_id("https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/9889") == "9889"


def test_rss_uses_guid_not_external_link_as_id():
    rows = parse_rss(realistic_rss())
    assert [m.number for m in rows] == ["9889"]


def test_render_ics_all_day_end_is_exclusive():
    m = Meeting(
        number="7516",
        title="Galaxy, evolution; workshop",
        start_date="2026-09-07",
        end_date="2026-09-11",
        location="Heidelberg, Germany",
        website="https://example.org",
        cadc_url="https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/7516",
        pub_date="2026-01-02T03:04:05Z",
    )
    ics = render_ics([m], date(2026, 9, 1), 730, 30)
    assert "UID:cadc-7516@cadc2ical" in ics
    assert "DTSTAMP:20260102T030405Z" in ics
    assert "DTSTART;VALUE=DATE:20260907" in ics
    assert "DTEND;VALUE=DATE:20260912" in ics
    assert "SUMMARY:Galaxy\\, evolution\\; workshop" in ics
