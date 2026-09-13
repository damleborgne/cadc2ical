# cadc2ical

A small mirror that turns the **CADC International Astronomy Meetings** database into a fresh, subscribable iCalendar (`.ics`) feed.

The official CADC iCal feed appears stale, while the meetings database and RSS feed are still active. This project uses the **documented CADC Meetings REST service** as its primary source, with RSS and the public HTML meeting pages as fallbacks.

## Calendar URL

Once this repository is running on GitHub Actions, subscribe to:

```text
https://raw.githubusercontent.com/damleborgne/cadc2ical/main/calendar/cadc.ics
```

On iPhone/iPad: **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar**, then paste the URL above.

## How it works

1. Queries the CADC Meetings REST service (`/meetings?new=…`, `?month=this`, `?month=next`).
2. Uses the CADC RSS feed as an additional discovery source.
3. Uses `/meetings/<number>` and, if necessary, the public HTML detail page to fill missing metadata.
4. Keeps a small `data/meetings.json` state file so meetings discovered on earlier runs are not lost when they disappear from the RSS window.
5. Generates stable all-day `VEVENT`s with `UID:cadc-<number>@cadc2ical`.
6. GitHub Actions refreshes the mirror daily and commits changes automatically.

The generated feed keeps meetings from 30 days in the past through two years in the future by default.

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
python cadc2ical.py --lookback-days 730 --horizon-days 730 --refresh-days 7
```

Environment variables:

- `CADC_BASE_URLS`: comma-separated CADC hostnames to try, in priority order.
- `CADC_LOOKBACK_DAYS`: discovery window for `?new=<days>` (default: 730).
- `CADC_HORIZON_DAYS`: future calendar horizon (default: 730).
- `CADC_BOOTSTRAP_ICS`: optional URL of the old CADC iCal feed, used only to discover meeting IDs.

## Source

CADC Meetings service documentation:

```text
https://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/
```

Public meetings UI:

```text
https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/meetings/
```

This project is an independent mirror and is not affiliated with or endorsed by the Canadian Astronomy Data Centre or NRC Canada.
