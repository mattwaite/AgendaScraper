"""Lancaster County Board of Commissioners -- CivicPlus Agenda Center.

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

log = logging.getLogger(__name__)

BASE_URL = "https://www.lancaster.ne.gov"
LISTING_URL = f"{BASE_URL}/AgendaCenter/Board-of-Commissioners-29/"
TIMEOUT = 30

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

TYPE_PATTERNS = (
    ("SPECIAL", re.compile(r"(?i)special")),
    ("EMERGENCY", re.compile(r"(?i)emergency")),
    ("HEARING", re.compile(r"(?i)public hearing|board of equalization")),
    ("WORKSHOP", re.compile(r"(?i)staff meeting|work session|retreat")),
)


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

        try:
            response = session.get(LISTING_URL, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {LISTING_URL}: {exc}") from exc

        today = datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)

        meetings = []
        for entry in parse_listing(response.text):
            if not earliest <= entry["date"] <= latest:
                continue
            if meeting := self._build(session, entry):
                meetings.append(meeting)

        meetings.sort(key=lambda m: m.starts_at)
        return meetings

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
            external_id=f"lnc-agenda-{entry['agenda_id']}",
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
