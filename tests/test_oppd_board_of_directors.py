import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from bs4 import BeautifulSoup

from scrapers.agencies.oppd_board_of_directors import (
    CIVIC_CENTER,
    OppdBoardOfDirectors,
    external_id_for,
    normalize_location,
    parse_agenda_header,
    parse_archive,
    parse_schedule_blocks,
    parse_schedule_resolution,
)
from scrapers.base import ScraperError

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "oppd_board_meetings.html").read_text()
AGENDA = (FIXTURES / "oppd_agenda_september.pdf").read_bytes()
RESOLUTION = (FIXTURES / "oppd_schedule_resolution_2026.pdf").read_bytes()

# The fixture is fixed in time: the schedule block lists the four meetings left
# in 2026, and the archive holds 2026, 2025 and 2024.
TODAY = date(2026, 9, 11)


def fetch_with(listing=LISTING, today=TODAY, agenda=AGENDA, resolution=RESOLUTION, **kwargs):
    """Run parse() with every PDF served from the fixtures.

    Both schedule resolutions on the page return the 2026 one; the test that
    cares about which year a resolution describes stubs the parse instead.
    """
    scraper = OppdBoardOfDirectors(**kwargs)
    fetched = []

    def get(url, **_):
        fetched.append(url)
        response = Mock()
        response.raise_for_status = Mock()
        if "board-meeting-schedule.pdf" in url:
            if resolution is None:
                raise __import__("requests").HTTPError("404")
            response.content = resolution
        else:
            if agenda is None:
                raise __import__("requests").HTTPError("404")
            response.content = agenda
        return response

    session = MagicMock()
    session.get = Mock(side_effect=get)
    return scraper, scraper.parse(listing, today=today, session=session), fetched


def by_id(meetings):
    return {m.external_id: m for m in meetings}


def with_next_years_resolution(listing=LISTING):
    """The page as it looks once the board has adopted the following year.

    The real fixture was saved on 2026-09-11, six days before the meeting that
    adopts the 2027 schedule, so the link does not exist on it yet. It has
    appeared in the September block of every year since 2022.
    """
    soup = BeautifulSoup(listing, "html.parser")
    september = soup.find(
        "h3", string=re.compile("2026 Minutes and Supporting Materials")
    ).find_next_sibling("p")
    link = BeautifulSoup(
        '<a href="/media/399999/2026-9-sept-resolution-6800-2027-board-meeting'
        '-schedule.pdf">Resolution 6800</a>',
        "html.parser",
    )
    september.append(link)
    return str(soup)


def agenda_text(data=AGENDA):
    import pdfplumber
    from io import BytesIO

    with pdfplumber.open(BytesIO(data)) as pdf:
        return pdf.pages[0].extract_text() or ""


# --- the live schedule block -------------------------------------------------


def test_the_schedule_block_gives_the_remaining_meetings_of_the_year():
    assert parse_schedule_blocks(LISTING) == {
        2026: [date(2026, 9, 17), date(2026, 10, 15),
               date(2026, 11, 19), date(2026, 12, 17)]
    }


def test_the_year_comes_from_the_heading_not_from_today():
    """The dates themselves read "September 17" with no year. Falling back to
    the current year would file every meeting twelve months out in December."""
    listing = LISTING.replace("2026 Board Meetings Schedule", "2031 Board Meetings Schedule")
    assert parse_schedule_blocks(listing) == {
        2031: [date(2031, 9, 17), date(2031, 10, 15),
               date(2031, 11, 19), date(2031, 12, 17)]
    }


def test_every_schedule_block_is_read_not_just_the_first():
    """A page carrying the tail of this year beside the whole of next year is
    the likeliest layout change here, and taking one block would drop a year."""
    soup = BeautifulSoup(LISTING, "html.parser")
    block = soup.find("h3", string=re.compile("2026 Board Meetings Schedule"))
    extra = BeautifulSoup(
        "<h3>2027 Board Meetings Schedule</h3><p>January 21<br/>February 18</p>",
        "html.parser",
    )
    for node in list(extra.children):
        block.insert_before(node)

    blocks = parse_schedule_blocks(str(soup))
    assert set(blocks) == {2026, 2027}
    assert blocks[2027] == [date(2027, 1, 21), date(2027, 2, 18)]


def test_the_subject_to_change_note_is_not_read_as_dates():
    dates = parse_schedule_blocks(LISTING)[2026]
    assert all(d.year == 2026 for d in dates)
    assert len(dates) == 4


# --- the archive -------------------------------------------------------------


def test_the_archive_links_one_agenda_per_month():
    agendas, _ = parse_archive(LISTING)
    assert sorted(m for (y, m) in agendas if y == 2025) == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]


def test_july_is_absent_in_the_years_the_board_skipped_it():
    """The adopted schedule writes it as "July - No Meeting"."""
    agendas, _ = parse_archive(LISTING)
    assert (2025, 7) not in agendas
    assert (2026, 7) not in agendas


def test_an_agenda_is_matched_by_year_as_well_as_month():
    """Every archive block has its own "September Board Agenda". Matching on
    the month alone hands a 2026 meeting a 2024 agenda."""
    agendas, _ = parse_archive(LISTING)
    assert "/2026-9-sept-" in agendas[(2026, 9)]
    assert "/2025-9-sept-" in agendas[(2025, 9)]
    assert "/2024-9-sept-" in agendas[(2024, 9)]


def test_a_schedule_resolution_is_filed_under_the_year_it_describes():
    """The 2026 schedule is adopted in September 2025, so it sits in the 2025
    block -- keying it by the block's year would be off by one."""
    _, resolutions = parse_archive(LISTING)
    assert set(resolutions) == {2025, 2026}
    assert "2025-9-sept-resolution-6727-2026-board-meeting-schedule" in resolutions[2026]


def test_a_changed_layout_raises_rather_than_returning_nothing():
    with pytest.raises(ScraperError, match="layout"):
        parse_schedule_blocks("<html><body><p>nothing here</p></body></html>")


# --- the adopted schedule resolution -----------------------------------------


def test_the_resolution_gives_a_date_time_and_place_for_every_month():
    rows = parse_schedule_resolution(RESOLUTION, 2026)
    assert sorted(rows) == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
    assert rows[9]["day"] == date(2026, 9, 17)
    assert rows[9]["time"] == (17, 0)
    assert rows[9]["location"] == CIVIC_CENTER


def test_the_no_meeting_row_and_the_workshop_are_both_left_out():
    """One rule -- an empty board date cell -- drops the "July - No Meeting"
    row and the annual Board Governance Workshop. Workshops are not regular
    meetings and are deliberately out of scope."""
    rows = parse_schedule_resolution(RESOLUTION, 2026)
    assert 7 not in rows
    assert len(rows) == 11
    assert all(r["day"].weekday() == 3 for r in rows.values())  # Thursdays


def test_the_board_column_is_found_by_name_not_by_position():
    """Committee meetings sit in the same table, on the left. Reading the wrong
    column gives Tuesday committee meetings at 10 a.m."""
    rows = parse_schedule_resolution(RESOLUTION, 2026)
    assert rows[1]["day"] == date(2026, 1, 15)  # not the Jan 13 committee date
    assert rows[1]["time"] == (17, 0)  # not 10:00 a.m.


def test_an_unreadable_resolution_is_not_fatal():
    assert parse_schedule_resolution(b"not a pdf", 2026) == {}


# --- the agenda header -------------------------------------------------------


def test_the_agenda_gives_the_day_the_archive_link_does_not():
    """The link text says "September", never "September 17"."""
    header = parse_agenda_header(agenda_text())
    assert (header["month"], header["day"]) == (9, 17)
    assert header["time"] == (17, 0)
    assert header["type"] == "REGULAR"


def test_the_agenda_location_becomes_the_full_address():
    assert parse_agenda_header(agenda_text())["location"] == CIVIC_CENTER


def test_an_older_agenda_carries_its_year_inline():
    """Agendas through 2024 read "Thursday, January 20, 2022 at 5:00 P.M.";
    2025 onward drop the year."""
    header = parse_agenda_header(
        "BOARD OF DIRECTORS\nAgenda\nOPPD BOARD OF DIRECTORS\n"
        "REGULAR BOARD MEETING\nThursday, January 20, 2022 at 5:00 P.M.\n"
    )
    assert (header["year"], header["month"], header["day"]) == (2022, 1, 20)


def test_a_meeting_at_a_different_hour_is_read_from_the_agenda():
    """The board met at 6 p.m. on January 18, 2024, which is why "meetings
    start at 5 p.m. unless otherwise noted" cannot be hardcoded."""
    header = parse_agenda_header(
        "BOARD OF DIRECTORS\nAgenda\nOPPD BOARD OF DIRECTORS\n"
        "REGULAR BOARD MEETING\nThursday, January 18, 2024 at 6:00 P.M.\n"
    )
    assert header["time"] == (18, 0)


def test_the_time_is_read_from_the_header_not_from_the_agenda_items():
    """Items further down carry times of their own."""
    text = agenda_text() + "\n20. Recess until 1:45 P.M.\n21. Adjourn at 3:15 P.M."
    assert parse_agenda_header(text)["time"] == (17, 0)


def test_a_special_meeting_is_typed_from_the_agenda():
    header = parse_agenda_header(
        "BOARD OF DIRECTORS\nAgenda\nOPPD BOARD OF DIRECTORS\n"
        "SPECIAL BOARD MEETING\nThursday, March 6 at 5:00 P.M.\n"
    )
    assert header["type"] == "SPECIAL"


def test_an_agenda_with_no_header_gives_nothing_rather_than_guessing():
    assert parse_agenda_header("BOARD OF DIRECTORS\nAgenda\nsee attached\n") is None
    assert parse_agenda_header("") is None


# --- locations ---------------------------------------------------------------


def test_the_bare_civic_center_gains_its_address():
    """The resolution writes "Omaha Douglas Civic Center" with no street, which
    is not somewhere a reporter can be sent."""
    assert normalize_location("Omaha Douglas\nCivic Center") == CIVIC_CENTER


def test_a_virtual_meeting_is_left_as_written():
    """2023's meetings were held over Webex, with no street to give."""
    assert normalize_location("Webex\nAudio/Video\nConference") == (
        "Webex Audio/Video Conference"
    )


def test_an_empty_location_becomes_nothing():
    assert normalize_location("") is None
    assert normalize_location(None) is None


# --- the upsert key ----------------------------------------------------------


def test_the_key_is_the_date_alone():
    """The time is excluded because an agenda can correct it -- keying on a
    field this scraper also defaults is what strands records."""
    assert external_id_for(date(2026, 9, 17)) == "oppd-2026-09-17"


def test_a_meeting_keeps_its_key_when_an_agenda_corrects_the_time():
    _, before, _ = fetch_with(agenda=None)
    with patch.object(
        OppdBoardOfDirectors,
        "read_agenda",
        return_value={"month": 9, "day": 17, "year": None, "time": (18, 30),
                      "location": None, "type": "REGULAR"},
    ):
        _, after, _ = fetch_with()

    assert by_id(before)["oppd-2026-09-17"].starts_at.hour == 17
    assert by_id(after)["oppd-2026-09-17"].starts_at.hour == 18
    assert len([m for m in after if m.external_id == "oppd-2026-09-17"]) == 1


def test_every_key_is_distinct():
    _, meetings, _ = fetch_with(since=date(2024, 1, 1), until=date(2027, 12, 31))
    assert len({m.external_id for m in meetings}) == len(meetings)


# --- merging the three sources -----------------------------------------------


def test_the_default_window_gives_the_meetings_still_to_come():
    _, meetings, _ = fetch_with()
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 17), date(2026, 10, 15),
        date(2026, 11, 19), date(2026, 12, 17),
    ]


def test_the_resolution_supplies_times_for_dates_the_block_only_names():
    """The block gives days and no times at all. Without the resolution these
    four would all fall back to the published default, which is right until it
    is not."""
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        return_value={12: {"day": date(2026, 12, 17), "time": (19, 30),
                           "location": None, "details": None}},
    ):
        _, meetings, _ = fetch_with(agenda=None)
    assert by_id(meetings)["oppd-2026-12-17"].starts_at.hour == 19


def test_the_resolution_supplies_a_year_the_block_does_not_cover():
    """The branch a live run cannot reach today: the block is pruned to the
    current year, so next year arrives only from the adopted resolution."""
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        return_value={
            1: {"day": date(2027, 1, 21), "time": (17, 0), "location": None, "details": None},
            2: {"day": date(2027, 2, 18), "time": (17, 0), "location": None, "details": None},
        },
    ):
        _, meetings, _ = fetch_with(
            with_next_years_resolution(), since=date(2027, 1, 1), until=date(2027, 12, 31)
        )

    assert [m.starts_at.date() for m in meetings] == [date(2027, 1, 21), date(2027, 2, 18)]
    assert all(m.starts_at.hour == 17 for m in meetings)


def test_the_resolution_may_not_add_a_meeting_above_the_blocks_horizon():
    """A meeting moved after the schedule was adopted appears at its new date
    in the block and its old one in the frozen resolution. Taking both files
    two records for one meeting, and the one nobody attends never goes away."""
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        return_value={10: {"day": date(2026, 10, 22), "time": (17, 0),
                           "location": None, "details": None}},
    ):
        _, meetings, _ = fetch_with(agenda=None)

    days = [m.starts_at.date() for m in meetings]
    assert date(2026, 10, 15) in days  # what the live block says
    assert date(2026, 10, 22) not in days  # what the stale resolution said


def test_an_empty_block_still_yields_the_resolutions_meetings():
    """Late December, after the last meeting of the year and before the next
    year's block is posted. An empty block must mean "the resolution supplies
    everything", not "nothing qualifies" -- the two readings give opposite
    results, and this is the week that hits it."""
    soup = BeautifulSoup(with_next_years_resolution(), "html.parser")
    heading = soup.find("h3", string=re.compile("2026 Board Meetings Schedule"))
    heading.find_next_sibling("p").decompose()

    adopted = {
        2026: {12: {"day": date(2026, 12, 17), "time": (17, 0),
                    "location": None, "details": None}},
        2027: {1: {"day": date(2027, 1, 21), "time": (17, 0),
                   "location": None, "details": None}},
    }
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        side_effect=lambda data, year: adopted.get(year, {}),
    ):
        _, meetings, _ = fetch_with(
            str(soup), today=date(2026, 12, 20), agenda=None,
            since=date(2026, 12, 1), until=date(2027, 3, 31),
        )
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 12, 17), date(2027, 1, 21)
    ]


def test_an_agenda_does_not_move_a_meeting_the_schedule_already_dated(caplog):
    """A PDF parse is the most fragile input here. Moving the key on one would
    strand the record it replaced, so the disagreement is logged instead."""
    import logging

    caplog.set_level(logging.WARNING)
    with patch.object(
        OppdBoardOfDirectors,
        "read_agenda",
        return_value={"month": 9, "day": 24, "year": None, "time": (17, 0),
                      "location": None, "type": "REGULAR"},
    ):
        _, meetings, _ = fetch_with()

    days = [m.starts_at.date() for m in meetings]
    assert date(2026, 9, 17) in days
    assert date(2026, 9, 24) not in days
    assert "Keeping the schedule's date" in caplog.text


def test_a_month_with_no_other_source_takes_its_date_from_the_agenda():
    """2024's adopted schedule was published in the 2023 block, which is not on
    the page any more -- so those dates exist only inside the agenda PDFs."""
    agendas, resolutions = parse_archive(LISTING)
    assert 2024 not in resolutions, "the fixture is supposed to lack this one"

    # One PDF cannot stand in for twelve here -- the point of the test is that
    # each month's day comes out of its own agenda, so the stub reads the month
    # off the filename the archive linked.
    def header_for(_self, _session, url):
        found = re.search(r"/(\d{4})-(\d{1,2})-[a-z]+-board-agenda\.pdf", url)
        return {
            "month": int(found.group(2)), "day": 18, "year": int(found.group(1)),
            "time": (17, 0), "location": None, "type": "REGULAR",
        }

    with patch.object(OppdBoardOfDirectors, "read_agenda", header_for):
        _, meetings, _ = fetch_with(since=date(2024, 1, 1), until=date(2024, 12, 31))

    # one per archived month of 2024, and July is not one of them
    assert len(meetings) == len([1 for (y, _) in agendas if y == 2024]) == 11
    assert [m.starts_at.month for m in meetings] == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
    assert all(m.starts_at.day == 18 for m in meetings)


def test_a_block_missing_meetings_the_adopted_schedule_still_has_is_warned_about(caplog):
    """The premise the horizon rule rests on: the block is the *complete* list
    of what is still to come. If it is not, the difference is dropped and the
    forward window is still non-empty, so nothing else would notice."""
    import logging

    caplog.set_level(logging.WARNING)

    # The block has lost November and December; the adopted schedule still has
    # all four meetings left in the year.
    soup = BeautifulSoup(LISTING, "html.parser")
    block = soup.find(
        "h3", string=re.compile("2026 Board Meetings Schedule")
    ).find_next_sibling("p")
    block.clear()
    block.append(BeautifulSoup("September 17<br/>October 15", "html.parser"))

    _, meetings, _ = fetch_with(str(soup), agenda=None)

    assert "adopted 2026 schedule has 4 meetings" in caplog.text
    assert "schedule block lists 2" in caplog.text
    # and the warning is earning its place: those two really are dropped
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 17), date(2026, 10, 15)
    ]


def test_a_second_agenda_in_one_month_is_warned_about(caplog):
    """A special meeting appears exactly this way, and no other source can see
    it: it is neither adopted in advance nor listed in the block."""
    import logging

    caplog.set_level(logging.WARNING)
    soup = BeautifulSoup(LISTING, "html.parser")
    september = soup.find(
        "h3", string=re.compile("2026 Minutes and Supporting Materials")
    ).find_next_sibling("p")
    september.append(
        BeautifulSoup(
            '<a href="/media/399998/2026-9-sept-special-board-agenda.pdf">'
            "Board Agenda</a>",
            "html.parser",
        )
    )

    agendas, _ = parse_archive(str(soup))
    assert agendas[(2026, 9)].endswith("2026-9-sept-board-agenda.pdf")  # the first wins
    assert "more than one board agenda" in caplog.text


def test_a_tentative_row_is_read_off_a_real_resolution():
    """2023's adopted schedule footnoted June and July as "**", meaning the
    board might yet cancel them. The note is carried, not discarded."""
    table = [
        ["All Committees Meeting\nTuesdays", None, None, "", None, "Board Meeting\nThursdays", None, None],
        ["Date*", "Location*", "Time*", "", "", "Date*", "Location*", "Time*"],
        ["June 13**", "Webex", "10:00 a.m.", "", None, "June 15**", "Omaha Douglas\nCivic Center", "5:00 p.m."],
        ["July 18", "Webex", "10:00 a.m.", "", None, "July 20", "Omaha Douglas\nCivic Center", "5:00 p.m."],
    ]
    text = (
        "Exhibit A\n"
        "* Dates, times and locations are subject to change.\n"
        "** Tentative. The Board may consider cancelling either the June or "
        "July 2023 meetings.\n"
    )
    page = Mock(extract_table=Mock(return_value=table), extract_text=Mock(return_value=text))
    pdf = MagicMock()
    pdf.__enter__ = Mock(return_value=Mock(pages=[page]))
    pdf.__exit__ = Mock(return_value=False)

    with patch("scrapers.agencies.oppd_board_of_directors.pdfplumber.open", return_value=pdf):
        rows = parse_schedule_resolution(b"", 2023)

    assert rows[6]["day"] == date(2023, 6, 15)
    assert rows[6]["details"].startswith("Tentative.")
    assert rows[7]["details"] is None  # not footnoted


def test_a_dead_agenda_link_does_not_lose_a_meeting_the_block_named():
    _, meetings, _ = fetch_with(agenda=None)
    assert by_id(meetings)["oppd-2026-09-17"].starts_at.hour == 17


def test_a_dead_resolution_link_does_not_lose_the_blocks_meetings():
    _, meetings, _ = fetch_with(resolution=None)
    assert len(meetings) == 4


# --- what gets submitted -----------------------------------------------------


def test_every_meeting_is_named_for_the_board():
    _, meetings, _ = fetch_with()
    assert {m.name for m in meetings} == {"OPPD Board of Directors Meeting"}


def test_a_meeting_with_no_stated_place_falls_back_to_the_board_room():
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        return_value={12: {"day": date(2026, 12, 17), "time": (17, 0),
                           "location": None, "details": None}},
    ):
        _, meetings, _ = fetch_with(agenda=None)
    assert by_id(meetings)["oppd-2026-12-17"].location == CIVIC_CENTER


def test_a_tentative_meeting_carries_the_boards_own_note():
    """"**" in the adopted schedule means the board may yet cancel. The meeting
    still goes in -- an editor can unassign a reporter, but cannot assign one
    to a meeting they never saw."""
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        return_value={12: {"day": date(2026, 12, 17), "time": (17, 0), "location": None,
                           "details": "Tentative. The Board may consider cancelling."}},
    ):
        _, meetings, _ = fetch_with(agenda=None)
    assert "Tentative" in by_id(meetings)["oppd-2026-12-17"].details


def test_the_upcoming_meeting_carries_its_agenda():
    _, meetings, _ = fetch_with()
    assert by_id(meetings)["oppd-2026-09-17"].agenda_url.endswith(
        "2026-9-sept-board-agenda.pdf"
    )


def test_a_meeting_with_no_agenda_yet_still_goes_in():
    """The lead time this project exists for."""
    _, meetings, _ = fetch_with()
    assert by_id(meetings)["oppd-2026-12-17"].agenda_url is None


def test_a_winter_meeting_is_central_standard_time():
    _, meetings, _ = fetch_with()
    assert by_id(meetings)["oppd-2026-12-17"].starts_at.isoformat() == (
        "2026-12-17T17:00:00-06:00"
    )


def test_a_summer_meeting_is_central_daylight_time():
    _, meetings, _ = fetch_with()
    assert by_id(meetings)["oppd-2026-09-17"].starts_at.isoformat() == (
        "2026-09-17T17:00:00-05:00"
    )


def test_meetings_come_back_in_chronological_order():
    _, meetings, _ = fetch_with(since=date(2024, 1, 1), until=date(2027, 12, 31))
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


# --- cost and failure modes --------------------------------------------------


def test_a_normal_run_opens_only_a_handful_of_pdfs():
    """Agenda packets and resolutions are hundreds of kilobytes each. A meeting
    whose time the adopted schedule already gives does not need its agenda
    downloaded -- the two agreed in all 41 months where both exist."""
    _, meetings, fetched = fetch_with()
    assert len(fetched) <= 3, fetched


def test_no_future_meetings_is_warned_about_rather_than_reported_quietly(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    with patch(
        "scrapers.agencies.oppd_board_of_directors.parse_schedule_resolution",
        return_value={},
    ):
        fetch_with(today=date(2027, 6, 1), agenda=None)
    assert "No future OPPD meetings" in caplog.text


def test_a_dead_site_raises_a_scraper_error():
    import requests

    session = MagicMock()
    session.get = Mock(side_effect=requests.ConnectionError("down"))
    with patch(
        "scrapers.agencies.oppd_board_of_directors.requests.Session", return_value=session
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            OppdBoardOfDirectors().fetch()
