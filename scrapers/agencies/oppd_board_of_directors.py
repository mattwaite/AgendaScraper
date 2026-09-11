"""OPPD Board of Directors -- the board meeting page on oppd.com.

Omaha Public Power District answers ordinary requests with no bot challenge and
no JavaScript, and one page carries everything: the remaining meetings of the
current year, and an archive of every meeting back to 2022. What it does not
carry in HTML is a *time* or a full date for anything but the next meeting --
those live in PDFs, which is why this scraper reads three sources rather than
the usual two. Each one covers a failure the other two cannot:

* **The schedule block** (`<h3>2026 Board Meetings Schedule</h3>` and the
  paragraph under it) is the only *live* source. It is the agency's current
  statement of what is still to come, and its own note says "Dates, times and
  locations are subject to change," so it is the only thing that shows a
  reschedule more than a week ahead. It gives days and months and no times.

* **The adopted schedule resolution** is the only source for *next* year, and
  the only per-meeting time. Article IV of OPPD's bylaws makes the board adopt
  the following year's schedule annually; it does so every September, and the
  resolution PDF lands in that month's archive block with a filename ending
  `{year}-board-meeting-schedule.pdf`. Exhibit A inside is a ruled two-column
  table -- committees on the left, the board on the right -- carrying a date,
  a place and a time for every meeting of the year.

* **The agenda PDF** is authoritative for the imminent meeting. Its fourth line
  reads `Thursday, September 17 at 5:00 P.M.`, and for months too old to appear
  in any surviving resolution it is the only place the day of the month exists
  at all: the archive's link text says "September", never "September 17".

Do not collapse this to two. Drop the resolution and next year never arrives,
because the schedule block is pruned as meetings pass -- in September 2026 it
listed four dates, not eleven. Drop the block and a reschedule goes unseen.
Drop the agenda and a backfill before 2023 has no dates.

**The times are worth trusting, and a hardcoded default is not.** The page says
"Meetings start at 5 p.m. unless otherwise noted," and the exception is real:
the board met at 6:00 p.m. on January 18, 2024. The adopted resolution had said
6:00 p.m. for that meeting all along. Checked on 2026-09-11 across the 41
months where an agenda PDF and a resolution table both describe the same
meeting: zero disagreements on either the date or the time, and all 52 agendas
on the page read "REGULAR BOARD MEETING".

The board meets at most once a month -- 45 resolution rows, one per month, and
one archive paragraph per month -- so the three sources are reconciled by month
rather than by date. Where they overlap the fresher source wins: the block
outranks the resolution, and an agenda outranks both for the time and the
place. A date the agenda disagrees with is logged loudly and *not* moved; a PDF
parse is the most fragile input here, and moving the key on one would strand
the record it replaced.

July is skipped in some years and kept in others, and the resolution writes it
as a `July - No Meeting` row with an empty board column. The annual Board
Governance Workshop sits in the same table with an empty board column too, so
one rule -- ignore rows whose board date cell is empty -- drops both. Workshops
are deliberately out of scope: they are not regular meetings.

One thing that could not be verified: what the schedule block looks like
between the last meeting of a year and the posting of the next year's block.
The Internet Archive rate-limited every attempt on 2026-09-11. It does not
matter much -- the resolution for the coming year is adopted in September, so a
January gap in the block is covered either way -- but an empty block has never
been observed and is only inferred.

Meetings are keyed on the date alone. The time is deliberately excluded: an
agenda can correct it, which makes it a mutable attribute, and keying on a
field this scraper also supplies a default for is what strands records.
"""

import logging
import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from io import BytesIO

import pdfplumber
import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting

log = logging.getLogger(__name__)

BASE_URL = "https://www.oppd.com"
LISTING_URL = f"{BASE_URL}/about/leadership/board-meeting-schedule-minutes/"
TIMEOUT = 45
USER_AGENT = "flatwater-agenda-scraper/1.0"

DAYS_BACK = 7
DAYS_FORWARD = 400

# An agenda is opened when it is the only source of a date, or when the meeting
# is close enough for a last-minute change of hour to matter. Everything else
# takes its time from the adopted resolution, which agreed with the agenda in
# all 41 months where both exist.
AGENDA_WINDOW = timedelta(days=60)

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        1,
    )
}

SCHEDULE_HEADING = re.compile(r"(?i)^(\d{4})\s+Board\s+Meetings?\s+Schedule")
ARCHIVE_HEADING = re.compile(r"(?i)^(\d{4})\s+Minutes and Supporting Materials")
SCHEDULE_PDF = re.compile(r"(\d{4})-board-meeting-schedule\.pdf$")
MONTH_DAY = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2})\b")

# "Thursday, September 17 at 5:00 P.M." since 2025; "Thursday, January 20, 2022
# at 5:00 P.M." before that. The year is optional because the newer agendas
# leave it off.
AGENDA_HEADER = re.compile(
    r"([A-Z][a-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\s+at\s+"
    r"(\d{1,2}):(\d{2})\s*([APap])\.?\s*[Mm]\.?"
)
TABLE_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([APap])")
FOOTNOTE = re.compile(r"^\*\*\s*(.+)$")

# The board's own room, written the way the agency writes it. The resolution
# table abbreviates this to a bare "Omaha Douglas Civic Center", which is not
# an address anyone can be sent to.
CIVIC_CENTER = (
    "Omaha-Douglas Civic Center, 2nd Floor Legislative Chamber, "
    "1819 Farnam St., Omaha, NE 68183"
)
CIVIC_CENTER_RE = re.compile(r"(?i)omaha[\s-]*douglas|civic center")
DEFAULT_TIME = (17, 0)

NAME = "OPPD Board of Directors Meeting"
SPECIAL_RE = re.compile(r"(?i)\bspecial\b")


def external_id_for(day: date) -> str:
    """The upsert key for the meeting on this date.

    The date alone. The time is left out on purpose: an agenda can correct it,
    and a key built from a field this scraper defaults would file a second
    record every time that correction arrived.
    """
    return f"oppd-{day.isoformat()}"


def _text(node) -> str:
    return " ".join(node.get_text(" ").split())


def _main(html: str):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("div.oMainContent") or soup.body or soup
    if not main.find("h3"):
        raise ScraperError(
            f"No headings found at {LISTING_URL} -- the page layout has "
            "probably changed."
        )
    return main


def parse_schedule_blocks(html: str) -> dict[int, list[date]]:
    """The live schedule: every `{year} Board Meetings Schedule` block.

    Every matching block is read, not just the first. A page carrying the tail
    of this year beside the whole of next year is the likeliest layout change
    here, and taking only one block would silently drop a year of meetings.

    The year comes from the heading and is never guessed -- the dates
    themselves say "September 17" with no year at all, and in late December a
    fallback to "this year" would file every meeting twelve months early.
    """
    blocks: dict[int, list[date]] = {}
    for heading in _main(html).find_all("h3"):
        match = SCHEDULE_HEADING.match(_text(heading))
        if not match:
            continue
        year = int(match.group(1))

        days: list[date] = []
        node = heading
        while True:
            node = node.find_next_sibling()
            if node is None or node.name != "p":
                break
            text = _text(node)
            if text.upper().startswith("NOTE"):
                break  # the "subject to change" footer closes the block
            for line in node.get_text("\n").split("\n"):
                found = MONTH_DAY.search(line.strip())
                if not found or found.group(1) not in MONTHS:
                    continue
                try:
                    days.append(
                        date(year, MONTHS[found.group(1)], int(found.group(2)))
                    )
                except ValueError:
                    log.warning(
                        "unreadable date %r in the %s schedule block", line, year
                    )
        blocks[year] = sorted(set(days))
    return blocks


def parse_archive(html: str) -> tuple[dict[tuple[int, int], str], dict[int, str]]:
    """What the archive blocks link to.

    Returns the agenda URL for each (year, month), and the URL of each adopted
    schedule resolution keyed by the year it *describes* -- the 2026 schedule
    is adopted in September 2025, so it is filed under the 2025 block.
    """
    agendas: dict[tuple[int, int], str] = {}
    resolutions: dict[int, str] = {}

    for heading in _main(html).find_all("h3"):
        match = ARCHIVE_HEADING.match(_text(heading))
        if not match:
            continue
        year = int(match.group(1))

        node = heading
        while True:
            node = node.find_next_sibling()
            if node is None or node.name == "h3":
                break
            lines = [l.strip() for l in node.get_text("\n").split("\n") if l.strip()]
            month = MONTHS.get(lines[0]) if lines else None
            for link in node.find_all("a", href=True):
                href = link["href"]
                url = BASE_URL + href if href.startswith("/") else href
                target = SCHEDULE_PDF.search(href)
                if target:
                    resolutions[int(target.group(1))] = url
                elif month and _text(link) == "Board Agenda":
                    # Scoped to this block's year: every year's archive has its
                    # own "September Board Agenda", and matching on the month
                    # alone would hand a 2026 meeting a 2022 agenda.
                    agendas.setdefault((year, month), url)
    return agendas, resolutions


def normalize_location(text: str | None) -> str | None:
    """A place a reporter can be sent to.

    The resolution table writes the board's room as a bare "Omaha Douglas
    Civic Center" with no street. Anything else -- 2023's meetings were held
    over Webex, with no street to give -- is passed through as written.
    """
    if not text:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    return CIVIC_CENTER if CIVIC_CENTER_RE.search(collapsed) else collapsed


def parse_schedule_resolution(data: bytes, year: int) -> dict[int, dict]:
    """The adopted schedule for one year, from Exhibit A's ruled table.

    The table sets committee meetings beside board meetings, so the board's
    columns are found by the "Board Meeting" label in the header row rather
    than by a fixed index. A row whose board date cell is empty is not a board
    meeting -- that is both the `July - No Meeting` row and the annual Board
    Governance Workshop, neither of which belongs here.
    """
    rows: dict[int, dict] = {}
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            pages = [(p.extract_table(), p.extract_text() or "") for p in pdf.pages]
    except Exception as exc:
        log.warning("could not read the %s schedule resolution (%s)", year, exc)
        return rows

    note = ""
    for table, text in pages:
        if not table or len(table) < 3:
            continue
        header = table[0]
        column = next(
            (
                i
                for i, cell in enumerate(header)
                if cell and "board meeting" in " ".join(cell.split()).lower()
            ),
            None,
        )
        if column is None:
            continue

        for line in text.splitlines():
            found = FOOTNOTE.match(line.strip())
            if found:
                note = found.group(1).strip()

        for row in table[2:]:
            if column + 2 >= len(row):
                continue
            cell = " ".join((row[column] or "").split())
            found = MONTH_DAY.match(cell)
            if not found or found.group(1) not in MONTHS:
                continue  # "July - No Meeting", the workshop, a blank
            time_cell = " ".join((row[column + 2] or "").split())
            clock = TABLE_TIME.search(time_cell)
            if not clock:
                log.warning("no time beside %s %s in the %s schedule", cell, year, year)
                continue
            hour = int(clock.group(1)) % 12 + (12 if clock.group(3).upper() == "P" else 0)
            try:
                day = date(year, MONTHS[found.group(1)], int(found.group(2)))
            except ValueError:
                log.warning("unreadable date %r in the %s schedule", cell, year)
                continue
            rows[day.month] = {
                "day": day,
                "time": (hour, int(clock.group(2))),
                "location": normalize_location(row[column + 1]),
                # "**" marks a meeting the board has flagged as tentative.
                "details": note if "**" in cell else None,
            }
        break
    return rows


def parse_agenda_header(text: str) -> dict | None:
    """Date, time, place and type from an agenda's opening lines.

    Only the first few lines are read. The agenda items below carry times of
    their own, and a regex over the whole document picks those up instead.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()][:8]
    found = next((AGENDA_HEADER.search(l) for l in lines if AGENDA_HEADER.search(l)), None)
    if not found or found.group(1) not in MONTHS:
        return None

    hour = int(found.group(4)) % 12 + (12 if found.group(6).upper() == "P" else 0)
    place = " ".join(
        line for line in lines if "Conducted" in line or "Omaha, NE" in line
    )
    place = re.sub(r"(?i)^.*?conducted (?:in person at |virtually via |)", "", place)
    return {
        "month": MONTHS[found.group(1)],
        "day": int(found.group(2)),
        "year": int(found.group(3)) if found.group(3) else None,
        "time": (hour, int(found.group(5))),
        "location": normalize_location(place),
        "type": "SPECIAL" if any(SPECIAL_RE.search(l) for l in lines[:5]) else "REGULAR",
    }


class OppdBoardOfDirectors(BaseScraper):
    slug = "oppd_board_of_directors"
    agency_id = "cmryfo71d0001qf1m109oj1uz"
    agency_name = "Omaha Public Power District"

    def fetch(self) -> list[Meeting]:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        try:
            return self.parse(self._get(session, LISTING_URL).text, session=session)
        finally:
            session.close()

    def _get(self, session, url):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {url}: {exc}") from exc
        return response

    def _window(self, today: date) -> tuple[date, date]:
        return (
            self.since or today - timedelta(days=DAYS_BACK),
            self.until or today + timedelta(days=DAYS_FORWARD),
        )

    def read_pdf(self, session, url: str) -> bytes | None:
        """A PDF's bytes, or None. A missing one is ordinary, not fatal."""
        if session is None:
            return None
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            log.info("could not read %s (%s)", url, exc)
            return None

    def read_agenda(self, session, url: str) -> dict | None:
        data = self.read_pdf(session, url)
        if data is None:
            return None
        try:
            with pdfplumber.open(BytesIO(data)) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception as exc:
            log.info("could not read the agenda at %s (%s)", url, exc)
            return None
        return parse_agenda_header(text)

    def parse(self, html: str, today: date | None = None, session=None) -> list[Meeting]:
        today = today or datetime.now(CENTRAL).date()
        earliest, latest = self._window(today)

        blocks = parse_schedule_blocks(html)
        agendas, resolutions = parse_archive(html)
        if not blocks and not resolutions and not agendas:
            raise ScraperError(
                f"Found no schedule, resolutions or archive at {LISTING_URL} -- "
                "the page layout has probably changed."
            )

        # The block, where one exists, is the agency's complete list of what is
        # still to come. From its first date onward it is the only authority:
        # letting the frozen resolution add dates above that line is how one
        # rescheduled meeting becomes two records in a single run.
        horizons = {year: days[0] for year, days in blocks.items() if days}

        chosen: dict[date, dict] = {}
        for year, days in blocks.items():
            for day in days:
                chosen[day] = {"day": day, "source": "schedule"}

        for year in sorted(resolutions):
            if date(year, 12, 31) < earliest or date(year, 1, 1) > latest:
                continue  # the whole year falls outside the window
            data = self.read_pdf(session, resolutions[year])
            if data is None:
                continue
            for entry in parse_schedule_resolution(data, year).values():
                day = entry["day"]
                if year in horizons and day >= horizons[year] and day not in chosen:
                    # Above the block's first date the block is the whole truth,
                    # so the frozen resolution may not *add* a meeting there --
                    # that is how one rescheduled meeting becomes two records.
                    # It may still supply the time for a date the block listed.
                    continue
                chosen.setdefault(day, {}).update(
                    {k: v for k, v in entry.items() if v is not None}
                )
                chosen[day].setdefault("source", "resolution")
                chosen[day]["day"] = day

        covered = {(d.year, d.month) for d in chosen}
        for (year, month), url in sorted(agendas.items()):
            last = date(year, month, monthrange(year, month)[1])
            if last < earliest or date(year, month, 1) > latest:
                continue  # no meeting of this month could land in the window

            # Only dates the run will actually submit are worth a download.
            dated = [
                d
                for d in chosen
                if (d.year, d.month) == (year, month) and earliest <= d <= latest
            ]
            needed = (year, month) not in covered
            near = any(abs((d - today).days) <= AGENDA_WINDOW.days for d in dated)
            if not (needed or near):
                continue

            header = self.read_agenda(session, url)
            if header is None:
                if needed:
                    log.info(
                        "%s-%02d is in the archive but no agenda could be read, "
                        "and nothing else gives it a date", year, month
                    )
                continue

            day = date(header["year"] or year, header["month"], header["day"])
            if dated and day not in dated:
                log.warning(
                    "the %s-%02d agenda says %s but the schedule says %s. Keeping "
                    "the schedule's date -- an agenda cannot move a meeting's key. "
                    "Check the page by hand.",
                    year, month, day.isoformat(),
                    ", ".join(d.isoformat() for d in sorted(dated)),
                )
                day = sorted(dated)[0]

            entry = chosen.setdefault(day, {"day": day, "source": "agenda"})
            entry["day"] = day
            entry["time"] = header["time"]
            entry["type"] = header["type"]
            if header["location"]:
                entry["location"] = header["location"]

        for (year, month), url in agendas.items():
            for day in list(chosen):
                if (day.year, day.month) == (year, month):
                    chosen[day].setdefault("agenda_url", url)

        meetings: list[Meeting] = []
        for day in sorted(chosen):
            if not earliest <= day <= latest:
                continue
            entry = chosen[day]
            hour, minute = entry.get("time") or DEFAULT_TIME
            if "time" not in entry:
                log.info(
                    "%s: no adopted time and no agenda; using the board's "
                    "published 5:00 PM", day.isoformat()
                )
            meetings.append(
                Meeting(
                    name=NAME,
                    starts_at=datetime(
                        day.year, day.month, day.day, hour, minute, tzinfo=CENTRAL
                    ),
                    external_id=external_id_for(day),
                    location=entry.get("location") or CIVIC_CENTER,
                    agenda_url=entry.get("agenda_url"),
                    meeting_type=entry.get("type", "REGULAR"),
                    details=entry.get("details"),
                )
            )

        if not [m for m in meetings if m.starts_at.date() >= today]:
            log.warning(
                "No future OPPD meetings from any of the three sources. The "
                "schedule block is pruned as meetings pass, so this is expected "
                "only if the next year's block and its adopted resolution are "
                "both missing -- check %s by hand.", LISTING_URL
            )

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
