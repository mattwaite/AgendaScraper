"""Lincoln-Lancaster County Planning Commission -- lincoln.ne.gov.

The first source in this project that publishes meetings **before** their
agendas exist. Granicus, the Agenda Center and SPARQ all list a meeting only
once its agenda is posted, usually days ahead; the commission publishes its
whole schedule -- it meets every other Wednesday at 1:00 p.m. -- months out.
That is the lead time editors actually need to assign a reporter, and it is
only usable because the API stopped requiring an agenda URL.

Two pages, because no single one has everything:

* The meeting calendar lists every date, past and future, in markup that
  carries the times as data attributes rather than prose:

      <li class="multi-date-item" data-start-year="2026" data-start-month="09"
          data-start-day="16" data-start-hour="13" data-start-mins="00">

* The commission's landing page carries a notice about the next meeting and a
  link to its agenda packet, named for the date it belongs to
  (`agenda-packets/2026/20260916.pdf`). Only the next meeting's agenda appears
  there, so most meetings are submitted without one and pick it up on a later
  run.

Both pages need a real browser -- plain HTTP gets an edge-server 403 -- which
is what Playwright is already here for.

`app.lincoln.ne.gov` hosts an older agenda archive, but it sits behind a
Cloudflare bot-verification interstitial. Working around that is not something
this project does, so those older agendas are left alone.

Meetings have no id in either page, so external_id is built from the date. A
meeting moved to a different day therefore reads as a new one, leaving the old
record behind for a human to delete. The commission publishes a fixed calendar
and cancels rather than moves, so that is a rare case, but it is the one thing
here that cannot be fixed automatically.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting
from ..sources import opencities

log = logging.getLogger(__name__)

BASE = "https://www.lincoln.ne.gov"
CALENDAR_URL = (
    f"{BASE}/City/Departments/Planning-Department/Boards-and-Commissions"
    "/Planning-Commission/Planning-Commission-Meeting-Calendar"
)
LANDING_URL = (
    f"{BASE}/City/Departments/Planning-Department/Boards-and-Commissions"
    "/Planning-Commission"
)

DEFAULT_PLACE = (
    "City Council Chambers, County-City Building, 555 South 10th Street, "
    "Lincoln 68508"
)

DAYS_BACK = 7
DAYS_FORWARD = 400

MEETING_NAME = "Lincoln-Lancaster County Planning Commission Regular Meeting"

# agenda-packets/2026/20260916.pdf -- the file is named for the meeting date.
AGENDA_RE = re.compile(r"/agenda-packets/\d{4}/(\d{4})(\d{2})(\d{2})\.pdf", re.I)
# Note the dots in "555 S. 10th Street" -- an address is not a sentence.
LOCATION_RE = r"(County/City Building.{0,140}?68508)"


def parse_calendar(html: str) -> list[datetime]:
    """Every meeting start time on the calendar page."""
    return opencities.parse_event_dates(html, CALENDAR_URL)


def parse_agenda_links(html: str) -> dict[date, str]:
    """Agenda packet URLs from the landing page, keyed by the date they cover."""
    soup = BeautifulSoup(html, "html.parser")
    links = {}
    for anchor in soup.find_all("a", href=AGENDA_RE):
        href = anchor["href"]
        year, month, day = AGENDA_RE.search(href).groups()
        try:
            when = date(int(year), int(month), int(day))
        except ValueError:
            continue
        links[when] = href if href.startswith("http") else BASE + href
    return links


def parse_location(html: str) -> str | None:
    """The address block on the calendar page, if it is still where it was."""
    return opencities.parse_location(html, LOCATION_RE)


class PlanningCommission(BaseScraper):
    slug = "planning_commission"
    agency_id = "cmrygfnxn0007s91mpsxoovti"
    agency_name = "Lincoln-Lancaster County Planning Commission"

    def fetch(self) -> list[Meeting]:
        calendar_html, landing_html = asyncio.run(self._load_pages())

        starts = parse_calendar(calendar_html)
        agendas = parse_agenda_links(landing_html)
        place = parse_location(calendar_html) or DEFAULT_PLACE

        today = datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)

        meetings = []
        for starts_at in starts:
            if not earliest <= starts_at.date() <= latest:
                continue
            meetings.append(
                Meeting(
                    name=MEETING_NAME,
                    starts_at=starts_at,
                    external_id=f"llcpc-{starts_at.date().isoformat()}",
                    location=place,
                    agenda_url=agendas.get(starts_at.date()),
                    meeting_type="REGULAR",
                )
            )
        return meetings

    async def _load_pages(self) -> tuple[str, str]:
        """Both pages in one browser session.

        `networkidle` never settles on this site, so wait for the document and
        then give its scripts a moment to fill the calendar in.
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                pages = []
                for url in (CALENDAR_URL, LANDING_URL):
                    try:
                        await page.goto(
                            url, wait_until="domcontentloaded", timeout=60000
                        )
                        await page.wait_for_timeout(4000)
                        pages.append(await page.content())
                    except Exception as exc:
                        raise ScraperError(f"Could not load {url}: {exc}") from exc
                return pages[0], pages[1]
            finally:
                await browser.close()
