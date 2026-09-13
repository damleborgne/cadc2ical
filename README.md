# cadc2ical

A small mirror that turns the **CADC International Astronomy Meetings** RSS feed into a fresh, subscribable iCalendar (`.ics`) feed.

The official CADC iCal feed appears stale, while the RSS feed is still live. This project fetches that RSS feed from GitHub Actions, so subscribers do not need direct access to the CADC site or a VPN.

## Calendar URL

Subscribe to:

```text
https://raw.githubusercontent.com/damleborgne/cadc2ical/main/calendar/cadc.ics
```

On iPhone/iPad: **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar**, then paste the URL above.

## How it works

1. Fetches the live CADC Astronomy Meetings RSS feed, trying several official CADC hostnames if needed.
2. Reads the stable CADC meeting number from each RSS `<guid>`.
3. Parses the RSS description table for the real start/end dates, location, and meeting website.
4. Keeps `data/meetings.json` as cumulative state, so meetings do not disappear merely because they roll off the RSS window.
5. Generates stable all-day `VEVENT`s with `UID:cadc-<number>@cadc2ical`.
6. GitHub Actions refreshes the feed every day at 04:17 UTC and commits changes automatically.

The published calendar includes meetings from 30 days in the past through two years in the future by default. Old state entries are pruned after 90 days.

The generator is fail-safe: if all CADC RSS endpoints are unavailable, or if the feed cannot be parsed into any complete meetings, it exits with an error instead of replacing the calendar with an empty file.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python cadc2ical.py
```

Run tests:

```bash
pytest -q
```

Useful options:

```bash
python cadc2ical.py --horizon-days 730 --grace-days 30
```

Environment variables:

- `CADC_RSS_URLS`: comma-separated RSS URLs to try, in priority order.
- `CADC_HORIZON_DAYS`: future calendar horizon in days (default: 730).

## Source

Live CADC RSS feed used by default:

```text
https://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/rssFeed
```

Public CADC meetings UI:

```text
https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/meetings/
```

This project is an independent mirror and is not affiliated with or endorsed by the Canadian Astronomy Data Centre or NRC Canada.
