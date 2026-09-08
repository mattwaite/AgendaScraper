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
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting

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

# Meeting names are constant per (agency, meeting type) -- the date lives only in
# dateTime. Changing these strings risks duplicating every meeting if the API's
# importFingerprint covers `name`; see docs/api-notes.md.
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

            # Find the agenda link anywhere in the row: it sits in a different
            # column in the upcoming table than in the archive tables.
            agenda_href = ""
            for link in row.find_all("a"):
                href = link.get("href", "")
                if "AgendaViewer.php" in href:
                    agenda_href = _absolute(href)
                    break

            if not agenda_href:
                # The API requires agendaUrl, so this meeting cannot be submitted
                # yet. A later run picks it up once Lincoln posts the agenda.
                self.skipped_no_agenda += 1
                continue

            clip_match = CLIP_ID_RE.search(agenda_href)
            clip_id = clip_match.group(1) if clip_match else agenda_href
            if clip_id in seen_clips:
                continue  # a meeting can appear in both upcoming and archives
            seen_clips.add(clip_id)

            meeting_type = classify(" ".join(cells[0].get_text().split()))
            meetings.append(
                Meeting(
                    name=TYPE_NAMES[meeting_type],
                    starts_at=starts_at,
                    location=PLACE,
                    agenda_url=agenda_href,
                    meeting_type=meeting_type,
                    source_id=clip_id,
                )
            )

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
