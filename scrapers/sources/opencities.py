"""Reading the event pages on lincoln.ne.gov.

The city runs OpenCities, and every board's "meeting" page carries its schedule
in the same shape: a list of dates whose times live in data attributes rather
than in the visible text.

    <li class="multi-date-item" data-start-year="2026" data-start-month="09"
        data-start-day="16" data-start-hour="13" data-start-mins="00">
      Wednesday, September 16, 2026 | 01:00 PM - 04:30 PM
    </li>

These pages are the only Lincoln source that publishes meetings months before
their agendas exist, which is the lead time an editor needs. They need a real
browser -- plain HTTP gets an edge-server 403 -- but show no bot challenge.

Used by the planning commission and city council scrapers.
"""

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..base import ScraperError
from ..meeting import CENTRAL

log = logging.getLogger(__name__)


def parse_event_dates(html: str, source: str) -> list[datetime]:
    """Every meeting start time on an OpenCities event page, sorted.

    `source` names the page in the error raised when nothing parses, so a
    layout change says which scraper to go and look at.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li.multi-date-item")
    if not items:
        raise ScraperError(
            f"No meeting dates found at {source} -- the page layout has "
            "probably changed."
        )

    starts = []
    for item in items:
        try:
            starts.append(
                datetime(
                    int(item["data-start-year"]),
                    int(item["data-start-month"]),
                    int(item["data-start-day"]),
                    int(item["data-start-hour"]),
                    int(item["data-start-mins"]),
                    tzinfo=CENTRAL,
                )
            )
        except (KeyError, ValueError) as exc:
            log.debug("skipping a calendar entry we could not read: %s", exc)
    return sorted(set(starts))


def parse_location(html: str, pattern: str) -> str | None:
    """The address block on an event page, matched by a per-page pattern.

    Patterns must tolerate the dots in "555 S. 10th Street" -- an address is
    not a sentence, so anything anchored on `[^.]` stops halfway through it.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text().split())
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    return " ".join(match.group(1).split()).strip(" ,")
