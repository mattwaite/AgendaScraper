# Lincoln City Council Agenda Scraper

Scrapes the current year's City Council meeting schedule from Lincoln, Nebraska's public Granicus portal and writes a CSV with the date, time, location, and agenda PDF link for each meeting.

## Output

Running the script produces `council_meetings.csv` with four columns:

| Column | Description |
|---|---|
| `time` | Meeting start time (e.g. `3:00 PM`) |
| `date` | Meeting date in ISO 8601 format (e.g. `2026-01-05`) |
| `place` | Always `Council Chambers, County/City Building, 555 South 10th Street, Lincoln 68508` |
| `agenda_pdf_url` | Direct URL to the agenda PDF on the Granicus document server |

## Requirements

- Python 3.10 or later
- [Playwright](https://playwright.dev/python/) (drives a headless Chromium browser to load the Granicus page)
- [Beautiful Soup 4](https://www.crummy.com/software/BeautifulSoup/)

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/<your-username>/AgendaScraper.git
cd AgendaScraper
```

**2. Create and activate a virtual environment** (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

**3. Install Python dependencies**

```bash
pip install playwright beautifulsoup4
```

**4. Download the Playwright browser**

Playwright needs a copy of Chromium the first time you run it:

```bash
playwright install chromium
```

## Usage

```bash
python3 scrape_council.py
```

The script prints a confirmation and writes `council_meetings.csv` in the current directory.

## Notes

- The scraper targets the first year tab on the Granicus page, which is always the current calendar year. It will automatically pick up the correct year when run in future years.
- The agenda PDF URLs resolve directly — no login or further navigation required.
- The city's website (`lincoln.ne.gov`) blocks automated HTTP clients; the script fetches data from the underlying Granicus embed URL instead.
