"""Sarpy County Board of Commissioners -- the CivicWeb portal's meetings service.

The first agency here that needs only one source. Sarpy runs a CivicWeb portal
whose JSON service (see `sources/civicweb.py`) returns the whole archive *and*
the meetings still to come in a single call -- 658 meetings from 2019 to 2027
when this was written -- so there is no agenda system to reconcile against a
separate calendar.

The county's other publishing systems are dead ends, and are documented here so
nobody spends an afternoon rediscovering that:

- `sarpy.gov/AgendaCenter` is CivicPlus but holds one category, the Visitors
  Committee, with two agendas in it.
- `sarpy.gov/calendar.aspx` is a community events calendar -- concerts, bingo,
  minor-league baseball. It carries no board meetings at all.
- Every CivicPlus iCalendar feed on the site returns an empty VCALENDAR, so the
  reader that works for Lancaster has nothing to read here.

Because there is one source, meetings are keyed on the portal's own `Id`. That
is the opposite of every other scraper in this project, and deliberately: the
rule against source ids exists because the other agencies read two sources and
only one of them has the id, so a meeting changed identity as it moved between
them. That cannot happen here. The id is also strictly better than a key built
from the date and time, which changes whenever a meeting moves and strands the
old record; an id survives a reschedule and updates in place.

**Only the apex board.** Types 10 and 30 are the board's own meetings -- 30 is
the legacy type, running to 2025-12-09, with nothing in it after the changeover
to 10 -- and 16 is the Board of Equalization.

Equalization is published, matching the decision made for Lancaster County: it
is the same commissioners in the same room under a different statutory hat, not
a body beneath them, and the valuation protests it hears are worth a reporter.
Everything else the portal carries is a separate body and is left out: the
Planning Commission, the Wastewater Agency, the Personnel Policy Board, the
Board of Adjustment, the Board of Corrections, the Leasing Corporation and the
Veterans Service Committee.

Meeting *types* are read off the name rather than the TypeId, because the two
do not line up: four equalization meetings sit under the legacy board type, and
that same type also carries budget hearings, retreats and special meetings.

Cancellations stay in the feed with "NO MEETING" in the name -- 19 of them
inside the apex types, plus a dedicated type for 37 more -- and are dropped.
Two rows are literally titled "test".

One thing an editor should know, and it is not a bug here. The board meets
every Tuesday at 3pm, but the portal lists only some of those Tuesdays: as of
2026-09-11 it had Sep 15, 22 and 29, then Oct 20, then Dec 1, and nothing in
November. The gaps are not a publication lag -- the feed happily carries
unpublished meetings -- those Tuesdays simply have no record yet. So this
agency's forward list is thinner than its actual cadence.
"""

import logging
import re
from datetime import date, datetime, timedelta

import requests

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting
from ..sources import civicweb

log = logging.getLogger(__name__)

BASE_URL = "https://sarpy.civicweb.net"
MEETING_URL = f"{BASE_URL}/Portal/MeetingInformation.aspx?Id={{id}}"
TIMEOUT = 30
USER_AGENT = "flatwater-agenda-scraper/1.0"

DAYS_BACK = 7
DAYS_FORWARD = 400
# The archive starts in 2019; asking from further back costs nothing and means
# --since can reach all of it.
ARCHIVE_START = date(2015, 1, 1)

# The board's own meeting types. 30 is the legacy one (nothing after
# 2025-12-09), 10 replaced it, and 16 is the Board of Equalization.
APEX_TYPES = frozenset({10, 30, 16})

# Read off the name, not the TypeId -- see the module docstring. First hit wins.
NAME_TYPES = (
    ("HEARING", re.compile(r"(?i)equalization|hearing")),
    ("WORKSHOP", re.compile(r"(?i)retreat|work\s*session|strategic planning|sarpy 101")),
    ("SPECIAL", re.compile(r"(?i)special")),
)

TEST_RE = re.compile(r"(?i)^test$")

# The feed names the room and street but never the town, so add it for the one
# address we know. Anywhere else -- Bellevue University, Werner Park -- is left
# as the portal wrote it rather than given a town it may not be in.
BOARDROOM_RE = re.compile(r"(?i)^county boardroom, 1210 golden gate")
BOARDROOM = "County Boardroom, 1210 Golden Gate Drive, Papillion, NE 68046"
# Two board retreats carry this instead of a place.
PLACEHOLDER_LOCATIONS = {"tbd", "n/a", ""}

NAME_PREFIX = "Sarpy County"


def external_id_for(meeting_id: str) -> str:
    """The upsert key: the portal's own meeting id.

    Unlike the other agencies, this one reads a single source that covers both
    the archive and the schedule, so the id cannot change as a meeting moves
    between systems -- and unlike a key built from the start time, it survives
    a reschedule instead of stranding the old record.
    """
    return f"sarpy-{meeting_id}"


def canonical_name(name: str) -> str:
    """A readable name, without the portal's trailing date label.

    "Board Meetings - Aug 25 2026" is the portal labelling its own row; an
    editor wants "Sarpy County Board of Commissioners".
    """
    short, qualifier = civicweb.split_label(name)
    # The board's routine meetings are filed under a plural that reads oddly on
    # its own, and under two different names across the changeover of types.
    if re.fullmatch(r"(?i)board meetings?", short):
        short = "Board of Commissioners"
    if qualifier and qualifier.lower() not in short.lower():
        # "Board Meetings - Aug 25 2026 Budget" is the budget meeting, and an
        # editor loses that if only the date label is stripped.
        short = f"{short} ({qualifier})"
    if short.lower().startswith(NAME_PREFIX.lower()):
        return short
    return f"{NAME_PREFIX} {short}"


def classify(name: str) -> str:
    for meeting_type, pattern in NAME_TYPES:
        if pattern.search(name):
            return meeting_type
    return "REGULAR"


def clean_location(location: str) -> str | None:
    if location.strip().lower() in PLACEHOLDER_LOCATIONS:
        return None
    if BOARDROOM_RE.match(location.strip()):
        return BOARDROOM
    return " ".join(location.split()).strip(" ,") or None


class SarpyCountyCommissioners(BaseScraper):
    slug = "sarpy_county_commissioners"
    agency_id = "cmryfe0vv0007pl1ms6i9ijh0"
    agency_name = "Sarpy County Board of Commissioners"

    def fetch(self) -> list[Meeting]:
        url = civicweb.service_url(BASE_URL, self.since or ARCHIVE_START)
        try:
            response = requests.get(
                url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {url}: {exc}") from exc
        return self.parse(response.text)

    def _in_window(self, day: date, today: date | None = None) -> bool:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)
        return earliest <= day <= latest

    def parse(self, text: str, today: date | None = None) -> list[Meeting]:
        rows = civicweb.parse_meetings(text, BASE_URL)
        if not rows:
            raise ScraperError(
                f"No meetings at all from {BASE_URL} -- the service has probably "
                "changed."
            )

        meetings: list[Meeting] = []
        by_id: dict[str, Meeting] = {}

        for row in rows:
            if row.type_id not in APEX_TYPES:
                continue
            if row.cancelled or TEST_RE.match(civicweb.short_name(row.name)):
                continue
            if row.starts_at is None:
                self.skip(
                    f"{row.name!r} has no usable start time in the portal, so "
                    "there is no hour to send a reporter to"
                )
                continue
            if not self._in_window(row.starts_at.date(), today):
                continue

            external_id = external_id_for(row.id)
            if external_id in by_id:
                # The portal's ids are unique across all 658 rows; this cannot
                # happen without the service changing underneath us.
                self.skip(
                    f"{row.starts_at.date()}: {row.name!r} repeats meeting id "
                    f"{row.id}, which the portal is supposed to keep unique"
                )
                continue

            meeting = Meeting(
                name=canonical_name(row.name),
                starts_at=row.starts_at,
                external_id=external_id,
                location=clean_location(row.location),
                # The portal says whether the agenda is up. Until it is, the
                # meeting goes in without one and a later run fills it in.
                agenda_url=MEETING_URL.format(id=row.id) if row.published else None,
                meeting_type=classify(row.name),
            )
            meetings.append(meeting)
            by_id[external_id] = meeting

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
