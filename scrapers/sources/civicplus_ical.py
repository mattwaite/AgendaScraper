"""Reading the iCalendar feeds CivicPlus sites publish.

A CivicPlus calendar exports itself at

    /common/modules/iCalendar/iCalendar.aspx?catID=<id>&feed=calendar

which is a better source than the calendar HTML: times come with a timezone,
each event carries a stable numeric UID, and there is no markup to break.

Only the handful of properties this project needs are read, so this is a
reader for these feeds rather than a general iCalendar parser.

One shape matters and is easy to miss. An event with a time looks like

    DTSTART;TZID=America/Chicago:20260915T090000

while an all-day event -- which is how these sites record something whose time
has not been set -- looks like

    DTSTART;VALUE=DATE:20260917

The second has no time in it at all, and `starts_at` is None for those. Do not
paper over that by assuming midnight or a usual hour: on Lancaster's feed those
are the staff meetings, whose event page says "Time: All Day" outright.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from ..meeting import CENTRAL

log = logging.getLogger(__name__)

EVENT_RE = re.compile(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", re.S)
EID_RE = re.compile(r"[?&]EID=(\d+)", re.I)


@dataclass(frozen=True)
class Event:
    uid: str
    summary: str
    day: date
    starts_at: datetime | None  # None for an all-day event
    location: str
    eid: str  # the site's own event id, from the description link

    @property
    def all_day(self) -> bool:
        return self.starts_at is None


def _unfold(text: str) -> str:
    """Undo iCalendar's line folding: a leading space continues the line."""
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))


def _property(block: str, name: str) -> tuple[str, str]:
    """Return (parameters, value) for a property, or ("", "")."""
    match = re.search(rf"^{name}([^:\n]*):(.*)$", block, re.M)
    if not match:
        return "", ""
    return match.group(1), match.group(2).strip()


def _unescape(value: str) -> str:
    return (
        value.replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\n", " ")
        .replace("\\N", " ")
        .strip()
    )


def parse_events(text: str) -> list[Event]:
    """Every VEVENT in the feed, in file order."""
    events = []
    for block in EVENT_RE.findall(_unfold(text)):
        params, raw_start = _property(block, "DTSTART")
        if not raw_start:
            continue

        starts_at, day = None, None
        try:
            if "VALUE=DATE" in params.upper():
                day = datetime.strptime(raw_start[:8], "%Y%m%d").date()
            else:
                naive = datetime.strptime(raw_start[:15], "%Y%m%dT%H%M%S")
                # Every agency in this project is in Central time, and these
                # feeds say so in their own TZID.
                starts_at = naive.replace(tzinfo=CENTRAL)
                day = starts_at.date()
        except ValueError:
            log.debug("skipping an event with an unreadable DTSTART: %r", raw_start)
            continue

        # The feed's own URL property points at the whole calendar, so the
        # event's own id comes from the EID in its description instead.
        description = _unescape(_property(block, "DESCRIPTION")[1])
        eid = EID_RE.search(description)
        events.append(
            Event(
                uid=_property(block, "UID")[1],
                summary=_unescape(_property(block, "SUMMARY")[1]),
                day=day,
                starts_at=starts_at,
                location=_unescape(_property(block, "LOCATION")[1]),
                eid=eid.group(1) if eid else "",
            )
        )
    return events
