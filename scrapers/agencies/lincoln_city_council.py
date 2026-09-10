"""Lincoln City Council -- Granicus portal.

The portal has two kinds of table and this scraper reads both:

* "Upcoming Events" (Name | Date | Agenda | Packet) -- future meetings, the ones
  reporters actually need to be assigned to. It is often empty; Lincoln only
  publishes a meeting here once its agenda is posted, usually days ahead.
* "Available Archives", one tab per year (Name | Date | Action | Minutes) --
  meetings that have already happened.

Rows are keyed on the Granicus clip_id, which is unique per meeting, so two
meetings on the same day (a pre-council session and a regular session, say)
both survive.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting

log = logging.getLogger(__name__)

GRANICUS_URL = "https://lnklan.granicus.com/ViewPublisher.php?view_id=2"
PLACE = (
    "Council Chambers, County/City Building, 555 South 10th Street, Lincoln 68508"
)

# How far either side of today to keep meetings. Backwards is short because the
# assignment site cares about upcoming coverage, not history; override with
# --since for a backfill.
DAYS_BACK = 7
DAYS_FORWARD = 400

DATE_TIME_RE = re.compile(
    r"([A-Za-z]+\s+\d+,\s+\d{4})\s*-\s*(\d+:\d+\s*[APap][Mm])"
)
CLIP_ID_RE = re.compile(r"clip_id=(\d+)")

# One name per meeting type. Editing these is safe -- the API upserts on
# externalId, so a renamed meeting updates its record rather than doubling up.
TYPE_NAMES = {
    "REGULAR": "Lincoln City Council Regular Meeting",
    "SPECIAL": "Lincoln City Council Special Meeting",
    "HEARING": "Lincoln City Council Public Hearing",
    "WORKSHOP": "Lincoln City Council Work Session",
    "EMERGENCY": "Lincoln City Council Emergency Meeting",
}

# Matched against the Granicus row title, first hit wins.
TYPE_PATTERNS = (
    ("SPECIAL", re.compile(r"(?i)special")),
    ("EMERGENCY", re.compile(r"(?i)emergency")),
    ("HEARING", re.compile(r"(?i)hearing")),
    ("WORKSHOP", re.compile(r"(?i)pre-?council|work\s*session|committee|retreat")),
)


def parse_date_time(raw_date: str) -> datetime | None:
    """Turn a Granicus date cell into an aware datetime, or None if unparseable.

    Typical cell text, once whitespace is collapsed: "May 18, 2026 - 5:30 PM".
    """
    normalized = " ".join(raw_date.split()).strip("- ").strip()
    match = DATE_TIME_RE.search(normalized)
    if not match:
        return None
    try:
        naive = datetime.strptime(
            f"{match.group(1).strip()} {match.group(2).strip().upper()}",
            "%b %d, %Y %I:%M %p",
        )
    except ValueError:
        return None
    return naive.replace(tzinfo=CENTRAL)


def classify(title: str) -> str:
    for meeting_type, pattern in TYPE_PATTERNS:
        if pattern.search(title):
            return meeting_type
    return "REGULAR"


def _absolute(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    return href


class LincolnCityCouncil(BaseScraper):
    slug = "lincoln_city_council"
    agency_id = "cmryg8k2p0001s91mclkzpdnq"
    agency_name = "Lincoln City Council"

    def fetch(self) -> list[Meeting]:
        return self.parse(asyncio.run(self._load_html()))

    async def _load_html(self) -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(
                    GRANICUS_URL, wait_until="networkidle", timeout=60000
                )
                return await page.content()
            finally:
                await browser.close()

    def _external_id(self, row, starts_at: datetime) -> str:
        """A stable id for this meeting, for the API's upsert.

        Granicus puts clip_id in every link it emits for a row -- agenda,
        minutes, video -- so any one of them will do, and the id survives the
        meeting being rescheduled or renamed.

        A row with no links at all has no clip_id to borrow. That has never been
        observed on this portal, so the date-based fallback is a backstop rather
        than a code path to rely on: if the agenda later appears and brings a
        clip_id with it, the id changes, and the next run gets a 409 rather than
        quietly creating a second record.
        """
        for link in row.find_all("a"):
            if match := CLIP_ID_RE.search(link.get("href", "")):
                return f"lnk-clip-{match.group(1)}"

        fallback = f"lnk-date-{starts_at.date().isoformat()}"
        log.warning(
            "No clip_id on the %s row; falling back to %r. If Granicus adds a "
            "clip_id for this meeting later, expect a 409 that needs sorting "
            "out by hand -- see docs/api-notes.md.",
            starts_at.date().isoformat(),
            fallback,
        )
        return fallback

    def parse(
        self,
        html: str,
        today: date | None = None,
        days_back: int = DAYS_BACK,
        days_forward: int = DAYS_FORWARD,
    ) -> list[Meeting]:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=days_back)
        latest = self.until or today + timedelta(days=days_forward)

        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table", class_="listingTable")
        if not tables:
            raise ScraperError(
                f"No listingTable found at {GRANICUS_URL} -- the page layout has "
                "probably changed."
            )

        meetings: list[Meeting] = []
        seen_clips: set[str] = set()

        for row in [tr for table in tables for tr in table.find_all("tr")]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue  # header row, or the "no Upcoming Events" placeholder

            starts_at = parse_date_time(cells[1].get_text())
            if starts_at is None:
                continue
            if not earliest <= starts_at.date() <= latest:
                continue

            # The agenda link sits in a different column in the upcoming table
            # than in the archives, so search the whole row. It may be missing
            # entirely on a meeting whose agenda has not been posted; that is
            # fine, the API does not require one and a later run fills it in.
            agenda_href = ""
            for link in row.find_all("a"):
                href = link.get("href", "")
                if "AgendaViewer.php" in href:
                    agenda_href = _absolute(href)
                    break

            external_id = self._external_id(row, starts_at)
            if external_id in seen_clips:
                continue  # a meeting can appear in both upcoming and archives
            seen_clips.add(external_id)

            meeting_type = classify(" ".join(cells[0].get_text().split()))
            meetings.append(
                Meeting(
                    name=TYPE_NAMES[meeting_type],
                    starts_at=starts_at,
                    external_id=external_id,
                    location=PLACE,
                    agenda_url=agenda_href or None,
                    meeting_type=meeting_type,
                )
            )

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
