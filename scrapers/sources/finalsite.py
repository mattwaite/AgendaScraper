"""Reading the calendar module on Finalsite-hosted district sites.

Finalsite renders a calendar as a numbered page element. The whole page is
heavy, but the element alone answers on its own URL and is a quarter the size:

    https://<district>/fs/elements/<element id>?cal_date=YYYY-MM-DD

That returns one month of server-rendered HTML -- no JavaScript, no bot
challenge -- so a range is read a month at a time. There is no JSON or
iCalendar feed; `/ical`, `/rss` and the `/fs/api/` paths all 404.

Each event carries more than the visible text suggests:

    <a class="fsCalendarEventLink"
       data-occur-id="1302_2026-09-10T23:00:00Z_2026-09-11T00:00:00Z"
       title="Board Meeting">Board Meeting</a>
    <time class="fsStartTime" datetime="2026-09-10T18:00:00-05:00">...</time>
    <div class="fsLocation">TAC Building - ..., 3215 Cuming St, Omaha, NE</div>

The `datetime` attribute is a real ISO timestamp whose offset tracks daylight
saving properly -- -05:00 in October, -06:00 in November -- so it is converted
rather than rebuilt from the displayed hour.

Two shapes to know about.

A month view includes the last days of the previous month and the first of the
next, so an event near a boundary comes back from two different fetches.
`occur_id` is unique per occurrence and is what to deduplicate on. It also
means a range must be fetched one month wider at each end than the window it
covers, or an event just inside the edge is only present in a month nobody
asked for.

An all-day event has no `<time>` element at all. `starts_at` is None for those,
the same distinction the iCalendar and Thrillshare readers draw, so that a
made-up hour never reaches an editor.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

from ..meeting import CENTRAL

log = logging.getLogger(__name__)

# "1302_2026-09-10T23:00:00Z_2026-09-11T00:00:00Z" -- the leading number is the
# occurrence's own id, kept only as a breadcrumb for the logs.
OCCUR_SERIES_RE = re.compile(r"^(\d+)_")


@dataclass(frozen=True)
class Event:
    occur_id: str
    title: str
    starts_at: datetime | None  # None for an all-day event
    location: str

    @property
    def all_day(self) -> bool:
        return self.starts_at is None

    @property
    def series_id(self) -> str:
        match = OCCUR_SERIES_RE.match(self.occur_id)
        return match.group(1) if match else ""


def element_url(base: str, element_id: str | int, day: date) -> str:
    return f"{base}/fs/elements/{element_id}?cal_date={day.isoformat()}"


def months_covering(earliest: date, latest: date) -> list[date]:
    """First-of-month dates to fetch to cover this range.

    One month wider at each end, because a month view carries a few days of its
    neighbours and an event near the boundary may be rendered only there.
    """
    first = earliest.year * 12 + earliest.month - 1 - 1
    last = latest.year * 12 + latest.month - 1 + 1
    return [date(i // 12, i % 12 + 1, 1) for i in range(first, last + 1)]


def parse_events(html: str) -> list[Event]:
    """Every event in one month of the calendar element."""
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for node in soup.select(".fsCalendarEvent"):
        link = node.select_one(".fsCalendarEventLink")
        if not link or not link.get("data-occur-id"):
            continue

        starts_at = None
        stamp = node.select_one("time.fsStartTime")
        if stamp and stamp.get("datetime"):
            try:
                starts_at = datetime.fromisoformat(stamp["datetime"]).astimezone(CENTRAL)
            except ValueError:
                log.debug("unreadable start time: %r", stamp.get("datetime"))
                continue

        location = node.select_one(".fsLocation")
        events.append(
            Event(
                occur_id=link["data-occur-id"],
                title=" ".join(link.get_text(" ").split()),
                starts_at=starts_at,
                location=" ".join(location.get_text(" ").split()) if location else "",
            )
        )
    return events
