"""Lincoln Public Schools Board of Education -- SPARQ portal plus the LPS calendar.

Two sources, for the same reason as the council and the county: the system that
holds the agendas only lists a meeting once its agenda is posted, about a week
ahead, and an editor assigning a reporter needs longer than that.

SPARQ Data (meeting.sparqdata.com/Public/Organization/89) is the agenda system.
One plain HTML table, no JavaScript and no bot challenge, and every row carries
what the API wants: date and time, the meeting's own title, an explicit meeting
type, the address, and a link to the agenda. It stops at the last posted agenda
-- checked on 2026-09-10, its newest meeting was two days in the past.

The district calendar (see `sources/thrillshare.py`) carries the board's own
schedule to the end of the school year -- 16 meetings running to May 2027 when
this was written -- with no agenda links. A meeting goes in as soon as it is
scheduled and gains its agenda on a later run once SPARQ publishes it.

Only the board's own meetings are on that calendar. Its committees -- finance,
wellness, work sessions -- are still SPARQ-only, so those keep appearing about
a week out. When SPARQ takes a record over it also brings its explicit meeting
type, which can differ from the one read off the calendar's title here: the
organizational meeting reads SPECIAL from the title and `Regular` in SPARQ, so
that record updates to REGULAR when the agenda posts. That is the right way
round -- SPARQ states the type, this scraper guesses it -- but it does show up
in a run's output as a changed field.

Because the titles in SPARQ are specific -- work sessions, budget hearings,
named committees -- meeting names come from the source rather than from a
constant per type, unlike the Granicus and Agenda Center scrapers whose titles
say only "City Council - Action". An editor reading a list of meetings gets more
from "Wellness, American Civics, Multicultural Committee" than from "Regular
Meeting".

Meetings are keyed on date *and* time, because this board really does meet more
than once a day: across 701 archived meetings, 170 of 489 dates carried more
than one, and keying on the date alone would have collapsed a 9 AM committee
into the 6 PM regular meeting.

ESU 18 is folded into the meeting it shares a slot with, and this is the part
to read before "fixing" it. ESU 18 is the educational service unit whose board
is the same people, in the same room, on the same night. SPARQ lists it as a
separate meeting, but says in that meeting's own text:

    or as soon thereafter as the same may commence following the
    Lincoln Board of Education Meeting

The district calendar agrees and does not split them at all, listing "Lincoln
Board of Education and ESU18 Regular Meetings" as one entry. Of 97 dates where
two meetings shared a start time, 91 were exactly this pairing. Left separate,
the platform shows one gathering twice and an editor sends two reporters to one
room -- which is what the first two records this scraper ever wrote did. So the
twin is folded in and the non-ESU title wins the name. A same-slot collision
that is *not* this pairing is a real one and is skipped with a warning.
"""

import logging
import re
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting
from ..sources import thrillshare

log = logging.getLogger(__name__)

BASE_URL = "https://meeting.sparqdata.com"
LISTING_URL = f"{BASE_URL}/Public/Organization/89"
CALENDAR_URL = (
    "https://lincolnpublicschools.thrillshare.com/api/v4/o/31429/cms/events"
    "?locale=en&page_no=1"
)
# The calendar API carries the whole district; this is the board's section.
CALENDAR_SECTION = "Board of Education Calendar"
TIMEOUT = 30
USER_AGENT = "flatwater-agenda-scraper/1.0"

_FAR_FUTURE = datetime.max.replace(tzinfo=CENTRAL)  # sorts all-day events last

DAYS_BACK = 7
DAYS_FORWARD = 400

# "September 8, 2026 at 6:00 PM - Board of Education Regular Meeting"
HEADING_RE = re.compile(
    r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(.+)"
)
MEETING_TYPE_RE = re.compile(r"Meeting Type:\s*(\w+)")
MEETING_ID_RE = re.compile(r"[?&]meeting=(\d+)")

# "ESU 18", "ESU18" and "ESU #18" all appear across the two sources.
ESU_RE = re.compile(r"(?i)\bESU\s*#?\s*18\b")

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

# The calendar has no type field, so it is read off the title. First hit wins.
TITLE_TYPES = (
    ("HEARING", re.compile(r"(?i)hearing")),
    ("SPECIAL", re.compile(r"(?i)special|organization")),
    ("WORKSHOP", re.compile(r"(?i)work\s*session|committee|retreat")),
)

NAME_PREFIX = "Lincoln Public Schools"


def external_id_for(starts_at: datetime) -> str:
    """The upsert key for the meeting in this slot.

    Date and time, because this board meets more than once on many days. Not
    SPARQ's own meeting id, which the district calendar has no equivalent of --
    keying on it would change a meeting's identity the moment it moved from one
    source to the other, and leave the calendar's record orphaned.
    """
    return f"lps-{starts_at.date().isoformat()}-{starts_at.strftime('%H%M')}"


def canonical_name(title: str) -> str:
    """Prefix a source title with the district, without saying "Lincoln" twice.

    SPARQ titles start "Board of Education ..."; the calendar's start "Lincoln
    Board of Education ...". Both come out the same, so a meeting does not get
    renamed when SPARQ takes the record over from the calendar.
    """
    title = re.sub(r"(?i)^lincoln\s+(?=board\b)", "", title).strip()
    if title.lower().startswith(NAME_PREFIX.lower()):
        return title
    return f"{NAME_PREFIX} {title}"


def classify_title(title: str) -> str:
    for meeting_type, pattern in TITLE_TYPES:
        if pattern.search(title):
            return meeting_type
    return "REGULAR"


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


def clean_venue(venue: str) -> str:
    """The calendar's venue string, punctuated like SPARQ's address.

    "Steve Joel District Leadership Center - 5905 O St, Lincoln, NE 68510, USA"

    These are typed by hand and vary: some name the room, some spell the dash
    without a space in front of it. Requiring a space on both sides leaves
    "Center- 5905" in the address, so only the space *after* the dash is
    required -- which is also what keeps a hyphenated name like
    "Lincoln-Lancaster" intact.
    """
    venue = re.sub(r"\s*-\s+", ", ", venue)
    venue = re.sub(r"(?i),\s*USA\s*$", "", venue)
    return " ".join(venue.split()).strip(" ,")


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
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        try:
            meetings = self.parse(self._get(session, LISTING_URL).text)
            return self.merge_calendar(meetings, self._calendar_events(session))
        finally:
            session.close()

    def _get(self, session, url):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {url}: {exc}") from exc
        return response

    def _calendar_events(self, session) -> list[thrillshare.Event]:
        """Every page of the district calendar.

        The API advertises a per-section URL but ignores the filter, so all of
        it is read and the board's section picked out here.
        """
        events, url, seen = [], CALENDAR_URL, 0
        while url and seen < thrillshare.PAGE_LIMIT:
            payload = self._get(session, url).json()
            events.extend(thrillshare.parse_events(payload))
            url = thrillshare.next_page(payload)
            seen += 1
        return events

    def merge_calendar(
        self, meetings: list[Meeting], events: list[thrillshare.Event]
    ) -> list[Meeting]:
        """Add board meetings the calendar knows about and SPARQ doesn't.

        Reconciled on the date, not the whole key. If SPARQ covers a date at
        all, its time wins and the calendar's entry is dropped -- otherwise a
        rescheduled meeting would be written twice, once under each time, and
        the calendar's copy would never go away. A disagreement is logged
        rather than resolved: nothing yet shows which source updates first.
        """
        board = [e for e in events if e.section == CALENDAR_SECTION]
        if events and not board:
            raise ScraperError(
                f"No {CALENDAR_SECTION!r} events among {len(events)} at "
                f"{CALENDAR_URL} -- the section has probably been renamed."
            )

        by_day = {m.starts_at.date(): m for m in meetings}
        for event in sorted(board, key=lambda e: (e.starts_at or _FAR_FUTURE)):
            if event.all_day:
                self.skip(
                    f"{event.title!r} is on the calendar with no start time, so "
                    "there is no hour to send a reporter to"
                )
                continue

            day = event.starts_at.date()
            if not self._in_window(day):
                continue

            if existing := by_day.get(day):
                if existing.starts_at != event.starts_at:
                    log.info(
                        "%s: SPARQ says %s, the district calendar says %s; "
                        "keeping SPARQ",
                        day.isoformat(),
                        existing.starts_at.strftime("%-I:%M %p"),
                        event.starts_at.strftime("%-I:%M %p"),
                    )
                continue

            meeting = Meeting(
                name=canonical_name(event.title),
                starts_at=event.starts_at,
                external_id=external_id_for(event.starts_at),
                location=clean_venue(event.venue) or None,
                agenda_url=None,  # SPARQ publishes it closer to the day
                meeting_type=classify_title(event.title),
            )
            by_day[day] = meeting
            meetings.append(meeting)

        meetings.sort(key=lambda m: m.starts_at)
        return meetings

    def _in_window(self, day: date, today: date | None = None) -> bool:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)
        return earliest <= day <= latest

    def parse(self, html: str, today: date | None = None) -> list[Meeting]:
        meetings: list[Meeting] = []
        by_id: dict[str, Meeting] = {}
        titles: dict[str, str] = {}  # external_id -> the winning source title

        for entry in parse_listing(html):
            if not self._in_window(entry["starts_at"].date(), today):
                continue
            if not self._wanted(entry):
                continue

            external_id = external_id_for(entry["starts_at"])
            if (existing := by_id.get(external_id)) is not None:
                self._fold(external_id, existing, titles, entry, by_id, meetings)
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

            meeting = Meeting(
                name=canonical_name(entry["title"]),
                starts_at=entry["starts_at"],
                external_id=external_id,
                location=entry["location"] or None,
                agenda_url=entry["agenda_url"],
                meeting_type=meeting_type,
            )
            meetings.append(meeting)
            by_id[external_id] = meeting
            titles[external_id] = entry["title"]

        meetings.sort(key=lambda m: m.starts_at)
        return meetings

    def _fold(self, external_id, existing, titles, entry, by_id, meetings) -> None:
        """Handle a second meeting in a slot that already has one.

        The ESU 18 twin is expected -- see the module docstring -- and is folded
        into the meeting it follows, at info level, because warning about the
        normal case 91 times over a backfill teaches people to ignore warnings.
        Anything else in the same slot is a real collision and gets one.
        """
        kept, extra = titles[external_id], entry["title"]
        if bool(ESU_RE.search(extra)) != bool(ESU_RE.search(kept)):
            esu, board = (extra, kept) if ESU_RE.search(extra) else (kept, extra)
            log.info(
                "%s: folding %r into %r -- same room, same night",
                entry["starts_at"].date().isoformat(),
                esu,
                board,
            )
            # The non-ESU title names the record, whichever order they arrived.
            if ESU_RE.search(kept):
                better = Meeting(
                    name=canonical_name(extra),
                    starts_at=existing.starts_at,
                    external_id=external_id,
                    location=entry["location"] or existing.location,
                    agenda_url=entry["agenda_url"] or existing.agenda_url,
                    meeting_type=TYPE_MAP.get(entry["source_type"], "REGULAR"),
                )
                meetings[meetings.index(existing)] = better
                by_id[external_id] = better
                titles[external_id] = extra
            return

        self.skip(
            f"{entry['starts_at'].date()} at "
            f"{entry['starts_at'].strftime('%-I:%M %p')}: {extra!r} shares the "
            f"slot with {kept!r}, and this source identifies meetings by when "
            "they start"
        )

    def _wanted(self, entry: dict) -> bool:
        """Which of the portal's meetings belong to this agency.

        Everything, for now: the board's own meetings and its committees. ESU 18
        is not filtered here -- it is folded into the meeting it shares a slot
        with, in `_fold`.
        """
        return True
