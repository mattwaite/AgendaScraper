"""Base class every agency scraper subclasses.

A new scraper implements exactly one method -- fetch() -- and returns Meeting
objects. Agency verification, deduplication and submission all live in the
runner, so no scraper contains API code.
"""

from abc import ABC, abstractmethod
from datetime import date

from .meeting import Meeting


class ScraperError(RuntimeError):
    """The source site could not be scraped -- layout changed, page down, etc."""


class BaseScraper(ABC):
    slug: str = ""
    agency_id: str = ""
    agency_name: str = ""

    @abstractmethod
    def fetch(self) -> list[Meeting]:
        """Scrape the source and return meetings, sorted by start time.

        Every Meeting needs an external_id that is stable for the life of that
        meeting -- the source system's own id. It is what lets the platform
        update a meeting that moves instead of filing a second copy of it.

        A meeting whose agenda has not been posted yet still belongs in the
        list; agenda_url is optional and a later run fills it in.
        """

    def __init__(self, since: date | None = None, until: date | None = None) -> None:
        # Optional window override from the CLI (--since / --until). When unset,
        # each scraper uses its own rolling default.
        self.since = since
        self.until = until
