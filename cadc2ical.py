#!/usr/bin/env python3
"""Mirror the live CADC Astronomy Meetings RSS feed as a subscribable iCalendar.

The CADC RSS feed is the source of truth. Each RSS item contains a stable CADC
meeting number in <guid> and an HTML table with the event dates, location and
website. A small JSON state file makes the feed cumulative when older items
roll off the RSS feed.
"""
from __future__ import annotations

import argparse
import email.utils
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_FEEDS = [
    "https://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/rssFeed",
    "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/meetings/rssFeed",
    "https://cadcwww.dao.nrc.ca/meetings/rssFeed",
]
USER_AGENT = "cadc2ical/2.0 (+https://github.com/damleborgne/cadc2ical)"
CADC_PATH_ID_RE = re.compile(r"/meetings/(\d{3,})/?$")


@dataclass
class Meeting:
    number: str
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    location: str = ""
    website: str = ""
    cadc_url: str = ""
    pub_date: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.number and self.title and self.start_date and self.end_date)

    def start(self) -> date | None:
        return parse_date(self.start_date)

    def end(self) -> date | None:
        return parse_date(self.end_date) or self.start()


def clean(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def parse_date(value: object) -> date | None:
    text = clean(value)
    if not text:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    for fmt in (
        "%A %d, %B %Y",
        "%a %d, %B %Y",
        "%A, %d %B %Y",
        "%a, %d %B %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def cadc_id(url: str) -> str:
    """Return a meeting number only for a CADC meeting URL.

    This deliberately rejects external URLs such as
    https://www.eso.org/sci/meetings/2027/... .
    """
    raw = html.unescape(url or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    host = parsed.netloc.lower()
    if not ("cadc" in host or host.endswith("dao.nrc.ca") or host.endswith("hia-iha.nrc-cnrc.gc.ca")):
        return ""
    m = CADC_PATH_ID_RE.search(parsed.path)
    return m.group(1) if m else ""


def parse_description(description: str) -> tuple[date | None, date | None, str, str]:
    soup = BeautifulSoup(html.unescape(description or ""), "html.parser")
    rows: dict[str, tuple[str, object]] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = clean(cells[0].get_text(" ", strip=True)).lower()
        value = clean(cells[1].get_text(" ", strip=True))
        rows[label] = (value, cells[1])

    start = end = None
    date_text = rows.get("date", ("", None))[0]
    if date_text:
        parts = re.split(r"\s+to\s+", str(date_text), maxsplit=1, flags=re.I)
        if len(parts) == 1:
            parts = re.split(r"\s+[–—-]\s+", str(date_text), maxsplit=1)
        start = parse_date(parts[0])
        end = parse_date(parts[1]) if len(parts) > 1 else start

    location = str(rows.get("location", ("", None))[0])
    website = ""
    for label in ("web site", "website"):
        cell = rows.get(label, ("", None))[1]
        if cell is None:
            continue
        anchor = cell.find("a", href=True)  # type: ignore[union-attr]
        if anchor:
            href = str(anchor.get("href", "")).strip()
            if href.startswith(("https://", "http://")):
                website = href
                break
        value = clean(cell.get_text(" ", strip=True))  # type: ignore[union-attr]
        if value.startswith(("https://", "http://")):
            website = value
            break
    return start, end, location, website


def parse_pubdate(value: str) -> str:
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rss(text: str) -> list[Meeting]:
    root = ET.fromstring(text)
    meetings: dict[str, Meeting] = {}
    for item in root.iter():
        if item.tag.split("}")[-1].lower() not in ("item", "entry"):
            continue
        values: dict[str, str] = {}
        for child in item:
            values[child.tag.split("}")[-1].lower()] = clean(child.text)

        guid = values.get("guid", "")
        number = cadc_id(guid)
        if not number:
            continue
        start, end, location, website = parse_description(values.get("description", ""))
        link = values.get("link", "")
        if not website and link.startswith(("https://", "http://")) and not cadc_id(link):
            website = link
        meetings[number] = Meeting(
            number=number,
            title=values.get("title", ""),
            start_date=start.isoformat() if start else "",
            end_date=end.isoformat() if end else "",
            location=location,
            website=website,
            cadc_url=guid,
            pub_date=parse_pubdate(values.get("pubdate", "")),
        )
    return list(meetings.values())


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"})
    return s


def fetch_feed(s: requests.Session, urls: Iterable[str]) -> tuple[list[Meeting], str]:
    errors: list[str] = []
    for url in urls:
        try:
            response = s.get(url, timeout=20, allow_redirects=True)
            response.raise_for_status()
            meetings = parse_rss(response.text)
            if not meetings:
                raise ValueError("RSS parsed successfully but contained no CADC meeting items")
            return meetings, url
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("all CADC RSS endpoints failed:\n" + "\n".join(errors))


def load_state(path: Path) -> dict[str, Meeting]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("meetings", []) if isinstance(payload, dict) else []
    out: dict[str, Meeting] = {}
    if not isinstance(rows, list):
        return out
    allowed = set(Meeting.__dataclass_fields__)
    for row in rows:
        if not isinstance(row, dict) or not row.get("number"):
            continue
        data = {k: v for k, v in row.items() if k in allowed}
        try:
            m = Meeting(**data)
        except TypeError:
            continue
        out[m.number] = m
    return out


def merge_state(old: dict[str, Meeting], fresh: Iterable[Meeting]) -> dict[str, Meeting]:
    out = dict(old)
    for m in fresh:
        previous = out.get(m.number)
        if previous:
            for field in Meeting.__dataclass_fields__:
                if field == "number":
                    continue
                if not getattr(m, field):
                    setattr(m, field, getattr(previous, field))
        out[m.number] = m
    return out


def save_state(path: Path, meetings: dict[str, Meeting]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "meetings": [asdict(meetings[k]) for k in sorted(meetings, key=int)],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ics_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold_line(line: str, limit: int = 73) -> list[str]:
    if len(line.encode("utf-8")) <= limit:
        return [line]
    result: list[str] = []
    current = ""
    for char in line:
        candidate = current + char
        if current and len(candidate.encode("utf-8")) > limit:
            result.append(current)
            current = " " + char
        else:
            current = candidate
    if current:
        result.append(current)
    return result


def dtstamp(m: Meeting) -> str:
    if m.pub_date:
        try:
            dt = datetime.fromisoformat(m.pub_date.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            pass
    return "19700101T000000Z"


def render_ics(meetings: Iterable[Meeting], today: date, horizon_days: int, grace_days: int) -> str:
    low = today - timedelta(days=grace_days)
    high = today + timedelta(days=horizon_days)
    selected: list[Meeting] = []
    for m in meetings:
        start, end = m.start(), m.end()
        if not (m.complete and start and end):
            continue
        if end < low or start > high:
            continue
        selected.append(m)
    selected.sort(key=lambda m: (m.start() or date.max, m.title.casefold()))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//damleborgne//cadc2ical//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:CADC Astronomy Meetings",
        "X-WR-CALDESC:International astronomy meetings mirrored from the CADC RSS feed",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    for m in selected:
        start, end = m.start(), m.end()
        assert start and end
        description = f"CADC meeting #{m.number}\\n{m.cadc_url}"
        if m.website:
            description += f"\\nMeeting website: {m.website}"
        event = [
            "BEGIN:VEVENT",
            f"UID:cadc-{m.number}@cadc2ical",
            f"DTSTAMP:{dtstamp(m)}",
            f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(end + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(m.title)}",
        ]
        if m.location:
            event.append(f"LOCATION:{ics_escape(m.location)}")
        event.extend(
            [
                f"URL:{ics_escape(m.website or m.cadc_url)}",
                f"DESCRIPTION:{ics_escape(description).replace('\\\\n', '\\n')}",
                f"X-CADC-MEETING-NUMBER:{m.number}",
                "STATUS:CONFIRMED",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )
        lines.extend(event)
    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(fold_line(line))
    return "\r\n".join(folded) + "\r\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("calendar/cadc.ics"))
    parser.add_argument("--state", type=Path, default=Path("data/meetings.json"))
    parser.add_argument("--horizon-days", type=int, default=int(os.getenv("CADC_HORIZON_DAYS", "730")))
    parser.add_argument("--grace-days", type=int, default=30)
    args = parser.parse_args()

    feed_env = os.getenv("CADC_RSS_URLS", "")
    feeds = [u.strip() for u in feed_env.split(",") if u.strip()] or DEFAULT_FEEDS
    try:
        fresh, used_url = fetch_feed(session(), feeds)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    state = merge_state(load_state(args.state), fresh)
    cutoff = date.today() - timedelta(days=90)
    state = {mid: m for mid, m in state.items() if m.end() is None or m.end() >= cutoff}

    complete = sum(1 for m in state.values() if m.complete)
    if complete == 0:
        print("refusing to overwrite calendar: no complete meetings were parsed", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_ics(state.values(), date.today(), args.horizon_days, args.grace_days), encoding="utf-8", newline="")
    save_state(args.state, state)
    print(f"feed={used_url} fresh={len(fresh)} state={len(state)} complete={complete} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
