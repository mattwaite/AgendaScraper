"""Omaha Inland Port Authority -- the meetings page on omahaipa.com.

One page, three tables -- the current year and two archived ones -- each a plain
`Date | Agenda | Minutes` grid. It answers ordinary requests with no bot
challenge and no JavaScript, and it carries meetings *already held* and
meetings *still to come* in the same tables, so like Sarpy this needs a single
source rather than a calendar merged against an agenda system.

The agency the platform calls "Omaha Port Authority" is the Omaha Inland Port
Authority, a political subdivision created under Neb. Rev. Stat. 13-3304. Its
own site and agendas use the longer name.

**The table gives a date and nothing else** -- no time, no place. Those live in
the agenda PDF, whose first page opens with a fixed three-line header:

    April 2nd, 2026
    Metropolitan Community College, Bldg. 21, Room 112, 9:00 A.M.
    5300 N. 30th Street, Omaha, NE 68111

The second line is read for the time and the room; the third for the street.
That position matters -- a regex over the whole page picks up times from the
agenda items instead, which is how an earlier pass came to believe meetings
were held at 1:45 and 3:15.

A meeting with no agenda yet is given the board's standing hour and room, and
this is the part not to "fix" into a skip. Lancaster skips its timeless staff
meetings because those never gain a time; here the default has a known expiry.
The agenda posts about a week ahead, the next run reads the real header, and
the record updates itself. Checked across 17 readable agendas, 10 of 12 that
state a time say 9:00 A.M. in Bldg. 21 Room 112 -- but not all of them, which
is why the PDF wins whenever there is one: the August 2026 meeting was at 4:30
P.M. in the Swanson Conference Center. Every defaulted meeting is logged.

Two things about the source are worth knowing before trusting a run.

Roughly a third of the agenda links are dead: 7 of 24 returned 404 from
omahaipa.com itself, mostly ones whose filenames contain commas. A broken link
is treated as no agenda -- the meeting keeps its place and takes the default
time -- and the URL is still submitted, because it is the agency's own
canonical link and a later fix upstream makes it work without anything
changing here.

A month the board skips is written into the table as a row like
"*July No Meeting*", which carries no date and is passed over silently. That is
a normal month, not an anomaly worth warning about.

Meetings are keyed on the date alone. One meeting per date across all 28 rows,
and the two September 2024 meetings fall on different days. The time is
deliberately *not* in the key: it is the field that gets defaulted, so a
meeting filed at the standing 9:00 and later corrected to 4:30 would strand its
own record every time an agenda posted a non-standard hour.
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

BASE_URL = "https://www.omahaipa.com"
LISTING_URL = f"{BASE_URL}/who-we-are/meetings-and-agendas"
TIMEOUT = 45
USER_AGENT = "flatwater-agenda-scraper/1.0"

DAYS_BACK = 7
DAYS_FORWARD = 400

# Agenda packets run to 4MB and 47 pages, so they are only opened for meetings
# recent enough for the exact hour to matter. A --since backfill still gets
# every meeting; it just takes the standing time for the older ones rather than
# downloading the archive to learn hours nobody will act on.
PDF_LOOKBACK = timedelta(days=90)

# "January 5, 2026" and "Thursday, August 1, 2024" both appear.
DATE_RE = re.compile(
    r"(?:[A-Z][a-z]+,\s*)?([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})"
)
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([APap])\.?\s*[Mm]\.?")

# Where the board sits, and when, unless an agenda says otherwise.
DEFAULT_TIME = (9, 0)
DEFAULT_ROOM = "Metropolitan Community College, Bldg. 21, Room 112"
STREET = "5300 N. 30th Street, Omaha, NE 68111"
DEFAULT_PLACE = f"{DEFAULT_ROOM}, {STREET}"

NAME = "Omaha Inland Port Authority Board Meeting"


def external_id_for(day: date) -> str:
    """The upsert key for the meeting on this date.

    The date alone. One page carries both the archive and the schedule, so
    nothing moves between systems -- and the time is left out deliberately,
    since it is the field that gets defaulted before an agenda exists.
    """
    return f"oipa-{day.isoformat()}"


def parse_listing(html: str) -> list[dict]:
    """One entry per dated row across every table on the page."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ScraperError(
            f"No meetings table at {LISTING_URL} -- the page layout has "
            "probably changed."
        )

    entries, seen = [], set()
    for row in [tr for table in tables for tr in table.find_all("tr")]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue  # header

        match = DATE_RE.search(" ".join(cells[0].get_text().split()))
        if not match:
            continue  # "*July No Meeting*" -- a normal month, not an anomaly
        try:
            day = datetime.strptime(
                f"{match.group(1)} {match.group(2)} {match.group(3)}", "%B %d %Y"
            ).date()
        except ValueError:
            continue
        if day in seen:
            continue
        seen.add(day)

        link = cells[1].find("a", href=True)
        entries.append(
            {
                "day": day,
                "agenda_url": BASE_URL + link["href"]
                if link and link["href"].startswith("/")
                else (link["href"] if link else None),
            }
        )
    return entries


def parse_header(text: str) -> tuple[tuple[int, int] | None, str | None]:
    """The time and place from an agenda's three-line header.

        April 2nd, 2026
        Metropolitan Community College, Bldg. 21, Room 112, 9:00 A.M.
        5300 N. 30th Street, Omaha, NE 68111

    Read by position, not by searching the page: the agenda items further down
    carry times of their own, and a loose regex picks those up instead.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()][:3]
    if len(lines) < 2:
        return None, None

    place_line, street = lines[1], (lines[2] if len(lines) > 2 else "")
    match = TIME_RE.search(place_line)
    if not match:
        return None, None

    hour = int(match.group(1)) % 12
    if match.group(3).upper() == "P":
        hour += 12
    room = place_line[: match.start()].strip(" ,@")
    place = ", ".join(part for part in (room, street) if part)
    return (hour, int(match.group(2))), place or None


class OmahaPortAuthority(BaseScraper):
    slug = "omaha_port_authority"
    agency_id = "cmryfih4a0009pl1m9pf7zf1j"
    agency_name = "Omaha Port Authority"

    def fetch(self) -> list[Meeting]:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        try:
            return self.parse(self._get(session, LISTING_URL).text, session=session)
        finally:
            session.close()

    def _get(self, session, url):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {url}: {exc}") from exc
        return response

    def _in_window(self, day: date, today: date | None = None) -> bool:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)
        return earliest <= day <= latest

    def read_agenda(self, session, url: str) -> tuple[tuple[int, int] | None, str | None]:
        """The time and place from an agenda packet, or (None, None).

        A failure here is ordinary rather than fatal: 7 of 24 agenda links on
        this site 404, and the meeting is still real.
        """
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception as exc:  # network, 404, or an unreadable PDF
            log.info("could not read the agenda at %s (%s)", url, exc)
            return None, None
        return parse_header(text)

    def parse(
        self, html: str, today: date | None = None, session=None
    ) -> list[Meeting]:
        today = today or datetime.now(CENTRAL).date()
        meetings: list[Meeting] = []

        for entry in parse_listing(html):
            day = entry["day"]
            if not self._in_window(day, today):
                continue

            start, place = None, None
            if entry["agenda_url"] and session and day >= today - PDF_LOOKBACK:
                start, place = self.read_agenda(session, entry["agenda_url"])

            if start is None:
                # No agenda yet, a dead link, or a header this could not read.
                # The standing hour stands in until the agenda posts, and the
                # next run replaces it with the real one.
                log.info(
                    "%s: no agenda to read a time from; using the board's "
                    "standing 9:00 AM at %s",
                    day.isoformat(),
                    DEFAULT_ROOM,
                )
                start = DEFAULT_TIME

            meetings.append(
                Meeting(
                    name=NAME,
                    starts_at=datetime(
                        day.year, day.month, day.day, start[0], start[1], tzinfo=CENTRAL
                    ),
                    external_id=external_id_for(day),
                    location=place or DEFAULT_PLACE,
                    agenda_url=entry["agenda_url"],
                    meeting_type="REGULAR",
                )
            )

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
