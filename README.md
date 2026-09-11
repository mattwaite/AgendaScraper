# Flatwater Free Press Agenda Scrapers

Scrapes meeting schedules from Nebraska government bodies and pushes them to the
NE Civic Newsroom API, which editors use to assign reporters to meetings. One
scraper per government body; a shared client handles authentication and
deduplication so a scraper is only a parser.

Currently implemented:

| Agency | Slug | Source |
|---|---|---|
| Lincoln City Council | `lincoln_city_council` | Granicus portal + city meeting calendar |
| Lancaster County Board of Commissioners | `lancaster_county_commissioners` | CivicPlus Agenda Center + agenda PDFs + iCalendar feed |
| Lincoln Public Schools Board of Education | `lps_board_of_education` | SPARQ Data portal + district calendar API |
| Lincoln-Lancaster County Planning Commission | `planning_commission` | lincoln.ne.gov calendar + landing page |
| Omaha Public Schools Board of Education | `ops_board_of_education` | SPARQ Data portal + Finalsite district calendar |
| Omaha Inland Port Authority | `omaha_port_authority` | omahaipa.com meetings page + agenda PDFs |
| Sarpy County Board of Commissioners | `sarpy_county_commissioners` | CivicWeb portal meetings service |

All four Lincoln agencies are done, and Omaha has begun. Most of them publish
their forward schedule somewhere separate from their agendas — two OpenCities
calendar pages on lincoln.ne.gov, an iCalendar feed for the county, a
Thrillshare events API for Lincoln's schools and a Finalsite calendar for
Omaha's. Granicus, the Agenda Center and SPARQ each list a meeting only once
its agenda is posted, so each of those scrapers reads a second source for the
meetings still to come. Sarpy County and the Inland Port Authority are the
exceptions: each publishes one page carrying the archive and the schedule
together.

Each scraper publishes only its agency's apex body — the board or council
itself, not its committees and not a separate body that meets under the same
roof.

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
| `--dry-run` | Scrape and print the exact JSON payloads, submit nothing. Reports `would submit N` rather than guessing which the platform already holds |
| `--format csv\|json` with `--out FILE` | Write meetings to a file instead of submitting |
| `--since` / `--until` | Override the default window (7 days back → 400 days ahead), `YYYY-MM-DD` |
| `--limit N` | Keep only the earliest N meetings — for `--dry-run` and CSV/JSON output |
| `--stop-on-unexpected-create` | Abort if a meeting is created where an existing record was expected |
| `-v` | Debug logging, including every meeting and what the API did with it |

Each run ends with a summary:

```
scraped 12 / created 3 / adopted 0 / updated 1 / duplicate 8 / conflict 0 / failed 0
```

Every meeting is submitted on every run and the platform decides what to do with
it, keyed on the meeting's `externalId`:

- `created` — new to the platform.
- `adopted` — matched a meeting submitted before `externalId` existed, and
  claimed it. Only ever seen on a first run.
- `updated` — the meeting moved or was renamed; the existing record changed.
  `-v` prints which fields.
- `duplicate` — nothing changed; the most common outcome.
- `conflict` — a `409`, meaning this meeting's `externalId` has changed since it
  was filed. Needs a human; see the recovery section of
  [`docs/api-notes.md`](docs/api-notes.md).
- `stranded` — only shown when non-zero: the platform holds a record over these
  dates that this run did not produce, usually a meeting that moved. Reported,
  not deleted; see the recovery section of [`docs/api-notes.md`](docs/api-notes.md).
- `skipped` — only shown when non-zero: the source didn't give enough to build a
  meeting (Lancaster, for instance, skips a meeting whose agenda has no start
  time rather than inventing one). Each reason is logged as a warning.

Runs are safe to repeat: a second run immediately after the first reports
everything as `duplicate`.

Submitting takes about a second per meeting — the client throttles writes to
stay under the platform's requested rate.

### The first run against an agency

If an agency already has meetings on the platform from before `externalId`
existed, they get adopted rather than duplicated — but only if the scraper's
names still match what was submitted then. Prove it on a small window first:

```bash
python3 -m scrapers.run lincoln_city_council --since 2026-08-24 --until 2026-08-31 --stop-on-unexpected-create
```

`created` where you expected `adopted` means the names drifted; the run stops
before it can duplicate the rest.

## Adding a scraper for another agency

1. Create `scrapers/agencies/<agency>.py` with a `BaseScraper` subclass that
   sets `slug`, `agency_id` (from `docs/api-notes.md`), and `agency_name`, and
   implements `fetch() -> list[Meeting]`.
2. Give every `Meeting` an `external_id`. **This is the one value that must
   never drift** — it is how the platform recognizes a meeting it already has.
   Meeting names, by contrast, are free to change.

   If the agency needs **two** sources, do *not* use either system's own
   meeting id, however stable it looks — only one source has it, so the record
   would change identity the moment the meeting moved from the calendar to the
   agenda system, stranding the first copy. Build the id from what both sources
   can produce, and make it the narrowest key that still tells two meetings
   apart: a date is enough for the council, LPS needs the time too.

   If **one** source covers both the archive and the schedule, as Sarpy's
   does, use that source's own id: it survives a reschedule, where a key built
   from the start time files a new record and strands the old one. See
   "Choosing an externalId" in `docs/api-notes.md`.

   Name meetings from the source's own titles where those are specific (LPS
   distinguishes work sessions, budget hearings and named committees), and from
   a constant per meeting type where they aren't — Granicus and Agenda Center
   say only "City Council - Action" or "Board of Commissioners".
3. Publish only the agency's apex body — the board or council itself, not its
   committees and not a separate body that meets under the same roof. Each
   source marks that differently and some not at all, so enumerate the distinct
   titles first and record what your filter drops; `is_apex_board` in the OPS
   scraper and `NOT_THE_BOARD` in the LPS one are the two worked examples.
4. Include meetings whose agenda isn't posted yet; `agenda_url` is optional and
   a later run fills it in. The planning commission publishes its schedule
   months ahead of any agenda, which is the lead time editors actually want.
   If the source withholds something the API needs — Lancaster's listing has no
   meeting time — call `self.skip(reason)` rather than guessing a value; the run
   reports the count and logs each reason.

   Don't worry about a value the source only sometimes exposes: submitting
   replaces the whole record, so the runner puts back any optional field the
   platform already holds that this scrape didn't find.
5. Register the class in `scrapers/registry.py`.
6. Save a copy of the source page under `tests/fixtures/` and write parser tests
   against it.
7. Run `python3 -m tools.verify_upsert` (sandbox), then the first real run with
   `--stop-on-unexpected-create`.

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
| `scrapers/sources/` | Readers for a publishing platform, reusable across agencies (OpenCities, CivicPlus iCalendar, CivicWeb, SPARQ meeting portals, Thrillshare and Finalsite district calendars) |
| `docs/api-notes.md` | Verified API behavior; read before adding a scraper |
| `tools/verify_upsert.py` | Proves upsert works, against the sandbox agency |

## Tests

```bash
python3 -m pytest
```

Parser tests run against saved HTML in `tests/fixtures/`, so they need no
network and no browser.
