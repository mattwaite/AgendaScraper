"""Reading the events API behind Thrillshare (Apptegy) district websites.

Districts running Thrillshare -- lps.org is one -- publish their calendars from

    https://<district>.thrillshare.com/api/v4/o/<org id>/cms/events?locale=en&page_no=1

which returns ``{"events": [...], "meta": {...}}``. It is the source to use.
The rendered calendar at ``www.lps.org/events`` is a FullCalendar widget behind
a "Client Challenge" interstitial that plain HTTP cannot pass, so reaching for
a browser there is the obvious wrong turn. This host answers ordinary requests
with JSON and no challenge at all, so no Playwright is needed.

Two things about the API are worth knowing before trusting it.

``meta`` advertises a ``sections`` list with a ``slug`` and a URL that looks
like a server-side filter. It is not one: passing ``slug=`` returns every event
anyway, both sections included. Filter on ``custom_section_name`` here instead.

An all-day event carries a ``start_at`` of local midnight, which is a
placeholder rather than a time anyone meets at. ``Event.starts_at`` is None for
those, the same distinction the CivicPlus iCalendar reader draws, so that a
made-up midnight never reaches an editor.

Recurring meetings need no special handling: the API expands a series into one
event per occurrence, each with its own ``id`` and ``start_at``, and only the
shared ``recurrency`` object says they belong together.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from ..meeting import CENTRAL

log = logging.getLogger(__name__)

PAGE_LIMIT = 25  # a stop for the paging loop; these calendars run to 2-3 pages


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    starts_at: datetime | None  # None for an all-day event
    section: str
    venue: str

    @property
    def all_day(self) -> bool:
        return self.starts_at is None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_events(payload: dict) -> list[Event]:
    """The events in one page of the API's response, in the order given."""
    events = []
    for raw in payload.get("events") or []:
        started = _text(raw.get("start_at"))
        if not started or raw.get("id") is None:
            continue

        starts_at = None
        if not raw.get("all_day"):
            try:
                # The offsets are real: these read -05:00 in October and
                # -06:00 in November, so the timestamp is converted rather
                # than rebuilt from the wall time.
                starts_at = datetime.fromisoformat(started).astimezone(CENTRAL)
            except ValueError:
                log.debug("skipping an event with an unreadable start_at: %r", started)
                continue

        events.append(
            Event(
                id=str(raw["id"]),
                title=" ".join(_text(raw.get("title")).split()),
                starts_at=starts_at,
                section=_text(raw.get("custom_section_name")),
                venue=" ".join(_text(raw.get("venue")).split()),
            )
        )
    return events


def next_page(payload: dict) -> str | None:
    """The URL of the following page, or None on the last one."""
    links = (payload.get("meta") or {}).get("links") or {}
    return _text(links.get("next")) or None
