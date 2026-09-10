"""Lincoln City Council -- Granicus portal plus the city's meeting calendar.

Two sources, because neither alone covers what an editor needs.

Granicus (lnklan.granicus.com) holds the agendas. Its "Upcoming Events" table
lists a meeting only once its agenda is posted -- often not at all, days before
the meeting -- and its "Available Archives" tabs hold everything past. On its
own this scraper could see no future meetings whatsoever.

The city's own calendar page carries the schedule months ahead, in the same
OpenCities format the planning commission uses. It has no agenda links, but the
API does not require one, so a meeting goes in as soon as it is scheduled and
gains its agenda on a later run once Granicus publishes it.

Checked on 2026-09-10, the two agreed exactly: 30 shared dates, identical times,
and the only calendar-extra entries were the five future meetings. Where both
describe a meeting, Granicus wins -- it is the system the agenda hangs off --
and a disagreement about the time is logged.

Meetings are keyed on their date. Granicus offers a clip_id and the calendar
does not, so keying on the clip would change a meeting's identity the moment it
moved from one source to the other, which the platform would reject as a
collision. Across 45 archived meetings no two shared a date; if that ever
happens the second is skipped rather than silently overwriting the first.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting
from ..sources import opencities

log = logging.getLogger(__name__)

GRANICUS_URL = "https://lnklan.granicus.com/ViewPublisher.php?view_id=2"
CALENDAR_URL = (
    "https://www.lincoln.ne.gov/City/City-Council/City-Council-Public-Meeting"
)
PLACE = (
    "Council Chambers, County/City Building, 555 South 10th Street, Lincoln 68508"
)
# Note the dots in "555 S. 10th Street" -- an address is not a sentence.
LOCATION_RE = r"(Council Chambers, County/City Building.{0,120}?68508)"

# How far either side of today to keep meetings. Backwards is short because the
# assignment site cares about upcoming coverage, not history; override with
# --since for a backfill.
DAYS_BACK = 7
DAYS_FORWARD = 400

DATE_TIME_RE = re.compile(
    r"([A-Za-z]+\s+\d+,\s+\d{4})\s*-\s*(\d+:\d+\s*[APap][Mm])"
)
CLIP_ID_RE = re.compile(r"clip_id=(\d+)")

# One name per meeting type. Editing these is safe -- the API upserts on
# externalId, so a renamed meeting updates its record rather than doubling up.
TYPE_NAMES = {
    "REGULAR": "Lincoln City Council Regular Meeting",
    "SPECIAL": "Lincoln City Council Special Meeting",
    "HEARING": "Lincoln City Council Public Hearing",
    "WORKSHOP": "Lincoln City Council Work Session",
    "EMERGENCY": "Lincoln City Council Emergency Meeting",
}

# Matched against the Granicus row title, first hit wins.
TYPE_PATTERNS = (
    ("SPECIAL", re.compile(r"(?i)special")),
    ("EMERGENCY", re.compile(r"(?i)emergency")),
    ("HEARING", re.compile(r"(?i)hearing")),
    ("WORKSHOP", re.compile(r"(?i)pre-?council|work\s*session|committee|retreat")),
)


def parse_date_time(raw_date: str) -> datetime | None:
    """Turn a Granicus date cell into an aware datetime, or None if unparseable.

    Typical cell text, once whitespace is collapsed: "May 18, 2026 - 5:30 PM".
    """
    normalized = " ".join(raw_date.split()).strip("- ").strip()
    match = DATE_TIME_RE.search(normalized)
    if not match:
        return None
    try:
        naive = datetime.strptime(
            f"{match.group(1).strip()} {match.group(2).strip().upper()}",
            "%b %d, %Y %I:%M %p",
        )
    except ValueError:
        return None
    return naive.replace(tzinfo=CENTRAL)


def classify(title: str) -> str:
    for meeting_type, pattern in TYPE_PATTERNS:
        if pattern.search(title):
            return meeting_type
    return "REGULAR"


def external_id_for(day: date) -> str:
    """The upsert key for a meeting on this date.

    Deliberately the date alone. Granicus offers a clip_id and the city
    calendar does not, so keying on the clip would change a meeting's identity
    the moment it moved between sources -- which the platform rejects as a
    collision. Meeting type is no good either: it is derived from the Granicus
    row title, which the calendar has no equivalent of.
    """
    return f"lnk-{day.isoformat()}"


def _clip_id(row) -> str | None:
    """Granicus puts clip_id in every link it emits for a row."""
    for link in row.find_all("a"):
        if match := CLIP_ID_RE.search(link.get("href", "")):
            return match.group(1)
    return None


def _absolute(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    return href


class LincolnCityCouncil(BaseScraper):
    slug = "lincoln_city_council"
    agency_id = "cmryg8k2p0001s91mclkzpdnq"
    agency_name = "Lincoln City Council"

    def fetch(self) -> list[Meeting]:
        granicus_html, calendar_html = asyncio.run(self._load_html())
        meetings = self.parse(granicus_html)
        return self.merge_calendar(meetings, calendar_html)

    async def _load_html(self) -> tuple[str, str]:
        """Both sources in one browser session."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(
                    GRANICUS_URL, wait_until="networkidle", timeout=60000
                )
                granicus = await page.content()

                # The city site never reaches networkidle, so wait for the
                # document and give its scripts a moment to fill the list in.
                await page.goto(
                    CALENDAR_URL, wait_until="domcontentloaded", timeout=60000
                )
                await page.wait_for_timeout(4000)
                return granicus, await page.content()
            finally:
                await browser.close()

    def merge_calendar(self, meetings: list[Meeting], html: str) -> list[Meeting]:
        """Add scheduled meetings the calendar knows about and Granicus doesn't.

        Granicus wins where both describe a meeting: it is the system the agenda
        hangs off. A disagreement about the time is logged rather than resolved,
        since which source updates first after a reschedule is not yet known.
        """
        window = {m.starts_at.date(): m for m in meetings}
        place = opencities.parse_location(html, LOCATION_RE) or PLACE

        for starts_at in opencities.parse_event_dates(html, CALENDAR_URL):
            day = starts_at.date()
            if not self._in_window(day):
                continue

            if existing := window.get(day):
                if existing.starts_at != starts_at:
                    log.info(
                        "%s: Granicus says %s, the city calendar says %s; "
                        "keeping Granicus",
                        day.isoformat(),
                        existing.starts_at.strftime("%-I:%M %p"),
                        starts_at.strftime("%-I:%M %p"),
                    )
                continue

            meeting = Meeting(
                name=TYPE_NAMES["REGULAR"],
                starts_at=starts_at,
                external_id=external_id_for(day),
                location=place,
                agenda_url=None,  # Granicus publishes it closer to the day
                meeting_type="REGULAR",
            )
            window[day] = meeting
            meetings.append(meeting)

        meetings.sort(key=lambda m: m.starts_at)
        return meetings

    def _in_window(self, day: date, today: date | None = None) -> bool:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)
        return earliest <= day <= latest

    def parse(
        self,
        html: str,
        today: date | None = None,
        days_back: int = DAYS_BACK,
        days_forward: int = DAYS_FORWARD,
    ) -> list[Meeting]:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=days_back)
        latest = self.until or today + timedelta(days=days_forward)

        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table", class_="listingTable")
        if not tables:
            raise ScraperError(
                f"No listingTable found at {GRANICUS_URL} -- the page layout has "
                "probably changed."
            )

        meetings: list[Meeting] = []
        seen_clips: set[str] = set()
        by_id: dict[str, Meeting] = {}

        for row in [tr for table in tables for tr in table.find_all("tr")]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue  # header row, or the "no Upcoming Events" placeholder

            starts_at = parse_date_time(cells[1].get_text())
            if starts_at is None:
                continue
            if not earliest <= starts_at.date() <= latest:
                continue

            # The agenda link sits in a different column in the upcoming table
            # than in the archives, so search the whole row. It may be missing
            # entirely on a meeting whose agenda has not been posted; that is
            # fine, the API does not require one and a later run fills it in.
            agenda_href = ""
            for link in row.find_all("a"):
                href = link.get("href", "")
                if "AgendaViewer.php" in href:
                    agenda_href = _absolute(href)
                    break

            clip_id = _clip_id(row)
            if clip_id and clip_id in seen_clips:
                continue  # a meeting can appear in both upcoming and archives
            if clip_id:
                seen_clips.add(clip_id)

            external_id = external_id_for(starts_at.date())
            if external_id in by_id:
                # Two meetings on one day. Unobserved across 45 archived
                # meetings, but keying on the date cannot represent it, and
                # overwriting the first silently would be worse than saying so.
                self.skip(
                    f"{starts_at.date()}: a second meeting at "
                    f"{starts_at.strftime('%-I:%M %p')} shares the day with "
                    f"{by_id[external_id].starts_at.strftime('%-I:%M %p')}, and "
                    "this source identifies meetings only by date"
                )
                continue

            meeting_type = classify(" ".join(cells[0].get_text().split()))
            meeting = Meeting(
                name=TYPE_NAMES[meeting_type],
                starts_at=starts_at,
                external_id=external_id,
                location=PLACE,
                agenda_url=agenda_href or None,
                meeting_type=meeting_type,
            )
            meetings.append(meeting)
            by_id[external_id] = meeting

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
