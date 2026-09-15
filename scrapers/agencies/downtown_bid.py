"""Omaha Downtown Improvement District Association -- the board meetings page.

The platform calls this agency the Downtown Business Improvement District; the
body's own site is omahadowntown.org and it calls itself ODIDA. One page on it
carries everything this scraper reads, and it answers ordinary requests with no
bot challenge and no JavaScript.

**This is the first scraper here whose future dates are generated rather than
read.** The page publishes no forward schedule, only a standing rule --

    When:  Last Tuesday of each month at 5:30 PM
    Where: RDG Planning & Design, 1302 Howard St, Omaha, NE 68102
    Note:  There will be no meeting in July, Nov. or Dec.

-- plus a single "Next Meeting: September 29th" line and an archive of dated
rows for meetings already held, each with its topic and an agenda link. So the
forward dates come from applying the rule, and everything else on the page is
used to check it.

Checked on 2026-09-14 against the 13 archived meetings: 12 fell on the last
Tuesday of their month. The exception is January 2026, held Monday the 26th
rather than Tuesday the 27th -- a page typo or a real move, and there is no way
to tell which. Either way the rule is right almost always and not always, and
the agency's own wording ("the last Tuesday of *most* month") says the same.
The skip months hold too: no July in either year, and the 2025 archive says
"No November & December Meetings" in so many words. The "Next Meeting" line
agreed with the rule on the day this was written.

That one exception decides the key. A computed date is a field this scraper
supplies rather than reads, so it stays out of the `external_id` -- the rule in
`docs/api-notes.md` -- and the key is the *month*: `odida-2026-01`. When the
archive row for a month appears with the real date, the same record is
updated; a date-keyed record would have filed a second copy and stranded the
first. The 13 archived meetings fall in 13 distinct months, and the rule
produces at most one meeting a month by construction. A second archive row in
one month would be a special meeting, and is skipped with a warning rather
than overwriting the first.

The merge is one sentence: for each month in the window, the archive's date
wins if there is one, then the "Next Meeting" line, then the computed last
Tuesday. A computed meeting is logged and carries a note saying so, which the
next run clears once the agency has confirmed the date.

Three things are read off the page rather than hardcoded, each with a fallback
and a warning: the time and the place from the "When:" and "Where:" lines, and
the Zoom link, which goes in as the livestream. The rule's *shape* -- last
Tuesday, three skipped months -- is hardcoded, and the run warns if the page's
wording of it changes, because a silent constant is how a new November meeting
would go unnoticed.

The agenda links are Adobe Acrobat share pages with opaque ids, not PDFs. They
are not opened; there is nothing in them this page does not already say. Each
is submitted as the agency's canonical link, matched to its month from the
archive row's own text.
"""

import logging
import re
from calendar import monthrange
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperError
from ..meeting import CENTRAL, Meeting

log = logging.getLogger(__name__)

LISTING_URL = "https://omahadowntown.org/odida-board-meeting-information/"
TIMEOUT = 45
USER_AGENT = "flatwater-agenda-scraper/1.0"

DAYS_BACK = 7
DAYS_FORWARD = 400

NAME = "ODIDA Board of Directors Meeting"
TUESDAY = 1
SKIP_MONTHS = frozenset({7, 11, 12})

# What the page said on 2026-09-14, used only when the page no longer says it.
DEFAULT_TIME = (17, 30)
DEFAULT_PLACE = "RDG Planning & Design, 1302 Howard St, Omaha, NE 68102"

INFERRED_NOTE = (
    "Date inferred from the board's standing schedule (last Tuesday of the "
    "month); not yet confirmed by the agency."
)

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
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["Sept"] = 9

# "August 25, 2026", "Oct. 28, 2025"
ARCHIVE_DATE = re.compile(r"^([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")
# "September 29th"
NEXT_MEETING = re.compile(r"Next Meeting:\s*([A-Z][a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?")
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([APap])\.?\s*[Mm]\.?")
RULE_RE = re.compile(r"(?i)\blast\s+tuesday\b")
SKIP_RE = re.compile(r"(?i)no meeting in\s+july,?\s+nov\.?(?:ember)?\.?\s+or\s+dec")


def external_id_for(year: int, month: int) -> str:
    """The upsert key for the meeting in this month.

    The month, not the date. The date is computed from a rule that is right
    almost always -- 12 of 13 archived meetings -- and when the archive later
    supplies the real one the record must update, not duplicate.
    """
    return f"odida-{year}-{month:02d}"


def last_tuesday(year: int, month: int) -> date:
    last = date(year, month, monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - TUESDAY) % 7)


def _text(node) -> str:
    return " ".join(node.get_text(" ").split())


def _main(html: str):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.body or soup
    if "Meeting Details" not in main.get_text():
        raise ScraperError(
            f"No 'Meeting Details' section at {LISTING_URL} -- the page layout "
            "has probably changed."
        )
    return main


def parse_details(html: str) -> dict:
    """The standing rule as the page states it: time, place, Zoom link, and
    whether the wording of the rule and the skipped months still matches what
    this scraper hardcodes."""
    main = _main(html)
    details = {
        "time": None, "place": None, "livestream": None,
        "rule_matches": False, "skips_match": False,
    }
    for item in main.find_all("li"):
        label = item.find(["b", "strong"])
        if not label:
            continue
        key = _text(label).rstrip(":").lower()
        value = _text(item)[len(_text(label)):].strip(" :")
        if key == "when":
            details["rule_matches"] = bool(RULE_RE.search(value))
            clock = TIME_RE.search(value)
            if clock:
                hour = int(clock.group(1)) % 12
                if clock.group(3).upper() == "P":
                    hour += 12
                details["time"] = (hour, int(clock.group(2) or 0))
        elif key == "where":
            details["place"] = value or None
        elif key == "zoom option":
            link = item.find("a", href=True)
            details["livestream"] = link["href"] if link else None
        elif key == "note":
            details["skips_match"] = bool(SKIP_RE.search(value))
    return details


def parse_next_meeting(html: str, today: date) -> date | None:
    """The date in "Next Meeting: September 29th", given the year the page
    leaves off: the first such day on or after today.

    Read from the one element carrying the label, not the page's joined text:
    a label with no date after it would otherwise run on into the first
    archive row and announce last month's meeting for next year.
    """
    holder = next(
        (
            el for el in _main(html).find_all(["p", "li", "h4"])
            if _text(el).startswith("Next Meeting")
        ),
        None,
    )
    match = NEXT_MEETING.search(_text(holder)) if holder else None
    if not match or match.group(1) not in MONTHS:
        return None
    month, day = MONTHS[match.group(1)], int(match.group(2))
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def parse_archive(html: str) -> dict[tuple[int, int], dict]:
    """One entry per dated archive row: the real date, the topic, the agenda."""
    rows: dict[tuple[int, int], dict] = {}
    for item in _main(html).find_all("li"):
        text = _text(item)
        match = ARCHIVE_DATE.match(text)
        if not match or match.group(1) not in MONTHS:
            continue  # "No July Meeting", the details list, and so on
        try:
            day = date(int(match.group(3)), MONTHS[match.group(1)], int(match.group(2)))
        except ValueError:
            log.warning("unreadable archive date %r", text[:40])
            continue

        rest = text[match.end():].strip(" –-")
        topic = rest.split("|", 1)[0].strip() or None
        agenda = next(
            (a["href"] for a in item.find_all("a", href=True) if "agenda" in _text(a).lower()),
            None,
        )
        key = (day.year, day.month)
        if key in rows:
            log.warning(
                "two archived meetings in %s-%02d (%s and %s). The key holds one a "
                "month; the second is being skipped -- a special meeting would "
                "look like this.", day.year, day.month, rows[key]["day"], day,
            )
            continue
        rows[key] = {"day": day, "topic": topic, "agenda_url": agenda}
    return rows


class DowntownBid(BaseScraper):
    slug = "downtown_bid"
    agency_id = "cmryfsmz00003qf1mleq7pvp4"
    agency_name = "Downtown Business Improvement District"

    def fetch(self) -> list[Meeting]:
        try:
            response = requests.get(
                LISTING_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load {LISTING_URL}: {exc}") from exc
        return self.parse(response.text)

    def parse(self, html: str, today: date | None = None) -> list[Meeting]:
        today = today or datetime.now(CENTRAL).date()
        earliest = self.since or today - timedelta(days=DAYS_BACK)
        latest = self.until or today + timedelta(days=DAYS_FORWARD)

        details = parse_details(html)
        if not details["rule_matches"]:
            log.warning(
                "the page no longer says 'last Tuesday' under When:. The computed "
                "dates below assume it still is -- check %s by hand.", LISTING_URL,
            )
        if not details["skips_match"]:
            log.warning(
                "the page no longer says 'no meeting in July, Nov. or Dec.' Those "
                "months are still being skipped -- check %s by hand.", LISTING_URL,
            )
        time = details["time"] or DEFAULT_TIME
        if not details["time"]:
            log.warning("no time under When:; using %d:%02d", *DEFAULT_TIME)
        place = details["place"] or DEFAULT_PLACE
        if not details["place"]:
            log.warning("no place under Where:; using %s", DEFAULT_PLACE)

        archive = parse_archive(html)
        announced = parse_next_meeting(html, today)
        if announced and announced.month not in SKIP_MONTHS:
            expected = last_tuesday(announced.year, announced.month)
            if announced != expected:
                log.warning(
                    "the page announces the next meeting as %s but the last Tuesday "
                    "of that month is %s. Using the announced date; the rule may "
                    "have drifted.", announced, expected,
                )
        elif announced:
            log.warning(
                "the page announces a meeting on %s, in a month it says the board "
                "skips. Publishing it anyway -- the agency's statement wins.", announced,
            )

        meetings: list[Meeting] = []
        month = (earliest.year, earliest.month)
        while date(*month, 1) <= latest:
            year, mon = month
            key = (year, mon)
            row = archive.get(key)
            inferred = False
            if row:
                day = row["day"]
            elif announced and (announced.year, announced.month) == key:
                day = announced
            elif mon in SKIP_MONTHS:
                month = (year + (mon == 12), mon % 12 + 1)
                continue
            else:
                day = last_tuesday(year, mon)
                inferred = True

            if earliest <= day <= latest:
                if inferred and day >= today:
                    log.info("%s: inferred from the standing rule", day.isoformat())
                meetings.append(
                    Meeting(
                        name=NAME,
                        starts_at=datetime(day.year, day.month, day.day, *time, tzinfo=CENTRAL),
                        external_id=external_id_for(year, mon),
                        location=place,
                        agenda_url=row["agenda_url"] if row else None,
                        meeting_type="REGULAR",
                        details=(row["topic"] if row else None) or (INFERRED_NOTE if inferred else None),
                        livestream_url=details["livestream"],
                    )
                )
            month = (year + (mon == 12), mon % 12 + 1)

        meetings.sort(key=lambda m: m.starts_at)
        return meetings
