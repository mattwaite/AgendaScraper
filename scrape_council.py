import asyncio
import csv
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

GRANICUS_URL = "https://lnklan.granicus.com/ViewPublisher.php?view_id=2"
PLACE = "Council Chambers, County/City Building, 555 South 10th Street, Lincoln 68508"
CURRENT_YEAR = datetime.now().year


def parse_date_time(raw_date: str) -> tuple[str, str]:
    """Return (iso_date, time_str) from Granicus date cell text."""
    # Collapse whitespace; typical value: "May 18, 2026 - 5:30 PM"
    normalized = " ".join(raw_date.split())
    # Strip leading/trailing dashes that sometimes appear
    normalized = normalized.strip("- ").strip()

    match = re.search(
        r"([A-Za-z]+\s+\d+,\s+\d{4})\s*-\s*(\d+:\d+\s*[APap][Mm])", normalized
    )
    if not match:
        return "", ""

    date_part = match.group(1).strip()
    time_part = match.group(2).strip().upper()

    try:
        dt = datetime.strptime(date_part, "%b %d, %Y")
        iso_date = dt.strftime("%Y-%m-%d")
    except ValueError:
        iso_date = ""

    return iso_date, time_part


async def scrape() -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(GRANICUS_URL, wait_until="networkidle", timeout=30000)
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "html.parser")

    # The first TabbedPanelsContent is the current year (2026 by default)
    panels = soup.find_all(class_="TabbedPanelsContent")
    if not panels:
        sys.exit("Could not find tabbed panel content on Granicus page.")

    # Confirm it's the right year by checking the tab labels
    tab_labels = [t.get_text(strip=True) for t in soup.find_all(class_="TabbedPanelsTab")]
    target_year = str(CURRENT_YEAR)
    if not tab_labels or tab_labels[0] != target_year:
        sys.exit(
            f"Expected first tab to be {target_year}, got: {tab_labels[:4]}. "
            "The page layout may have changed."
        )

    current_year_panel = panels[0]
    rows = current_year_panel.find_all("tr", class_="listingRow")

    meetings = []
    seen_dates = set()

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        date_raw = cells[1].get_text()
        iso_date, time_str = parse_date_time(date_raw)

        if not iso_date or not iso_date.startswith(target_year):
            continue

        if iso_date in seen_dates:
            continue
        seen_dates.add(iso_date)

        agenda_link = cells[2].find("a")
        if not agenda_link:
            continue
        href = agenda_link.get("href", "")
        if href.startswith("//"):
            href = "https:" + href

        meetings.append(
            {
                "time": time_str,
                "date": iso_date,
                "place": PLACE,
                "agenda_pdf_url": href,
            }
        )

    # Sort chronologically
    meetings.sort(key=lambda m: m["date"])
    return meetings


def write_csv(meetings: list[dict], path: str = "council_meetings.csv") -> None:
    fieldnames = ["time", "date", "place", "agenda_pdf_url"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(meetings)
    print(f"Wrote {len(meetings)} meetings to {path}")


if __name__ == "__main__":
    meetings = asyncio.run(scrape())
    write_csv(meetings)
