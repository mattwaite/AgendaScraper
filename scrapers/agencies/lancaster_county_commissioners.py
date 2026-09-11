"""Lancaster County Board of Commissioners -- Agenda Center and iCalendar feed.

Two sources that turn out to be the two halves of the timeline, meeting at
today. The Agenda Center holds meetings that have already happened, back to
January and stopping at the last one held. The county calendar's iCalendar feed
holds the ones still to come. Checked on 2026-09-10 they did not overlap at all:
the Agenda Center ran to Sep 8 and the feed began at Sep 15.

Meetings are keyed on their series and date, e.g. lnc-commissioners-2026-09-15.
Each source has its own idea of an id -- an agenda file id on one side, an event
id on the other -- so a meeting would change identity as it aged from the feed
into the Agenda Center, which the platform rejects as a collision. The date
alone will not do either: the board and the Board of Equalization meet on the
same morning, so the series has to be part of it. Both sources name the series
in a way the same classifier can read.

Staff meetings are left out. The county records them as all-day entries with no
start time -- their event page says "Time: All Day" -- and the API needs a time.
Guessing the 8:30 the feed used to carry in 2019 would put a made-up hour in
front of an editor. The run says how many were passed over.

The Public Building Commission also appears on this calendar. It is a separate
body, not this agency, so it is left alone.

The Board of Equalization **is** published, and that is a deliberate exception
to the apex-board rule the other scrapers follow -- do not "fix" it. Every
agency here publishes only its governing body and not the committees beneath
it, but equalization is not beneath this one: it is the same five commissioners
in the same room sitting under a different statutory hat, and the valuation
protests it hears are worth a reporter on their own. Confirmed with the editors
on 2026-09-11.

## The Agenda Center half

A different shape of source from Lincoln's Granicus portal, in one way that
matters: **the listing has no meeting time.** Each row gives a date, the name of
the body, and a link to the agenda; the only clock time on the page is when the
agenda file was uploaded. The time lives in the agenda PDF's header:

    LANCASTER COUNTY BOARD OF COMMISSIONERS
    TUESDAY, SEPTEMBER 8, 2026, AT 9:00 AM
    COUNTY-CITY BUILDING, ROOM 112

So scraping is two steps -- read the listing, then open each agenda in the date
window to find its time and room. That is one PDF fetch per meeting, which is
cheap because the window normally holds one or two.

The board meets Tuesdays. A meeting appears here only once its agenda is posted,
usually the Friday before, so there is little lead time to be had from this
source.
"""

import logging
import re
from datetime import date, datetime, timedelta
from io import BytesIO

import pdfplumber
import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting
from ..sources import civicplus_ical

log = logging.getLogger(__name__)

BASE_URL = "https://www.lancaster.ne.gov"
LISTING_URL = f"{BASE_URL}/AgendaCenter/Board-of-Commissioners-29/"
ICAL_URL = (
    f"{BASE_URL}/common/modules/iCalendar/iCalendar.aspx?catID=14&feed=calendar"
)
TIMEOUT = 30

# Which of the calendar's events belong to this agency, and what to call them.
# The key is the series that goes into a meeting's external_id, so these strings
# are load-bearing in a way the display names are not.
SERIES = {
    "commissioners": {
        "pattern": re.compile(r"(?i)^board of commissioners meeting$"),
        "type": "REGULAR",
    },
    "equalization": {
        "pattern": re.compile(r"(?i)^board of equalization( meeting)?$"),
        "type": "HEARING",
    },
}
# Recorded as all-day entries with no start time, so they cannot be submitted.
STAFF_MEETING_RE = re.compile(r"(?i)staff meeting")

# Where the board sits when the agenda does not say otherwise.
DEFAULT_PLACE = "County-City Building, Room 112, 555 South 10th Street, Lincoln 68508"
# The agenda header names the building and room but never the street, so add it
# for the one address we know. Anywhere else (a Zoom meeting, say) is left as
# the agenda wrote it rather than given an address it may not have.
COUNTY_CITY_STREET = "555 South 10th Street, Lincoln 68508"

DAYS_BACK = 7
DAYS_FORWARD = 400

# "Agenda for September 8, 2026" on the row's <strong>.
ARIA_DATE_RE = re.compile(r"for\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")
# The numeric id in /AgendaCenter/ViewFile/Agenda/_09082026-2659
AGENDA_ID_RE = re.compile(r"/ViewFile/Agenda/_\d+-(\d+)")
# "AT 9:00 AM" in the agenda header.
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([APap])\.?\s*[Mm]\.?")
# The header line naming where it is held.
PLACE_RE = re.compile(r"(?i)BUILDING|ROOM|ZOOM|CHAMBERS")

TYPE_NAMES = {
    "REGULAR": "Lancaster County Board of Commissioners Regular Meeting",
    "SPECIAL": "Lancaster County Board of Commissioners Special Meeting",
    "HEARING": "Lancaster County Board of Commissioners Public Hearing",
    "WORKSHOP": "Lancaster County Board of Commissioners Staff Meeting",
    "EMERGENCY": "Lancaster County Board of Commissioners Emergency Meeting",
}

# The Board of Equalization is the same commissioners sitting to hear property
# valuation protests, so it gets its own name rather than the generic hearing.
SERIES_NAMES = {
    "equalization": "Lancaster County Board of Equalization",
}

TYPE_PATTERNS = (
    ("SPECIAL", re.compile(r"(?i)special")),
    ("EMERGENCY", re.compile(r"(?i)emergency")),
    ("HEARING", re.compile(r"(?i)public hearing|board of equalization")),
    ("WORKSHOP", re.compile(r"(?i)staff meeting|work session|retreat")),
)


def classify_series(summary: str) -> str | None:
    """Which of this agency's meeting series an event belongs to, if any.

    Reads the same way on both sources -- the calendar's event summary and the
    Agenda Center's row title -- which is what lets a meeting keep one identity
    as it ages from the calendar into the Agenda Center.
    """
    summary = " ".join(summary.split())
    for series, spec in SERIES.items():
        if spec["pattern"].match(summary):
            return series
    return None


def classify(text: str) -> str:
    for meeting_type, pattern in TYPE_PATTERNS:
        if pattern.search(text):
            return meeting_type
    return "REGULAR"


def parse_listing(html: str) -> list[dict]:
    """Pull one entry per agenda row: its date, agenda URL and stable id."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.catAgendaRow")
    if not rows:
        raise ScraperError(
            f"No agenda rows found at {LISTING_URL} -- the page layout has "
            "probably changed."
        )

    entries = []
    for row in rows:
        link = row.select_one('a[href*="/ViewFile/Agenda/"]')
        if not link:
            continue
        id_match = AGENDA_ID_RE.search(link.get("href", ""))
        if not id_match:
            continue

        label = row.find("strong")
        aria = label.get("aria-label", "") if label else ""
        date_match = ARIA_DATE_RE.search(aria)
        if not date_match:
            continue
        try:
            day = datetime.strptime(date_match.group(1), "%B %d, %Y").date()
        except ValueError:
            continue

        entries.append(
            {
                "date": day,
                "agenda_id": id_match.group(1),
                "agenda_url": BASE_URL + link["href"],
                "title": " ".join(link.get_text().split()),
            }
        )
    return entries


def parse_agenda_text(text: str) -> tuple[tuple[int, int] | None, str | None]:
    """Read the meeting's start time and room out of the agenda header.

    Returns ((hour, minute), place). Either may be None if the header does not
    say -- the caller decides what to do about a missing time.
    """
    head = text[:1200]

    start = None
    if match := TIME_RE.search(head):
        hour, minute = int(match.group(1)), int(match.group(2))
        if match.group(3).upper() == "P" and hour != 12:
            hour += 12
        elif match.group(3).upper() == "A" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            start = (hour, minute)

    place = None
    for raw_line in head.splitlines():
        line = " ".join(raw_line.split())
        # Skip the body's own name; we want the line naming the room.
        if "COMMISSIONERS" in line.upper() and "BUILDING" not in line.upper():
            continue
        if PLACE_RE.search(line):
            place = line.title()
            break

    if place and "county-city" in place.lower() and "street" not in place.lower():
        place = f"{place}, {COUNTY_CITY_STREET}"

    return start, place


class LancasterCountyCommissioners(BaseScraper):
    slug = "lancaster_county_commissioners"
    agency_id = "cmrygb9pe0003s91meavxc3s2"
    agency_name = "Lancaster County Board of Commissioners"

    def fetch(self) -> list[Meeting]:
        session = requests.Session()
        session.headers["User-Agent"] = "flatwater-agenda-scraper/1.0"

        listing = self._get(session, LISTING_URL)
        meetings = []
        by_id: dict[str, Meeting] = {}

        for entry in parse_listing(listing):
            if not self._in_window(entry["date"]):
                continue
            if meeting := self._build(session, entry):
                meetings.append(meeting)
                by_id[meeting.external_id] = meeting

        meetings += self.from_calendar(self._get(session, ICAL_URL), by_id)
        meetings.sort(key=lambda m: m.starts_at)
        return meetings

    def from_calendar(self, ical: str, by_id: dict[str, Meeting]) -> list[Meeting]:
        """Meetings the calendar knows about that the Agenda Center doesn't yet.

        The Agenda Center wins where both describe a meeting: it has the agenda,
        and a time read from the agenda's own header.
        """
        meetings, skipped_staff = [], 0

        for event in civicplus_ical.parse_events(ical):
            if STAFF_MEETING_RE.search(event.summary):
                if self._in_window(event.day):
                    skipped_staff += 1
                continue

            series = classify_series(event.summary)
            if series is None:
                continue  # a holiday, or another body's meeting
            if not self._in_window(event.day):
                continue
            if event.starts_at is None:
                self.skip(
                    f"{event.day}: {event.summary!r} is on the calendar as an "
                    "all-day entry, so its start time is unknown"
                )
                continue

            external_id = f"lnc-{series}-{event.day.isoformat()}"
            if external_id in by_id:
                continue

            meeting_type = SERIES[series]["type"]
            meeting = Meeting(
                name=SERIES_NAMES.get(series, TYPE_NAMES[meeting_type]),
                starts_at=event.starts_at,
                external_id=external_id,
                # The feed's own location is a bare "- Lincoln NE 68502".
                location=DEFAULT_PLACE,
                agenda_url=None,  # posted to the Agenda Center nearer the day
                meeting_type=meeting_type,
            )
            meetings.append(meeting)
            by_id[external_id] = meeting

        if skipped_staff:
            # One line rather than one per meeting: this is a known gap in what
            # the county publishes, not something going wrong each run.
            log.warning(
                "%d staff meetings left out: the county lists them as all-day "
                "entries with no start time",
                skipped_staff,
            )
        return meetings

    def _get(self, session: requests.Session, url: str) -> str:
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {url}: {exc}") from exc
        return response.text

    def _in_window(self, day: date, today: date | None = None) -> bool:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)
        return earliest <= day <= latest

    def _build(self, session: requests.Session, entry: dict) -> Meeting | None:
        """Turn one listing row into a Meeting, reading its agenda for the time."""
        text = self._agenda_text(session, entry["agenda_url"])
        if text is None:
            self.skip(
                f"{entry['date']}: could not read the agenda at "
                f"{entry['agenda_url']}, so the meeting time is unknown"
            )
            return None

        start, place = parse_agenda_text(text)
        if start is None:
            # Better a visibly missing meeting than one with an invented time.
            self.skip(
                f"{entry['date']}: no start time in the agenda header at "
                f"{entry['agenda_url']}"
            )
            return None

        day = entry["date"]
        meeting_type = classify(f"{entry['title']} {text[:400]}")
        return Meeting(
            name=TYPE_NAMES[meeting_type],
            starts_at=datetime(
                day.year, day.month, day.day, start[0], start[1], tzinfo=CENTRAL
            ),
            external_id=f"lnc-commissioners-{day.isoformat()}",
            location=place or DEFAULT_PLACE,
            agenda_url=entry["agenda_url"],
            meeting_type=meeting_type,
        )

    def _agenda_text(self, session: requests.Session, url: str) -> str | None:
        """First page of the agenda PDF as text, or None if it cannot be read.

        A malformed or missing PDF costs one meeting, not the run, so this
        catches broadly on purpose.
        """
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                return pdf.pages[0].extract_text() or ""
        except Exception as exc:
            log.debug("could not read agenda %s: %s", url, exc)
            return None
