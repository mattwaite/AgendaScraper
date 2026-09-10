"""Lincoln Public Schools Board of Education -- SPARQ Data meeting portal.

The richest of the three sources so far. One plain HTML table, no JavaScript and
no bot challenge, and every row carries what the API wants: date and time, the
meeting's own title, an explicit meeting type, the address, and a link to the
agenda keyed by a stable meeting id.

    September 8, 2026 at 6:00 PM - Board of Education Regular Meeting
    Meeting Type: Regular
    Steve Joel District Leadership Center, 5905 O Street, Lincoln, NE 68510
    Agenda -> /Public/Agenda/89?meeting=763426

Because the titles here are specific -- work sessions, budget hearings, named
committees -- meeting names come from the source rather than from a constant per
type, unlike the Granicus and Agenda Center scrapers whose titles say only
"City Council - Action" or "Board of Commissioners". An editor reading a list of
meetings gets more from "Wellness, American Civics, Multicultural Committee"
than from "Regular Meeting".

The portal lists every body that meets under this board, including ESU 18 (the
educational service unit, whose board is the same people meeting the same night)
and standing committees. All of them are scraped; each keeps its own name, so an
editor can tell them apart. Narrowing that is a one-line filter in `_wanted`.

The LPS website itself (home.lps.org/board) is behind a bot challenge and only
links here, so this portal is the source.
"""

import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting

log = logging.getLogger(__name__)

BASE_URL = "https://meeting.sparqdata.com"
LISTING_URL = f"{BASE_URL}/Public/Organization/89"
TIMEOUT = 30

DAYS_BACK = 7
DAYS_FORWARD = 400

# "September 8, 2026 at 6:00 PM - Board of Education Regular Meeting"
HEADING_RE = re.compile(
    r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(.+)"
)
MEETING_TYPE_RE = re.compile(r"Meeting Type:\s*(\w+)")
MEETING_ID_RE = re.compile(r"[?&]meeting=(\d+)")

# The portal's own vocabulary, mapped onto the five types the API accepts.
# Everything it calls a committee or a working session lands on WORKSHOP.
TYPE_MAP = {
    "Regular": "REGULAR",
    "Special": "SPECIAL",
    "Emergency": "EMERGENCY",
    "Hearing": "HEARING",
    "Working": "WORKSHOP",
    "Staff": "WORKSHOP",
    "Finance": "WORKSHOP",
}

NAME_PREFIX = "Lincoln Public Schools"


def clean_location(cell) -> str:
    """Flatten the address cell, dropping the "[ map it ]" link."""
    for link in cell.find_all("a"):
        link.decompose()
    parts = [
        " ".join(part.split())
        for part in cell.get_text(separator="\n").split("\n")
        if part.strip() and part.strip() not in "[]"
    ]
    return ", ".join(parts).replace(" ,", ",").strip(" ,")


def parse_listing(html: str) -> list[dict]:
    """One entry per row of the meetings table."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        raise ScraperError(
            f"No meetings table at {LISTING_URL} -- the page layout has "
            "probably changed."
        )

    entries = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue  # header

        heading = " ".join(cells[0].get_text().split())
        match = HEADING_RE.search(heading)
        if not match:
            continue
        try:
            starts_at = datetime.strptime(
                f"{match.group(1)} {match.group(2)}", "%B %d, %Y %I:%M %p"
            ).replace(tzinfo=CENTRAL)
        except ValueError:
            continue

        # The title runs up to the "Meeting Type:" label that follows it.
        title = MEETING_TYPE_RE.split(match.group(3))[0].strip(" -–")
        type_match = MEETING_TYPE_RE.search(heading)
        source_type = type_match.group(1) if type_match else ""

        link = cells[2].find("a", href=MEETING_ID_RE)
        if not link:
            continue
        meeting_id = MEETING_ID_RE.search(link["href"]).group(1)

        entries.append(
            {
                "starts_at": starts_at,
                "title": title,
                "source_type": source_type,
                "meeting_id": meeting_id,
                "agenda_url": BASE_URL + link["href"]
                if link["href"].startswith("/")
                else link["href"],
                "location": clean_location(cells[1]),
            }
        )
    return entries


class LpsBoardOfEducation(BaseScraper):
    slug = "lps_board_of_education"
    agency_id = "cmrygddn60005s91mb1posjj9"
    agency_name = "Lincoln Public Schools Board of Education"

    def fetch(self) -> list[Meeting]:
        try:
            response = requests.get(
                LISTING_URL,
                timeout=TIMEOUT,
                headers={"User-Agent": "flatwater-agenda-scraper/1.0"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {LISTING_URL}: {exc}") from exc

        today = datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)

        meetings = []
        for entry in parse_listing(response.text):
            if not earliest <= entry["starts_at"].date() <= latest:
                continue
            if not self._wanted(entry):
                continue

            source_type = entry["source_type"]
            meeting_type = TYPE_MAP.get(source_type)
            if meeting_type is None:
                # A type the portal has not used before. REGULAR keeps the
                # meeting rather than dropping it, and the warning says to come
                # back and map it properly.
                log.warning(
                    "unmapped meeting type %r on %s; treating it as REGULAR",
                    source_type,
                    entry["starts_at"].date(),
                )
                meeting_type = "REGULAR"

            meetings.append(
                Meeting(
                    name=f"{NAME_PREFIX} {entry['title']}",
                    starts_at=entry["starts_at"],
                    external_id=f"lps-meeting-{entry['meeting_id']}",
                    location=entry["location"] or None,
                    agenda_url=entry["agenda_url"],
                    meeting_type=meeting_type,
                )
            )

        meetings.sort(key=lambda m: m.starts_at)
        return meetings

    def _wanted(self, entry: dict) -> bool:
        """Which of the portal's meetings belong to this agency.

        Everything, for now: the board's own meetings, its committees, and the
        ESU 18 board that meets the same night. Add a filter here if editors
        decide some of those are noise.
        """
        return True
