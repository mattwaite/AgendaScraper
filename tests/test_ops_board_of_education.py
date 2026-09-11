import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from scrapers.agencies.ops_board_of_education import (
    OpsBoardOfEducation,
    best_title,
    canonical_name,
    external_id_for,
)
from scrapers.base import ScraperError
from scrapers.sources.sparq import parse_listing

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "ops_sparq_meetings.html").read_text()
CALENDAR = (FIXTURES / "ops_finalsite_september.html").read_text()
NO_CALENDAR = "<html><body></body></html>"

# The fixtures are fixed in time; pinning the window keeps these tests from
# depending on how far away today happens to be.
WIDE = dict(since=date(2011, 1, 1), until=date(2027, 12, 31))


def entry_for(meeting_id):
    return next(e for e in parse_listing(LISTING) if e["meeting_id"] == meeting_id)


def drop_row(meeting_id):
    """The listing with one meeting's row removed, as if SPARQ had not
    published that agenda yet."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(LISTING, "html.parser")
    for row in soup.find_all("tr"):
        if f"meeting={meeting_id}" in str(row):
            row.decompose()
    return str(soup)


def fetch_with(listing=LISTING, calendar=NO_CALENDAR, **kwargs):
    """Run fetch() with both HTTP calls stubbed out.

    Every month of the calendar returns the same fixture; the scraper
    deduplicates on the occurrence id, which is what a real month overlap
    produces too.
    """
    scraper = OpsBoardOfEducation(**kwargs)

    def get(url, **_):
        response = Mock()
        response.raise_for_status = Mock()
        response.text = calendar if "ops.org" in url else listing
        return response

    session = MagicMock()
    session.get = Mock(side_effect=get)
    with patch(
        "scrapers.agencies.ops_board_of_education.requests.Session",
        return_value=session,
    ):
        return scraper, scraper.fetch()


# --- the shared SPARQ parser on this district --------------------------------


def test_the_shared_sparq_parser_reads_this_portal_too():
    """Lincoln and Omaha run the same system at different organization
    numbers, which is why the parser lives in sources/."""
    assert len(parse_listing(LISTING)) == 11


def test_pulls_date_and_time_from_the_heading():
    assert entry_for("763716")["starts_at"].isoformat() == "2026-09-10T18:00:00-05:00"


def test_a_winter_meeting_is_central_standard_time():
    winter = [e for e in parse_listing(LISTING) if e["starts_at"].month == 11][0]
    assert winter["starts_at"].isoformat().endswith("-06:00")


def test_the_committee_name_is_captured_from_the_type_bracket():
    """OPS types a committee meeting "Unit (American Civics Committee)"."""
    unit = [e for e in parse_listing(LISTING) if e["source_type"] == "Unit"]
    assert unit, "the fixture should keep some Unit rows"
    assert any(e["type_detail"] == "American Civics Committee" for e in unit)


# --- the upsert key ----------------------------------------------------------


def test_the_key_is_the_date_and_the_time():
    assert external_id_for(entry_for("763716")["starts_at"]) == "ops-2026-09-10-1800"
    assert external_id_for(entry_for("763717")["starts_at"]) == "ops-2026-09-10-1700"


def test_a_hearing_and_the_meeting_after_it_both_survive():
    """The case date-only keying would lose: across 462 archived meetings no
    two shared a start time, but 51 of 409 dates carried more than one."""
    _, meetings = fetch_with(**WIDE)
    sep10 = sorted(
        (m for m in meetings if m.starts_at.date() == date(2026, 9, 10)),
        key=lambda m: m.starts_at,
    )
    assert [m.external_id for m in sep10] == [
        "ops-2026-09-10-1700",
        "ops-2026-09-10-1800",
    ]
    assert [m.meeting_type for m in sep10] == ["HEARING", "REGULAR"]


def test_every_key_is_distinct():
    _, meetings = fetch_with(**WIDE)
    assert len({m.external_id for m in meetings}) == len(meetings)


# --- building meetings -------------------------------------------------------


def test_maps_the_portals_meeting_types_onto_the_api_vocabulary():
    _, meetings = fetch_with(**WIDE)
    by_id = {m.external_id: m.meeting_type for m in meetings}
    assert by_id["ops-2026-09-10-1800"] == "REGULAR"
    assert by_id["ops-2026-09-10-1700"] == "HEARING"
    assert by_id["ops-2026-07-20-1800"] == "WORKSHOP"  # "Working"
    assert by_id["ops-2024-09-20-1700"] == "SPECIAL"


def test_a_committee_is_a_workshop():
    """OPS calls it "Unit", which no other district in this project uses."""
    _, meetings = fetch_with(**WIDE)
    committees = [m for m in meetings if "Civics" in m.name]
    assert committees
    assert all(m.meeting_type == "WORKSHOP" for m in committees)


def test_an_unmapped_type_is_kept_as_regular_with_a_warning(caplog):
    # The label and its value sit either side of a </b> in the markup.
    html = re.sub(r"(Meeting Type:\s*</b>\s*)Regular", r"\1Unheardof", LISTING, count=1)
    assert "Unheardof" in html, "the fixture's markup changed shape"

    _, meetings = fetch_with(html, **WIDE)
    assert len(meetings) == 11  # nothing dropped
    assert "unmapped meeting type" in caplog.text


def test_the_combined_board_and_esu_title_is_left_alone():
    """Unlike Lincoln, OPS names both bodies in one title, so there is no twin
    to fold and nothing to collapse."""
    _, meetings = fetch_with(**WIDE)
    regular = [m for m in meetings if m.external_id == "ops-2026-09-10-1800"][0]
    assert "Educational Service Unit 19" in regular.name
    assert regular.name.startswith("Omaha Public Schools Board of Education")


def test_the_district_is_not_named_twice():
    assert canonical_name("Board of Education Workshop") == (
        "Omaha Public Schools Board of Education Workshop"
    )
    assert canonical_name(
        "Omaha Public Schools Board of Education and Educational Service Unit 19"
    ) == "Omaha Public Schools Board of Education and Educational Service Unit 19"


def test_meetings_come_back_in_chronological_order():
    _, meetings = fetch_with(**WIDE)
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


def test_the_window_filters_by_date():
    _, meetings = fetch_with(since=date(2026, 7, 1), until=date(2026, 8, 31))
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 7, 20),
        date(2026, 8, 3),
        date(2026, 8, 24),
    ]


def test_a_dead_portal_raises_a_scraper_error():
    import requests

    session = MagicMock()
    session.get = Mock(side_effect=requests.ConnectionError("down"))
    with patch(
        "scrapers.agencies.ops_board_of_education.requests.Session",
        return_value=session,
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            OpsBoardOfEducation().fetch()


# --- naming a committee from its type bracket --------------------------------


def test_a_title_that_already_names_the_committee_is_kept():
    assert best_title(
        {"title": "American Civics Committee Meeting",
         "type_detail": "American Civics Committee"}
    ) == "American Civics Committee Meeting"


def test_a_bracket_that_corrects_a_typo_in_the_title_wins():
    """This row reads "American Committee Meeting" -- the bracket supplies the
    word the title dropped."""
    assert best_title(
        {"title": "American Committee Meeting",
         "type_detail": "American Civics Committee"}
    ) == "American Civics Committee"


def test_a_bracket_beats_a_title_that_says_nothing():
    assert best_title(
        {"title": "Committee Meeting", "type_detail": "American Civics Committee"}
    ) == "American Civics Committee"


def test_a_fuller_title_survives_a_shorter_bracket():
    assert best_title(
        {"title": "Ad Hoc Student Discipline Hearing Committee Meeting",
         "type_detail": "Ad Hoc Student Discipline Hearing"}
    ) == "Ad Hoc Student Discipline Hearing Committee Meeting"


def test_an_empty_bracket_changes_nothing():
    assert best_title({"title": "Board of Education Workshop", "type_detail": ""}) == (
        "Board of Education Workshop"
    )


def test_the_typo_row_reaches_the_platform_corrected():
    _, meetings = fetch_with(**WIDE)
    names = {m.starts_at.date(): m.name for m in meetings}
    assert names[date(2025, 3, 24)] == "Omaha Public Schools American Civics Committee"


# --- the district calendar ---------------------------------------------------


def test_the_calendar_supplies_meetings_sparq_has_not_published_yet():
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    future = [m for m in meetings if m.starts_at.date() > date(2026, 9, 10)]
    assert [m.external_id for m in future] == [
        "ops-2026-09-21-1800",
        "ops-2026-09-28-1800",
    ]
    assert all(m.agenda_url is None for m in future)


def test_sparq_wins_where_both_sources_describe_the_same_slot():
    """Both sources carry Sep 10 at 5:00 and 6:00. SPARQ has the agenda, so its
    record stands and the calendar adds nothing."""
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    sep10 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 10)]
    assert len(sep10) == 2
    assert all(m.agenda_url is not None for m in sep10)


def test_a_calendar_meeting_sparq_lacks_is_added_even_on_a_date_sparq_covers():
    """The reason this scraper reconciles on the whole slot rather than the
    date, unlike the Lincoln schools one. If SPARQ had published only the 6:00
    meeting, the 5:00 budget hearing must still come through."""
    thinned = drop_row("763717")  # the 5:00 budget hearing
    assert len(parse_listing(thinned)) == len(parse_listing(LISTING)) - 1
    _, meetings = fetch_with(thinned, calendar=CALENDAR, **WIDE)
    sep10 = sorted(
        (m for m in meetings if m.starts_at.date() == date(2026, 9, 10)),
        key=lambda m: m.starts_at,
    )
    assert [m.external_id for m in sep10] == [
        "ops-2026-09-10-1700",
        "ops-2026-09-10-1800",
    ]
    # the 5:00 one now comes from the calendar, so it has no agenda yet
    assert sep10[0].agenda_url is None
    assert sep10[1].agenda_url is not None


def test_school_events_are_left_off():
    """The same calendar carries open houses, graduations and days off."""
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    assert not [m for m in meetings if "Open House" in m.name]


def test_a_committee_on_the_calendar_is_caught_without_the_word_board():
    """"American Civics Committee" is a board committee whose calendar title
    says nothing about a board -- the "committee" half of the filter is what
    catches it."""
    html = CALENDAR.replace("Board Workshop", "American Civics Committee")
    _, meetings = fetch_with(calendar=html, **WIDE)
    assert [m for m in meetings if m.external_id == "ops-2026-09-28-1800"]


def test_an_all_day_calendar_entry_is_skipped_not_given_a_midnight():
    html = CALENDAR.replace("fsStartTime", "fsNotATime")
    scraper, meetings = fetch_with(calendar=html, **WIDE)
    assert not [m for m in meetings if m.starts_at.date() == date(2026, 9, 21)]
    assert any("no start time" in reason for reason in scraper.skipped)


def test_a_calendar_with_no_board_events_raises_rather_than_going_quiet():
    """Losing the board's events would otherwise look exactly like "no future
    meetings", which is the state this second source exists to fix."""
    html = CALENDAR.replace("Board", "Chess Club").replace("Committee", "Chess Club")
    with pytest.raises(ScraperError, match="calendar element"):
        fetch_with(calendar=html, **WIDE)


def test_an_empty_calendar_is_not_an_error():
    """A month with nothing in it is ordinary; only losing the board's events
    from a calendar that has some is the alarming case."""
    _, meetings = fetch_with(calendar=NO_CALENDAR, **WIDE)
    assert meetings
