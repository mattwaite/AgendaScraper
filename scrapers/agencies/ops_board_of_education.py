"""Omaha Public Schools Board of Education -- SPARQ portal plus the district calendar.

The same two-source shape as the other four agencies, and the first Omaha one.

SPARQ Data (meeting.sparqdata.com/Public/Organization/120) holds the agendas.
It is the same system Lincoln Public Schools uses, at a different organization
number, and the same parser reads both -- see `sources/sparq.py`. It lists a
meeting only once its agenda is posted: checked on 2026-09-11, its newest
meeting was the evening before.

The district calendar (see `sources/finalsite.py`) carries the board's schedule
about ten months ahead with no agenda links. A meeting goes in as soon as it is
scheduled and gains its agenda on a later run once SPARQ publishes it.

Checked on 2026-09-11 across Sep 2025 - Dec 2026, the two agreed exactly: 25
board events shared a date *and* a start time, with no disagreement about the
hour anywhere. The only calendar-extra entries were the eight future meetings
and three "Board of Education Town Hall" events, which SPARQ does not carry at
all -- those are kept, since a town hall is a board event a reporter may be
sent to, and the API does not require an agenda.

Only the board itself is published. Editors want the apex body of each
organization, so this leaves out the board's committees and also the other
boards that meet under the district's roof -- the Omaha School Employees'
Retirement System Board of Trustees, which accounts for 37 rows on its own, and
the Nebraska Schools Medicaid Consortium. See `is_apex_board` for how the two
are told apart; between them they are 62 of the portal's 462 rows.

Unlike Lincoln, no ESU folding is needed here: OPS names the combined body in
one title already, "Omaha Public Schools Board of Education and Educational
Service Unit 19 Board Meeting", so there is nothing to collapse.

Meetings are keyed on date and time. Across all 462 archived meetings no two
shared a start time, while 51 of 409 dates carried more than one meeting -- so
the time is what tells a 5:00 budget hearing from the 6:00 board meeting that
follows it, and keying on the date alone would lose one of them.

The two sources reconcile on date *and* time, which is the opposite of what the
Lincoln schools scraper does, and deliberately. LPS's calendar lists one event
per date, so collapsing a date was free there. This calendar does not: Sep 10
carries a 17:00 budget hearing and an 18:00 board meeting, both real and both
on the calendar. Reconciling on the date alone would drop whichever one SPARQ
had not published an agenda for yet -- silently losing the meeting editors most
want lead time on. Matching the whole slot instead risks leaving a stale record
behind when a meeting moves, and the runner reports that rather than hiding it.
"""

import logging
import re
from datetime import date, datetime, timedelta

import requests

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting
from ..sources import finalsite, sparq

log = logging.getLogger(__name__)

LISTING_URL = sparq.listing_url(120)
CALENDAR_BASE = "https://www.ops.org"
CALENDAR_ELEMENT = 2039
TIMEOUT = 30
USER_AGENT = "flatwater-agenda-scraper/1.0"

_FAR_FUTURE = datetime.max.replace(tzinfo=CENTRAL)  # sorts all-day events last

DAYS_BACK = 7
DAYS_FORWARD = 400

# How far back the calendar is read, whatever --since says, and a ceiling on
# the months fetched. The calendar ran ten months ahead when this was written.
CALENDAR_LOOKBACK = timedelta(days=31)
MAX_CALENDAR_MONTHS = 18

# The portal's own vocabulary, mapped onto the five types the API accepts.
# "Unit" is how OPS types a committee -- the row reads "Unit (American Civics
# Committee)" -- and a committee is a workshop as far as the API is concerned.
TYPE_MAP = {
    "Regular": "REGULAR",
    "Special": "SPECIAL",
    "Emergency": "EMERGENCY",
    "Hearing": "HEARING",
    "Working": "WORKSHOP",
    "Unit": "WORKSHOP",
}

# The calendar has no type field, so it is read off the title. First hit wins.
TITLE_TYPES = (
    ("HEARING", re.compile(r"(?i)hearing")),
    ("SPECIAL", re.compile(r"(?i)special")),
    ("WORKSHOP", re.compile(r"(?i)workshop|work\s*session|committee|retreat|town\s*hall")),
)

# Which calendar entries belong to the board. The district calendar is mostly
# school events -- open houses, graduations, days off -- so this is a filter
# rather than a sieve. Checked against 140 calendar events over 16 months: no
# entry without "board" in its title was a board meeting.
BOARD_TITLE_RE = re.compile(r"(?i)\bboard\b")

# Only the apex board. Editors want the Board of Education itself -- not its
# committees, and not the other boards that meet under the district's roof.
#
# SPARQ marks the difference itself: it types a meeting "Unit" when the body is
# something other than the board. All 54 Unit rows in the archive are one of
# these, and none of them is the board:
#
#     37  Omaha School Employees' Retirement System Board of Trustees
#     14  American Civics Committee
#      2  Nebraska Schools Medicaid Consortium Board
#      1  Ad Hoc Student Discipline Hearing Committee
#
# The type alone is not quite enough. The Ad Hoc student discipline committee
# is also filed as Hearing and Special on eight other dates, so the name is
# checked too -- both the title and the bracket qualifying the type, since the
# source is careless with these: one row is titled simply "Committee Meeting"
# and another "American Committee Meeting", and the bracket is the field that
# actually identifies the body.
#
# Together these drop 62 of 462 rows, and every one of the 400 that remain is
# a meeting of the board itself.
COMMITTEE_RE = re.compile(r"(?i)\bcommittee\b")
UNIT_TYPE = "Unit"


def is_apex_board(title: str, source_type: str = "", detail: str = "") -> bool:
    """Whether this row is the Board of Education rather than some other body."""
    if source_type == UNIT_TYPE:
        return False
    return not (COMMITTEE_RE.search(title) or COMMITTEE_RE.search(detail))

NAME_PREFIX = "Omaha Public Schools"


def external_id_for(starts_at: datetime) -> str:
    """The upsert key for the meeting in this slot.

    Date and time. Not SPARQ's own meeting id, which the calendar has no
    equivalent of -- keying on it would change a meeting's identity the moment
    it moved from one source to the other, stranding the calendar's record.
    """
    return f"ops-{starts_at.date().isoformat()}-{starts_at.strftime('%H%M')}"


def canonical_name(title: str) -> str:
    """Prefix a source title with the district, without repeating it.

    SPARQ's regular-meeting title already begins "Omaha Public Schools"; the
    calendar's says only "Board Meeting". Both end up readable, and a record
    does not get renamed for the sake of it when SPARQ takes it over.
    """
    title = " ".join(title.split())
    if title.lower().startswith(NAME_PREFIX.lower()):
        return title
    return f"{NAME_PREFIX} {title}"


def classify_title(title: str) -> str:
    for meeting_type, pattern in TITLE_TYPES:
        if pattern.search(title):
            return meeting_type
    return "REGULAR"


class OpsBoardOfEducation(BaseScraper):
    slug = "ops_board_of_education"
    agency_id = "cmryf502m0003pl1m3pqf52bt"
    agency_name = "Omaha Public Schools Board of Education"

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

    def _window(self, today: date | None = None) -> tuple[date, date]:
        today = today or datetime.now(CENTRAL).date()
        return (
            self.since or today - timedelta(days=DAYS_BACK),
            self.until or today + timedelta(days=DAYS_FORWARD),
        )

    def _in_window(self, day: date, today: date | None = None) -> bool:
        earliest, latest = self._window(today)
        return earliest <= day <= latest

    def _calendar_events(self, session) -> list[finalsite.Event]:
        """The calendar element, one month at a time, deduplicated.

        A month view carries a few days of its neighbours, so the same event
        comes back from two fetches; `occur_id` is what tells them apart.

        The lookback is clamped regardless of --since. This calendar is here
        for lead time, and SPARQ already holds the past with the agendas
        attached, so a backfill to 2011 would mean two hundred requests for
        months whose meetings are better described by the other source.
        """
        earliest, latest = self._window()
        earliest = max(earliest, datetime.now(CENTRAL).date() - CALENDAR_LOOKBACK)
        if latest < earliest:
            return []

        events: dict[str, finalsite.Event] = {}
        for month in finalsite.months_covering(earliest, latest)[:MAX_CALENDAR_MONTHS]:
            url = finalsite.element_url(CALENDAR_BASE, CALENDAR_ELEMENT, month)
            for event in finalsite.parse_events(self._get(session, url).text):
                events.setdefault(event.occur_id, event)
        return list(events.values())

    def merge_calendar(
        self, meetings: list[Meeting], events: list[finalsite.Event]
    ) -> list[Meeting]:
        """Add board meetings the calendar knows about and SPARQ doesn't.

        Matched on the whole slot -- see the note in the module docstring about
        why this differs from the Lincoln schools scraper.
        """
        board = [
            e
            for e in events
            if BOARD_TITLE_RE.search(e.title) and is_apex_board(e.title)
        ]
        if events and not board:
            raise ScraperError(
                f"No board meetings among {len(events)} calendar events at "
                f"{CALENDAR_BASE} -- the calendar element or its titles have "
                "probably changed."
            )

        by_slot = {m.starts_at: m for m in meetings}
        for event in sorted(board, key=lambda e: e.starts_at or _FAR_FUTURE):
            if event.all_day:
                self.skip(
                    f"{event.title!r} is on the calendar with no start time, so "
                    "there is no hour to send a reporter to"
                )
                continue
            if not self._in_window(event.starts_at.date()):
                continue
            if event.starts_at in by_slot:
                continue  # SPARQ has it, and SPARQ has the agenda

            meeting = Meeting(
                name=canonical_name(event.title),
                starts_at=event.starts_at,
                external_id=external_id_for(event.starts_at),
                location=event.location or None,
                agenda_url=None,  # SPARQ publishes it closer to the day
                meeting_type=classify_title(event.title),
            )
            by_slot[event.starts_at] = meeting
            meetings.append(meeting)

        meetings.sort(key=lambda m: m.starts_at)
        return meetings

    def parse(self, html: str, today: date | None = None) -> list[Meeting]:
        meetings: list[Meeting] = []
        by_id: dict[str, Meeting] = {}

        for entry in sparq.parse_listing(html, LISTING_URL):
            if not self._in_window(entry["starts_at"].date(), today):
                continue
            if not is_apex_board(
                entry["title"], entry["source_type"], entry.get("type_detail", "")
            ):
                continue

            external_id = external_id_for(entry["starts_at"])
            if (existing := by_id.get(external_id)) is not None:
                # Unobserved across 462 archived meetings, but keying on the
                # slot cannot represent it, and overwriting silently is worse
                # than saying so.
                self.skip(
                    f"{entry['starts_at'].date()} at "
                    f"{entry['starts_at'].strftime('%-I:%M %p')}: "
                    f"{entry['title']!r} shares the slot with {existing.name!r}"
                )
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

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
