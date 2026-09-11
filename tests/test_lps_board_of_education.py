import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from scrapers.agencies.lps_board_of_education import (
    LpsBoardOfEducation,
    canonical_name,
    clean_venue,
    external_id_for,
)
from scrapers.agencies.lps_board_of_education import NOT_THE_BOARD
from scrapers.base import ScraperError
from scrapers.sources.sparq import parse_listing

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "lps_sparq_meetings.html").read_text()
CALENDAR = json.loads((FIXTURES / "lps_thrillshare_events.json").read_text())
NO_CALENDAR = {"events": [], "meta": {"links": {"next": None}}}

# The calendar fixture runs to May 2027; pinning `until` keeps these tests from
# depending on how far away today happens to be.
WIDE = dict(since=date(2011, 1, 1), until=date(2027, 12, 31))


def entry_for(meeting_id):
    return next(e for e in parse_listing(LISTING) if e["meeting_id"] == meeting_id)


def fetch_with(html=LISTING, calendar=NO_CALENDAR, **kwargs):
    """Run fetch() with both HTTP calls stubbed out."""
    scraper = LpsBoardOfEducation(**kwargs)

    def get(url, **_):
        response = Mock()
        response.raise_for_status = Mock()
        if "thrillshare" in url:
            response.json = Mock(return_value=calendar)
        else:
            response.text = html
        return response

    session = MagicMock()
    session.get = Mock(side_effect=get)
    with patch(
        "scrapers.agencies.lps_board_of_education.requests.Session",
        return_value=session,
    ):
        return scraper, scraper.fetch()


# --- parsing the listing -----------------------------------------------------


def test_reads_every_row():
    assert len(parse_listing(LISTING)) == 10


def test_pulls_date_and_time_from_the_heading():
    """Unlike the other two sources, this one states the time outright."""
    entry = entry_for("763426")
    assert entry["starts_at"].isoformat() == "2026-09-08T18:00:00-05:00"


def test_a_winter_meeting_is_central_standard_time():
    assert entry_for("726978")["starts_at"].isoformat() == "2026-01-27T18:00:00-06:00"


def test_the_title_stops_before_the_meeting_type_label():
    assert entry_for("763426")["title"] == "Board of Education Regular Meeting"


def test_keeps_the_specific_title_of_a_committee_meeting():
    assert entry_for("736918")["title"].startswith(
        "Board of Education Wellness, American Civics, Multicultural Committee"
    )


def test_location_is_flattened_and_the_map_link_dropped():
    entry = entry_for("763426")
    assert entry["location"] == (
        "Steve Joel District Leadership Center, 5905 O Street, Lincoln, NE 68510"
    )
    assert "map it" not in entry["location"]


def test_agenda_url_is_absolute_and_keyed_by_meeting_id():
    entry = entry_for("763426")
    assert entry["agenda_url"] == (
        "https://meeting.sparqdata.com/Public/Agenda/89?meeting=763426"
    )


def test_a_changed_layout_raises_rather_than_returning_nothing():
    with pytest.raises(ScraperError, match="layout"):
        parse_listing("<html><body><p>no table</p></body></html>")


# --- the upsert key ----------------------------------------------------------


def test_the_key_is_the_date_and_the_time():
    """Date alone would collapse a 9 AM committee into the 6 PM regular meeting,
    which happens on 170 of the 489 dates this board has met on."""
    assert external_id_for(entry_for("763426")["starts_at"]) == "lps-2026-09-08-1800"
    assert external_id_for(entry_for("468109")["starts_at"]) == "lps-2021-05-05-0900"


def test_a_morning_session_and_an_evening_meeting_both_survive():
    """The case date-only keying would lose. The work session is moved here
    from 2024 so the two share a day: 9 AM and the 6 PM regular meeting."""
    html = LISTING.replace(
        "January 29, 2024 at 4:30 PM", "September 8, 2026 at 9:00 AM", 1
    )
    _, meetings = fetch_with(html, **WIDE)

    sep8 = sorted(
        (m for m in meetings if m.starts_at.date() == date(2026, 9, 8)),
        key=lambda m: m.starts_at,
    )
    assert [m.external_id for m in sep8] == [
        "lps-2026-09-08-0900",
        "lps-2026-09-08-1800",
    ]
    assert "Work Session" in sep8[0].name


# --- building meetings -------------------------------------------------------


def test_maps_the_portals_meeting_types_onto_the_api_vocabulary():
    _, meetings = fetch_with(**WIDE)
    by_id = {m.external_id: m.meeting_type for m in meetings}
    assert by_id["lps-2026-09-08-1800"] == "REGULAR"
    assert by_id["lps-2026-08-24-1800"] == "SPECIAL"
    assert by_id["lps-2012-08-14-1800"] == "HEARING"
    # Working, Staff and Finance all describe a session that isn't a formal
    # meeting, and the API has one word for that.
    assert by_id["lps-2024-01-29-1630"] == "WORKSHOP"
    assert by_id["lps-2026-06-25-1800"] == "WORKSHOP"


def test_an_unmapped_type_is_kept_as_regular_with_a_warning(caplog):
    # The label and its value sit either side of a </b> in the markup.
    html = re.sub(
        r"(Meeting Type:\s*</b>\s*)Regular", r"\1Unheardof", LISTING, count=1
    )
    assert "Unheardof" in html, "the fixture's markup changed shape"

    _, meetings = fetch_with(html, **WIDE)
    assert len(meetings) == 7  # the ESU twin folds in; committees are out
    assert "unmapped meeting type" in caplog.text
    assert [m for m in meetings if m.external_id == "lps-2026-09-08-1800"][
        0
    ].meeting_type == "REGULAR"


def test_names_carry_the_district_and_the_meetings_own_title():
    _, meetings = fetch_with(**WIDE)
    names = {m.external_id: m.name for m in meetings}
    assert names["lps-2026-09-08-1800"] == (
        "Lincoln Public Schools Board of Education Regular Meeting"
    )
    assert names["lps-2026-05-18-1800"] == (
        "Lincoln Public Schools Board of Education Organizational Meeting"
    )


def test_the_window_filters_by_date():
    _, meetings = fetch_with(since=date(2026, 1, 1), until=date(2026, 6, 30))
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 1, 27),
        date(2026, 5, 18),  # 2026-03-24 is a committee, and now left out
        date(2026, 6, 25),
    ]


def test_meetings_come_back_in_chronological_order():
    _, meetings = fetch_with(**WIDE)
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


def test_a_dead_portal_raises_a_scraper_error():
    import requests

    session = MagicMock()
    session.get = Mock(side_effect=requests.ConnectionError("down"))
    with patch(
        "scrapers.agencies.lps_board_of_education.requests.Session",
        return_value=session,
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            LpsBoardOfEducation().fetch()


# --- only the apex board ------------------------------------------------------


def test_committee_meetings_are_left_out():
    """Editors want the board itself, not its committees."""
    _, meetings = fetch_with(**WIDE)
    assert not [m for m in meetings if "Committee" in m.name]
    assert not [m for m in meetings if m.starts_at.date() == date(2026, 3, 24)]


def test_a_committee_that_never_says_committee_is_still_left_out():
    """One governmental relations row drops the word from its title."""
    assert NOT_THE_BOARD.search(
        "Board of Education Governmental Relations/Community Engagement"
    )


def test_a_separate_interlocal_board_is_left_out():
    """The Safe & Successful Kids Interlocal Board is written eight ways across
    the archive, once as plain "SSKI Board of Directors"."""
    for title in (
        "Lincoln Safe & Successful Kids Interlocal Board",
        "Safe and Successful Kids Interlocal (SSKI) Board",
        "SSKI Board of Directors",
        "Safe & Successful Kids Interlocal Board (SSKIB)",
    ):
        assert NOT_THE_BOARD.search(title), title


def test_the_board_itself_is_not_caught_by_the_filter():
    for title in (
        "Board of Education Regular Meeting",
        "Board of Education Special Meeting/Work Session",
        "Board of Education ESU 18 Regular Meeting",
        "Joint Public Meeting of School Board, City Council, County Board",
    ):
        assert not NOT_THE_BOARD.search(title), title


# --- folding ESU 18 ----------------------------------------------------------


def test_the_esu_twin_folds_into_the_meeting_it_follows(caplog):
    """ESU 18 is the same people, in the same room, on the same night -- SPARQ's
    own row says the meeting begins "as soon thereafter as the same may
    commence". Two records here means two reporters sent to one room."""
    import logging

    caplog.set_level(logging.INFO)
    _, meetings = fetch_with(**WIDE)

    sep8 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 8)]
    assert len(sep8) == 1
    assert sep8[0].external_id == "lps-2026-09-08-1800"
    assert "ESU" not in sep8[0].name  # the board's own title wins
    assert "folding" in caplog.text


def test_the_folded_meeting_keeps_its_agenda_link():
    _, meetings = fetch_with(**WIDE)
    sep8 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 8)][0]
    assert sep8.agenda_url.endswith("meeting=763426")


def test_the_board_title_wins_even_when_esu_is_listed_first():
    """Source order is not guaranteed, and the record should read the same
    either way."""
    board, esu = (
        "Board of Education Regular Meeting",
        "Board of Education ESU 18 Regular Meeting",
    )
    swapped = LISTING.replace(esu, "@@", 1).replace(board, esu, 1).replace("@@", board)
    assert swapped.index(esu) < swapped.index(board), "the rows did not swap"

    _, meetings = fetch_with(swapped, **WIDE)
    sep8 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 8)]
    assert len(sep8) == 1
    assert sep8[0].name == "Lincoln Public Schools Board of Education Regular Meeting"
    # and it carries the agenda from the row it took its name from
    assert sep8[0].agenda_url.endswith("meeting=764119")


def test_a_same_slot_collision_that_is_not_esu_is_skipped_with_a_warning():
    """Two genuinely different meetings in one slot cannot both be represented
    by a key made of the time, so the second is reported rather than dropped."""
    html = LISTING.replace("Board of Education ESU 18 Regular Meeting", "Budget Hearing")
    scraper, meetings = fetch_with(html, **WIDE)
    sep8 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 8)]
    assert len(sep8) == 1
    assert any("shares the slot" in reason for reason in scraper.skipped)


# --- the district calendar ---------------------------------------------------


def test_the_calendar_supplies_meetings_sparq_has_not_published_yet():
    """SPARQ stops at the last posted agenda; these are the ones an editor can
    actually assign a reporter to."""
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    future = [m for m in meetings if m.starts_at.date() > date(2026, 9, 8)]
    assert len(future) == 16
    assert min(m.starts_at.date() for m in future) == date(2026, 9, 22)
    assert max(m.starts_at.date() for m in future) == date(2027, 5, 25)
    assert all(m.agenda_url is None for m in future)


def test_calendar_meetings_use_the_same_key_scheme():
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    ids = {m.external_id for m in meetings}
    assert "lps-2026-09-22-1800" in ids


def test_a_winter_calendar_meeting_is_central_standard_time():
    """The API sends an offset per event rather than a fixed one. If a January
    meeting ever reads -05:00, every winter meeting is an hour out."""
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    jan = [m for m in meetings if m.starts_at.date() == date(2027, 1, 12)][0]
    assert jan.starts_at.isoformat() == "2027-01-12T18:00:00-06:00"
    oct_ = [m for m in meetings if m.starts_at.date() == date(2026, 10, 27)][0]
    assert oct_.starts_at.isoformat() == "2026-10-27T18:00:00-05:00"


def test_the_academic_calendar_is_left_out():
    """The same endpoint carries the school-year calendar -- quarter breaks and
    days off, not meetings."""
    _, meetings = fetch_with(calendar=CALENDAR, **WIDE)
    assert not [m for m in meetings if "quarter" in m.name.lower()]


def test_sparq_wins_where_both_sources_describe_a_meeting():
    """Reconciled on the date, not the time: if SPARQ has moved a meeting, the
    calendar's old hour must not become a second record."""
    moved = json.loads(json.dumps(CALENDAR))
    moved["events"][0]["start_at"] = "2026-09-08T19:30:00.000-05:00"
    scraper, meetings = fetch_with(calendar=moved, **WIDE)
    sep8 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 8)]
    assert len(sep8) == 1
    assert sep8[0].starts_at.hour == 18  # SPARQ's time
    assert sep8[0].agenda_url is not None


def test_a_time_disagreement_is_logged(caplog):
    import logging

    caplog.set_level(logging.INFO)
    moved = json.loads(json.dumps(CALENDAR))
    moved["events"][0]["start_at"] = "2026-09-08T19:30:00.000-05:00"
    fetch_with(calendar=moved, **WIDE)
    assert "keeping SPARQ" in caplog.text


def test_an_all_day_calendar_entry_is_skipped_not_given_a_midnight():
    """The API puts all-day events at local midnight, which is a placeholder,
    not an hour anyone meets at."""
    all_day = json.loads(json.dumps(CALENDAR))
    all_day["events"][0]["all_day"] = True
    scraper, meetings = fetch_with(calendar=all_day, **WIDE)
    assert not [m for m in meetings if m.starts_at.date() == date(2026, 9, 22)]
    assert any("no start time" in reason for reason in scraper.skipped)


def test_a_renamed_calendar_section_raises_rather_than_going_quiet():
    """Losing the section would otherwise look exactly like "no future
    meetings", which is the state this scraper exists to fix."""
    renamed = json.loads(json.dumps(CALENDAR))
    for event in renamed["events"]:
        event["custom_section_name"] = "Board Calendar (New)"
    with pytest.raises(ScraperError, match="renamed"):
        fetch_with(calendar=renamed, **WIDE)


def test_the_calendar_pages_until_it_runs_out():
    page2 = json.loads(json.dumps(CALENDAR))
    page2["meta"]["links"]["next"] = None
    page1 = json.loads(json.dumps(CALENDAR))
    page1["events"] = page1["events"][:1]
    page1["meta"]["links"]["next"] = "https://lincolnpublicschools.thrillshare.com/p2"

    pages = iter([page1, page2])
    scraper = LpsBoardOfEducation(**WIDE)

    def get(url, **_):
        response = Mock()
        response.raise_for_status = Mock()
        if "thrillshare" in url:
            response.json = Mock(return_value=next(pages))
        else:
            response.text = LISTING
        return response

    session = MagicMock()
    session.get = Mock(side_effect=get)
    with patch(
        "scrapers.agencies.lps_board_of_education.requests.Session",
        return_value=session,
    ):
        meetings = scraper.fetch()
    assert len([m for m in meetings if m.starts_at.date() > date(2026, 9, 8)]) == 16


# --- naming and addresses ----------------------------------------------------


def test_the_district_is_not_named_twice():
    """SPARQ says "Board of Education ...", the calendar says "Lincoln Board of
    Education ...". Both must come out the same, or SPARQ taking a record over
    from the calendar would rename it."""
    assert canonical_name("Board of Education Regular Meeting") == (
        "Lincoln Public Schools Board of Education Regular Meeting"
    )
    assert canonical_name("Lincoln Board of Education Regular Meeting") == (
        "Lincoln Public Schools Board of Education Regular Meeting"
    )


def test_a_combined_title_keeps_saying_both_boards_meet():
    assert canonical_name("Lincoln Board of Education and ESU18 Regular Meetings") == (
        "Lincoln Public Schools Board of Education and ESU18 Regular Meetings"
    )


def test_the_calendars_venue_is_punctuated_like_sparqs_address():
    assert clean_venue(
        "Steve Joel District Leadership Center - 5905 O St, Lincoln, NE 68510, USA"
    ) == "Steve Joel District Leadership Center, 5905 O St, Lincoln, NE 68510"


def test_a_venue_with_no_space_before_the_dash_is_still_punctuated():
    """Typed by hand, so both spellings turn up in the same feed."""
    assert clean_venue(
        "Steve Joel District Leadership Center- 5905 O St, Lincoln, NE 68510, USA"
    ) == "Steve Joel District Leadership Center, 5905 O St, Lincoln, NE 68510"


def test_a_hyphenated_name_is_not_broken_apart():
    assert clean_venue("Lincoln-Lancaster County Health Building") == (
        "Lincoln-Lancaster County Health Building"
    )
