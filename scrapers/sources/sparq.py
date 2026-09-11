"""Reading the meeting portals SPARQ Data hosts for school boards.

Each district gets a numbered organization:

    https://meeting.sparqdata.com/Public/Organization/<org id>

Lincoln Public Schools is 89, Omaha Public Schools is 120, and the markup is
identical between them -- the LPS parser read all 462 rows of the OPS portal
before a line of it was changed, which is why this lives here rather than in
either agency.

One plain HTML table, no JavaScript and no bot challenge. Every row carries
what the API wants: date and time, the meeting's own title, an explicit meeting
type, the address, and a link to the agenda keyed by a stable meeting id.

    September 8, 2026 at 6:00 PM - Board of Education Regular Meeting
    Meeting Type: Regular
    Steve Joel District Leadership Center, 5905 O Street, Lincoln, NE 68510
    Agenda -> /Public/Agenda/89?meeting=763426

These portals list a meeting only once its agenda is posted -- on both
districts the newest meeting was in the past when this was written -- so every
agency using this needs a second source for the schedule still to come.

The meeting type is sometimes qualified in brackets, which is how OPS names its
committees:

    Meeting Type: Unit (American Civics Committee)

`type_detail` carries that parenthetical. It is worth keeping: the row whose
type reads `Unit (American Civics Committee)` is titled "American Committee
Meeting" in the listing, a typo the parenthetical corrects.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..base import ScraperError
from ..meeting import CENTRAL

BASE_URL = "https://meeting.sparqdata.com"

# "September 8, 2026 at 6:00 PM - Board of Education Regular Meeting"
HEADING_RE = re.compile(
    r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(.+)"
)
MEETING_TYPE_RE = re.compile(r"Meeting Type:\s*(\w+)")
TYPE_DETAIL_RE = re.compile(r"Meeting Type:\s*\w+\s*\(([^)]*)\)")
MEETING_ID_RE = re.compile(r"[?&]meeting=(\d+)")


def listing_url(org_id: str | int) -> str:
    return f"{BASE_URL}/Public/Organization/{org_id}"


def clean_location(cell) -> str:
    """Flatten the address cell, dropping the "[ map it ]" link."""
    for link in cell.find_all("a"):
        link.decompose()
    parts = [
        " ".join(part.split())
        for part in cell.get_text(separator="\n").split("\n")
        if part.strip() and part.strip() not in "[]"
    ]
    return ", ".join(parts).replace(" ,", ",").strip(" ,")


def parse_listing(html: str, source: str = BASE_URL) -> list[dict]:
    """One entry per row of the meetings table.

    `source` names the portal in the error raised when nothing parses, so a
    layout change says which agency to go and look at.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        raise ScraperError(
            f"No meetings table at {source} -- the page layout has "
            "probably changed."
        )

    entries = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue  # header

        heading = " ".join(cells[0].get_text().split())
        match = HEADING_RE.search(heading)
        if not match:
            continue
        try:
            starts_at = datetime.strptime(
                f"{match.group(1)} {match.group(2)}", "%B %d, %Y %I:%M %p"
            ).replace(tzinfo=CENTRAL)
        except ValueError:
            continue

        # The title runs up to the "Meeting Type:" label that follows it.
        title = MEETING_TYPE_RE.split(match.group(3))[0].strip(" -–")
        type_match = MEETING_TYPE_RE.search(heading)
        detail_match = TYPE_DETAIL_RE.search(heading)

        link = cells[2].find("a", href=MEETING_ID_RE)
        if not link:
            continue
        meeting_id = MEETING_ID_RE.search(link["href"]).group(1)

        entries.append(
            {
                "starts_at": starts_at,
                "title": title,
                "source_type": type_match.group(1) if type_match else "",
                "type_detail": (detail_match.group(1).strip() if detail_match else ""),
                "meeting_id": meeting_id,
                "agenda_url": BASE_URL + link["href"]
                if link["href"].startswith("/")
                else link["href"],
                "location": clean_location(cells[1]),
            }
        )
    return entries
