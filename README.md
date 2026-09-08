# Flatwater Free Press Agenda Scrapers

Scrapes meeting schedules from Nebraska government bodies and pushes them to the
NE Civic Newsroom API, which editors use to assign reporters to meetings. One
scraper per government body; a shared client handles authentication and
deduplication so a scraper is only a parser.

Currently implemented: **Lincoln City Council**.

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then paste in the API key
```

`.env` holds `PLATFORM_API_KEY` (from the NE Civic Newsroom admin dashboard) and
`PLATFORM_API_BASE_URL`. It is gitignored — never commit the key.

## Usage

```bash
python3 -m scrapers.run --list                          # known scrapers
python3 -m scrapers.run lincoln_city_council --dry-run  # scrape, print payloads, POST nothing
python3 -m scrapers.run lincoln_city_council            # scrape and submit
```

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Scrape and dedup-check, print the exact JSON payloads, submit nothing |
| `--format csv\|json` with `--out FILE` | Write meetings to a file instead of submitting |
| `--since` / `--until` | Override the default window (7 days back → 400 days ahead), `YYYY-MM-DD` |
| `--limit N` | Keep only the earliest N meetings — for `--dry-run` and CSV/JSON output |
| `--allow-same-day` | Submit even when a same-named meeting already exists on that date |
| `-v` | Debug logging, including every skip and its reason |

Each run ends with a summary:

```
scraped 12 / new 3 / duplicate 9 / time-changed 0 / skipped-no-agenda 1 / failed 0
```

- `duplicate` — already on the platform; nothing was sent.
- `time-changed` — a meeting with this name already exists on this date at a
  different time. The platform has no update or delete endpoint, so submitting
  would leave two records that nobody can remove; the run warns and skips
  instead. Fix the time by hand, or pass `--allow-same-day` if the body really
  does meet twice that day.
- `skipped-no-agenda` — the API requires an agenda URL and this meeting's isn't
  posted yet. A later run picks it up.

Runs are safe to repeat: a second run immediately after the first reports
`new 0`.

Before changing anything about how meetings are named, read
[`docs/api-notes.md`](docs/api-notes.md) — meeting names are part of the
platform's deduplication fingerprint.

## Adding a scraper for another agency

1. Create `scrapers/agencies/<agency>.py` with a `BaseScraper` subclass that
   sets `slug`, `agency_id` (from `docs/api-notes.md`), and `agency_name`, and
   implements `fetch() -> list[Meeting]`.
2. Define the agency's meeting names as constants, one per meeting type.
   **Names must never change once submitted** — they are part of the API's
   deduplication fingerprint. See `docs/api-notes.md`.
3. Increment `self.skipped_no_agenda` for meetings with no agenda URL rather
   than submitting them.
4. Register the class in `scrapers/registry.py`.
5. Save a copy of the source page under `tests/fixtures/` and write parser tests
   against it.

No API code belongs in a scraper — the runner handles agency verification,
deduplication, and submission.

## Layout

| Path | What it is |
|---|---|
| `scrapers/meeting.py` | The `Meeting` record and its API payload |
| `scrapers/client.py` | The only code that talks HTTP to the platform |
| `scrapers/base.py` | `BaseScraper` — the contract a scraper implements |
| `scrapers/run.py` | CLI: dedup, submit, report |
| `scrapers/agencies/` | One module per government body |
| `docs/api-notes.md` | API behavior learned by probing; read before changing names |
| `tools/probe_fingerprint.py` | One-off experiment that established the dedup rule |

## Tests

```bash
python3 -m pytest
```

Parser tests run against saved HTML in `tests/fixtures/`, so they need no
network and no browser.
