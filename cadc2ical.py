#!/usr/bin/env python3
"""Mirror the CADC International Astronomy Meetings list as an iCalendar feed.

Primary source: the documented CADC Meetings REST service.
Fallbacks: CADC RSS feed and public HTML meeting pages.
"""
from __future__ import annotations

import argparse
import email.utils
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BASES = [
    "https://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca",
    "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca",
    "https://www3.cadc-ccda.hia-iha.nrc-cnrc.gc.ca",
    "https://cadcwww.dao.nrc.ca",
]
DEFAULT_RSS_PATH = "/meetings/rssFeed"
DEFAULT_BOOTSTRAP_ICS = "https://ws-cadc.canfar.net/files/vault/dbohlender/CADC/astroMeetings.ics"
USER_AGENT = "cadc2ical/1.0 (+https://github.com/damleborgne/cadc2ical)"
ID_RE = re.compile(r"(?:number=|/meetings/)(\d{3,})\b", re.I)
DATE_RANGE_RE = re.compile(
    r"(?P<start>(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+\d{1,2}\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})"
    r"\s*[-–—]\s*"
    r"(?P<end>(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+\d{1,2}\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",
    re.I,
)


@dataclass
class Meeting:
    number: str
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    location: str = ""
    website: str = ""
    cadc_url: str = ""
    keywords: str = ""
    last_checked: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.number and self.title and self.start_date and self.end_date)

    def start(self) -> date | None:
        return parse_date(self.start_date)

    def end(self) -> date | None:
        return parse_date(self.end_date) or self.start()


FIELD_NAMES = {f.name for f in fields(Meeting)}


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
        }
    )
    return s


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def parse_date(value: Any) -> date | None:
    s = clean_text(value)
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    formats = [
        "%A, %d %B %Y",
        "%a, %d %B %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt:
            return dt.date()
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def meeting_id_from_text(text: str) -> str:
    m = ID_RE.search(text or "")
    return m.group(1) if m else ""


def pick(mapping: dict[str, Any], aliases: Iterable[str]) -> Any:
    normalized = {norm_key(k): v for k, v in mapping.items()}
    for alias in aliases:
        key = norm_key(alias)
        if key in normalized and normalized[key] not in (None, "", []):
            return normalized[key]
    return None


def date_pair_from_mapping(d: dict[str, Any]) -> tuple[date | None, date | None]:
    start = parse_date(
        pick(d, ["start_date", "startDate", "date_start", "dateStart", "start", "from", "beginDate"])
    )
    end = parse_date(
        pick(d, ["end_date", "endDate", "date_end", "dateEnd", "end", "to", "finishDate"])
    )
    if start and not end:
        end = start
    if not start:
        combined = clean_text(pick(d, ["dates", "date", "when", "meetingDate", "dateRange"]))
        m = DATE_RANGE_RE.search(combined)
        if m:
            start = parse_date(m.group("start"))
            end = parse_date(m.group("end"))
        else:
            one = parse_date(combined)
            if one:
                start = end = one
    return start, end


def coerce_meeting(d: dict[str, Any], fallback_id: str = "") -> Meeting | None:
    number = clean_text(pick(d, ["number", "meeting_number", "meetingNo", "meeting_no", "id"])) or fallback_id
    if not number:
        for key in ("url", "link", "href", "cadc_url"):
            number = meeting_id_from_text(clean_text(pick(d, [key])))
            if number:
                break
    if number and not number.isdigit():
        m = re.search(r"\d{3,}", number)
        number = m.group(0) if m else ""
    if not number:
        return None

    start, end = date_pair_from_mapping(d)
    title = clean_text(pick(d, ["title", "meeting_title", "meetingTitle", "name", "eventName"]))
    location = clean_text(pick(d, ["location", "place", "venue", "city", "meetingLocation"]))
    website = clean_text(pick(d, ["website", "webpage", "event_url", "eventUrl", "url", "link"]))
    keywords = clean_text(pick(d, ["keywords", "keyword", "description", "summary"]))
    cadc_url = clean_text(pick(d, ["cadc_url", "source", "source_url"]))
    if website and "cadc-ccda.hia-iha.nrc-cnrc.gc.ca" in website:
        cadc_url, website = website, ""

    return Meeting(
        number=number,
        title=title,
        start_date=start.isoformat() if start else "",
        end_date=end.isoformat() if end else "",
        location=location,
        website=website,
        cadc_url=cadc_url,
        keywords=keywords,
    )


def merge_meeting(old: Meeting | None, new: Meeting | None) -> Meeting | None:
    if old is None:
        return new
    if new is None:
        return old
    if old.number != new.number:
        raise ValueError("cannot merge different meeting IDs")
    result = Meeting(number=old.number)
    for name in FIELD_NAMES - {"number"}:
        nv = getattr(new, name)
        ov = getattr(old, name)
        setattr(result, name, nv or ov)
    return result


def extract_meetings_from_obj(obj: Any) -> list[Meeting]:
    out: dict[str, Meeting] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            m = coerce_meeting(node)
            if m and (m.title or m.start_date or any("meeting" in norm_key(k) for k in node)):
                out[m.number] = merge_meeting(out.get(m.number), m)  # type: ignore[arg-type]
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return list(out.values())


def element_to_obj(el: ET.Element) -> Any:
    children = list(el)
    if not children:
        return clean_text(el.text)
    result: dict[str, Any] = {}
    for child in children:
        key = child.tag.split("}")[-1]
        val = element_to_obj(child)
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(val)
        else:
            result[key] = val
    return result


def extract_html(text: str, source_url: str = "") -> list[Meeting]:
    soup = BeautifulSoup(text, "html.parser")
    meetings: dict[str, Meeting] = {}

    for a in soup.find_all("a", href=True):
        href = urljoin(source_url, a.get("href", ""))
        mid = meeting_id_from_text(href)
        if mid:
            m = Meeting(number=mid, title=clean_text(a.get_text(" ", strip=True)), cadc_url=href)
            meetings[mid] = merge_meeting(meetings.get(mid), m)  # type: ignore[arg-type]

    title = ""
    h1 = soup.find(lambda tag: tag.name in ("h1", "h2") and "meeting" in tag.get_text(" ", strip=True).lower())
    if h1:
        h3 = h1.find_next("h3")
        if h3:
            title = clean_text(h3.get_text(" ", strip=True))
    if not title:
        h3 = soup.find("h3")
        if h3:
            title = clean_text(h3.get_text(" ", strip=True))

    visible = [clean_text(s) for s in soup.stripped_strings]
    joined = "\n".join(x for x in visible if x)
    range_match = DATE_RANGE_RE.search(joined)
    mid = meeting_id_from_text(source_url)
    if not mid:
        mnum = re.search(r"\bNumber\s+(\d{3,})\b", joined, re.I)
        if mnum:
            mid = mnum.group(1)

    if mid and title and range_match:
        start = parse_date(range_match.group("start"))
        end = parse_date(range_match.group("end"))
        location = ""
        start_idx = None
        for i, line in enumerate(visible):
            if range_match.group("start").lower() in line.lower() or (
                start and str(start.year) in line and start.strftime("%B").lower() in line.lower()
            ):
                start_idx = i
                break
        if start_idx is not None:
            for line in visible[start_idx + 1 : start_idx + 8]:
                if not line or line in ("-", "–", "—"):
                    continue
                if re.match(r"^(Name|Address|Telephone|Email|Keywords|Number)$", line, re.I):
                    break
                if not DATE_RANGE_RE.search(line):
                    location = line
                    break

        website = ""
        for a in soup.find_all("a", href=True):
            href = urljoin(source_url, a.get("href", ""))
            host = urlparse(href).netloc.lower()
            if href.startswith("http") and "cadc" not in host and "canada.ca" not in host and "nrc-cnrc.gc.ca" not in host:
                website = href
                break

        keywords = ""
        for i, line in enumerate(visible):
            if line.lower() == "keywords" and i + 1 < len(visible):
                nxt = visible[i + 1]
                if nxt.lower() != "number":
                    keywords = nxt
                break
        detail = Meeting(
            number=mid,
            title=title,
            start_date=start.isoformat() if start else "",
            end_date=end.isoformat() if end else "",
            location=location,
            website=website,
            cadc_url=source_url,
            keywords=keywords,
        )
        meetings[mid] = merge_meeting(meetings.get(mid), detail)  # type: ignore[arg-type]

    return list(meetings.values())


def parse_response(text: str, content_type: str = "", source_url: str = "") -> list[Meeting]:
    ctype = (content_type or "").lower()
    stripped = text.lstrip()

    if "json" in ctype or stripped.startswith(("{", "[")):
        try:
            return extract_meetings_from_obj(json.loads(text))
        except json.JSONDecodeError:
            pass

    if "xml" in ctype or stripped.startswith("<?xml") or stripped.startswith("<rss") or stripped.startswith("<feed"):
        try:
            root = ET.fromstring(text)
            meetings: dict[str, Meeting] = {}
            for item in root.iter():
                tag = item.tag.split("}")[-1].lower()
                if tag not in ("item", "entry"):
                    continue
                blob = ET.tostring(item, encoding="unicode")
                ids = set(ID_RE.findall(blob))
                title = ""
                for child in item:
                    if child.tag.split("}")[-1].lower() == "title":
                        title = clean_text(child.text)
                        break
                for mid in ids:
                    meetings[mid] = merge_meeting(meetings.get(mid), Meeting(number=mid, title=title))  # type: ignore[arg-type]
            for m in extract_meetings_from_obj(element_to_obj(root)):
                meetings[m.number] = merge_meeting(meetings.get(m.number), m)  # type: ignore[arg-type]
            if meetings:
                return list(meetings.values())
        except ET.ParseError:
            pass

    return extract_html(text, source_url)


def get_url(session: requests.Session, url: str, timeout: int = 25) -> requests.Response:
    r = session.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r


def fetch_first(session: requests.Session, urls: Iterable[str]) -> tuple[requests.Response, str]:
    errors = []
    for url in urls:
        try:
            return get_url(session, url), url
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("all CADC endpoints failed:\n" + "\n".join(errors))


def discover(session: requests.Session, bases: list[str], lookback_days: int) -> dict[str, Meeting]:
    discovered: dict[str, Meeting] = {}
    queries = [f"?new={lookback_days}", "?month=this", "?month=next"]

    for query in queries:
        urls = [b.rstrip("/") + "/meetings" + query for b in bases]
        try:
            r, used = fetch_first(session, urls)
            for m in parse_response(r.text, r.headers.get("content-type", ""), used):
                if not m.cadc_url:
                    m.cadc_url = used.split("?")[0].rstrip("/") + "/" + m.number
                discovered[m.number] = merge_meeting(discovered.get(m.number), m)  # type: ignore[arg-type]
        except RuntimeError as exc:
            print(f"warning: REST discovery failed for {query}: {exc}", file=sys.stderr)

    rss_urls = [b.rstrip("/") + DEFAULT_RSS_PATH for b in bases]
    try:
        r, used = fetch_first(session, rss_urls)
        for m in parse_response(r.text, r.headers.get("content-type", ""), used):
            discovered[m.number] = merge_meeting(discovered.get(m.number), m)  # type: ignore[arg-type]
    except RuntimeError as exc:
        print(f"warning: RSS discovery failed: {exc}", file=sys.stderr)

    return discovered


def bootstrap_ids_from_ics(session: requests.Session, url: str) -> set[str]:
    if not url:
        return set()
    try:
        r = get_url(session, url)
        return set(ID_RE.findall(r.text))
    except requests.RequestException as exc:
        print(f"warning: bootstrap iCal unavailable: {exc}", file=sys.stderr)
        return set()


def fetch_detail(session: requests.Session, mid: str, bases: list[str]) -> Meeting | None:
    rest_urls = [b.rstrip("/") + f"/meetings/{mid}" for b in bases]
    for url in rest_urls:
        try:
            r = get_url(session, url)
            meetings = parse_response(r.text, r.headers.get("content-type", ""), url)
            for m in meetings:
                if m.number == mid:
                    if not m.cadc_url:
                        m.cadc_url = url
                    return m
        except requests.RequestException:
            continue

    html_urls = [b.rstrip("/") + f"/en/meetings/getMeetings.html?number={mid}" for b in bases]
    for url in html_urls:
        try:
            r = get_url(session, url)
            for m in extract_html(r.text, url):
                if m.number == mid:
                    return m
        except requests.RequestException:
            continue
    return None


def load_state(path: Path) -> dict[str, Meeting]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    rows = raw.get("meetings", raw) if isinstance(raw, dict) else raw
    out: dict[str, Meeting] = {}
    if isinstance(rows, dict):
        iterable = rows.values()
    elif isinstance(rows, list):
        iterable = rows
    else:
        return {}
    for row in iterable:
        if not isinstance(row, dict) or not row.get("number"):
            continue
        payload = {k: v for k, v in row.items() if k in FIELD_NAMES}
        m = Meeting(**payload)
        out[m.number] = m
    return out


def save_state(path: Path, meetings: dict[str, Meeting]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "meetings": [asdict(meetings[k]) for k in sorted(meetings, key=lambda x: int(x))],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def should_refresh(m: Meeting, refresh_days: int) -> bool:
    if not m.complete:
        return True
    if not m.last_checked:
        return True
    try:
        checked = datetime.fromisoformat(m.last_checked.replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - checked >= timedelta(days=refresh_days)


def ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold_ics_line(line: str, limit: int = 73) -> list[str]:
    if len(line.encode("utf-8")) <= limit:
        return [line]
    parts: list[str] = []
    current = ""
    for ch in line:
        candidate = current + ch
        if len(candidate.encode("utf-8")) > limit and current:
            parts.append(current)
            current = " " + ch
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def render_ics(meetings: Iterable[Meeting], today: date, horizon_days: int, grace_days: int) -> str:
    eligible: list[Meeting] = []
    low = today - timedelta(days=grace_days)
    high = today + timedelta(days=horizon_days)
    for m in meetings:
        start, end = m.start(), m.end()
        if not (m.complete and start and end):
            continue
        if end < low or start > high:
            continue
        eligible.append(m)
    eligible.sort(key=lambda m: (m.start() or date.max, m.title.lower()))

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//damleborgne//cadc2ical//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:CADC Astronomy Meetings",
        "X-WR-CALDESC:International astronomy meetings mirrored from the Canadian Astronomy Data Centre",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    for m in eligible:
        start, end = m.start(), m.end()
        assert start and end
        dtend = end + timedelta(days=1)
        cadc_url = m.cadc_url or f"https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/meetings/getMeetings.html?number={m.number}"
        description_bits = [f"CADC meeting #{m.number}", cadc_url]
        if m.keywords:
            description_bits.append(f"Keywords: {m.keywords}")
        if m.website:
            description_bits.append(f"Meeting website: {m.website}")
        event_lines = [
            "BEGIN:VEVENT",
            f"UID:cadc-{m.number}@cadc2ical",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{dtend.strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(m.title)}",
        ]
        if m.location:
            event_lines.append(f"LOCATION:{ics_escape(m.location)}")
        event_lines.extend(
            [
                f"URL:{ics_escape(m.website or cadc_url)}",
                f"DESCRIPTION:{ics_escape(chr(10).join(description_bits))}",
                f"X-CADC-MEETING-NUMBER:{m.number}",
                "STATUS:CONFIRMED",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )
        lines.extend(event_lines)
    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(fold_ics_line(line))
    return "\r\n".join(folded) + "\r\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=Path("calendar/cadc.ics"))
    ap.add_argument("--state", type=Path, default=Path("data/meetings.json"))
    ap.add_argument("--lookback-days", type=int, default=int(os.getenv("CADC_LOOKBACK_DAYS", "730")))
    ap.add_argument("--horizon-days", type=int, default=int(os.getenv("CADC_HORIZON_DAYS", "730")))
    ap.add_argument("--grace-days", type=int, default=30)
    ap.add_argument("--refresh-days", type=int, default=7)
    ap.add_argument("--max-detail-fetches", type=int, default=350)
    ap.add_argument("--no-bootstrap-ics", action="store_true")
    args = ap.parse_args()

    bases = [x.strip().rstrip("/") for x in os.getenv("CADC_BASE_URLS", ",".join(DEFAULT_BASES)).split(",") if x.strip()]
    session = build_session()
    state = load_state(args.state)
    discovered = discover(session, bases, args.lookback_days)

    for mid, m in discovered.items():
        state[mid] = merge_meeting(state.get(mid), m)  # type: ignore[assignment]

    if not args.no_bootstrap_ics:
        for mid in bootstrap_ids_from_ics(session, os.getenv("CADC_BOOTSTRAP_ICS", DEFAULT_BOOTSTRAP_ICS)):
            state.setdefault(mid, Meeting(number=mid))

    today = date.today()
    candidates: list[Meeting] = []
    for m in state.values():
        end = m.end()
        if end is None or end >= today - timedelta(days=args.grace_days):
            if should_refresh(m, args.refresh_days):
                candidates.append(m)
    candidates.sort(key=lambda m: (0 if not m.complete else 1, int(m.number)))

    fetched = 0
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for m in candidates:
        if fetched >= args.max_detail_fetches:
            print(f"warning: detail fetch cap reached ({args.max_detail_fetches}); remaining entries will refresh next run", file=sys.stderr)
            break
        detail = fetch_detail(session, m.number, bases)
        fetched += 1
        if detail:
            detail.last_checked = now_iso
            state[m.number] = merge_meeting(state.get(m.number), detail)  # type: ignore[assignment]
        else:
            state[m.number].last_checked = now_iso
        time.sleep(0.05)

    pruned: dict[str, Meeting] = {}
    cutoff = today - timedelta(days=max(args.grace_days, 90))
    for mid, m in state.items():
        end = m.end()
        if end and end < cutoff:
            continue
        pruned[mid] = m
    state = pruned

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_ics(state.values(), today, args.horizon_days, args.grace_days), encoding="utf-8", newline="")
    save_state(args.state, state)

    complete = sum(1 for m in state.values() if m.complete)
    print(f"discovered={len(discovered)} state={len(state)} complete={complete} detail_fetches={fetched} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
