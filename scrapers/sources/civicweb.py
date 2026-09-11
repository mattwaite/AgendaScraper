"""Reading the meetings service behind CivicWeb portals.

A CivicWeb portal -- `<agency>.civicweb.net` -- renders its meeting list from a
JSON service rather than from the page:

    /Services/MeetingsService.svc/meetings?from=YYYY-MM-DD&to=9999-12-31

It answers ordinary requests with no bot challenge, needs no browser, and is
the whole archive in one call: Sarpy's returns 658 meetings from 2019 to 2027.

    {"Id": 5256, "Name": "Board Meetings - Aug 25 2026",
     "MeetingDate": "2026-08-25", "MeetingDateTime": "2026-08-25 15:00",
     "MeetingTime": "03:00 PM", "TypeId": 30,
     "MeetingLocation": "County Boardroom, 1210 Golden Gate Drive",
     "Published": true}

The portal's own HTML is a poor substitute and should not be used instead:
`MeetingTypeList.aspx` shows only the three most recent meetings per type, and
`MeetingInformation.aspx?type=N` needs JavaScript to fill itself in.

Three things about the data are worth knowing.

`MeetingDateTime` is a naive local wall time with no offset -- "2026-08-25
15:00" -- so it is read as Central rather than converted from one.

`Published` marks whether the agenda has been posted. It is false on meetings
that are only scheduled, which is exactly the lead time this project wants, so
an unpublished meeting is a real meeting and not a draft to skip.

A cancelled meeting is *kept in the feed* with "NO MEETING" written into its
name, sometimes under a dedicated type and sometimes not. Callers have to
filter those out themselves; see the `cancelled` property.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from ..base import ScraperError
from ..meeting import CENTRAL

log = logging.getLogger(__name__)

SERVICE_PATH = "/Services/MeetingsService.svc/meetings"

# "Board Meetings - Aug 25 2026", "NO Board Meetings - Jan 19 2021 HOLIDAY"
CANCELLED_RE = re.compile(r"(?i)\bno\s+(?:board\s+)?meeting|\bcancell?ed\b")
# A trailing "- Aug 25 2026" is the portal's own labelling, not part of a name.
# Anything *after* that date can still be meaningful -- "Board Meetings - Aug 25
# 2026 Budget" is the budget meeting -- so the two are separated rather than
# both thrown away.
# The dash is optional: most rows read "... - Aug 25 2026", but some write the
# date straight onto the name ("Board Retreat/Strategic Planning Session Jan
# 20, 2026") and that date is just as much a label.
DATE_LABEL_RE = re.compile(
    r"\s*(?:[-–]\s*)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
    r"\s+\d{1,2},?\s+\d{4}"
)


@dataclass(frozen=True)
class Meeting:
    id: str
    name: str
    starts_at: datetime | None  # None when the feed gives no usable time
    location: str
    type_id: int
    published: bool

    @property
    def cancelled(self) -> bool:
        return bool(CANCELLED_RE.search(self.name))


def service_url(base: str, since, until="9999-12-31") -> str:
    since = since.isoformat() if hasattr(since, "isoformat") else since
    until = until.isoformat() if hasattr(until, "isoformat") else until
    return f"{base}{SERVICE_PATH}?from={since}&to={until}"


def split_label(name: str) -> tuple[str, str]:
    """Separate a meeting's name from the portal's "- Aug 25 2026" label.

    Returns (name, qualifier). The qualifier is whatever followed the date --
    usually nothing, sometimes a word that matters, as in "Board Meetings - Aug
    25 2026 Budget".
    """
    match = DATE_LABEL_RE.search(name)
    if not match:
        return " ".join(name.split()).strip(" -–"), ""
    base = " ".join(name[: match.start()].split()).strip(" -–")
    qualifier = " ".join(name[match.end() :].split()).strip(" -–")
    return base, qualifier


def short_name(name: str) -> str:
    """The meeting's name without the portal's trailing date label."""
    return split_label(name)[0]


def parse_meetings(text: str, source: str = "the CivicWeb meetings service") -> list[Meeting]:
    """Every meeting the service returned, in the order given."""
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise ScraperError(f"{source} did not return JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise ScraperError(
            f"{source} returned {type(rows).__name__}, not a list of meetings -- "
            "the service has probably changed."
        )

    meetings = []
    for row in rows:
        if row.get("Id") is None:
            continue

        starts_at = None
        stamp = (row.get("MeetingDateTime") or "").strip()
        if stamp:
            try:
                # No offset in the feed; these portals are local time.
                starts_at = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(
                    tzinfo=CENTRAL
                )
            except ValueError:
                log.debug("unreadable MeetingDateTime: %r", stamp)

        meetings.append(
            Meeting(
                id=str(row["Id"]),
                name=" ".join((row.get("Name") or "").split()),
                starts_at=starts_at,
                location=" ".join((row.get("MeetingLocation") or "").split()),
                type_id=row.get("TypeId") or 0,
                published=bool(row.get("Published")),
            )
        )
    return meetings
