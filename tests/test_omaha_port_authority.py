from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pdfplumber
import pytest

from scrapers.agencies.omaha_port_authority import (
    DEFAULT_PLACE,
    OmahaPortAuthority,
    external_id_for,
    parse_header,
    parse_listing,
)
from scrapers.base import ScraperError

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "oipa_meetings.html").read_text()
AGENDA_PDF = (FIXTURES / "oipa_agenda_april.pdf").read_bytes()

# The fixture is fixed in time; pinning the window keeps these tests from
# depending on how far away today happens to be.
WIDE = dict(since=date(2024, 1, 1), until=date(2027, 12, 31))
TODAY = date(2026, 9, 11)


def agenda_text():
    with pdfplumber.open(FIXTURES / "oipa_agenda_april.pdf") as pdf:
        return pdf.pages[0].extract_text() or ""


def fetch_with(listing=LISTING, agenda=AGENDA_PDF, agenda_status=200, **kwargs):
    """Run fetch() with the listing and every agenda PDF stubbed out."""
    scraper = OmahaPortAuthority(**kwargs)

    def get(url, **_):
        response = Mock()
        if url.endswith(".pdf"):
            if agenda_status != 200:
                response.raise_for_status = Mock(
                    side_effect=__import__("requests").HTTPError("404")
                )
            else:
                response.raise_for_status = Mock()
            response.content = agenda
        else:
            response.raise_for_status = Mock()
            response.text = listing
        return response

    session = MagicMock()
    session.get = Mock(side_effect=get)
    with patch(
        "scrapers.agencies.omaha_port_authority.requests.Session", return_value=session
    ):
        return scraper, scraper.fetch()


def by_id(meetings):
    return {m.external_id: m for m in meetings}


# --- the listing table -------------------------------------------------------


def test_reads_every_dated_row_across_all_three_tables():
    """The page carries the current year plus two archived ones."""
    assert len(parse_listing(LISTING)) == 29


def test_a_month_with_no_meeting_is_passed_over_quietly():
    """The row reads "*July No Meeting*" and carries no date. A month the board
    skips is normal, not an anomaly worth warning about."""
    days = [e["day"] for e in parse_listing(LISTING)]
    assert not [d for d in days if (d.year, d.month) == (2026, 7)]


def test_a_date_with_a_weekday_in_front_still_parses():
    """One row reads "Thursday, August 1, 2024"."""
    days = [e["day"] for e in parse_listing(LISTING)]
    assert date(2024, 8, 1) in days


def test_future_meetings_are_listed_without_an_agenda():
    """The whole reason this page is usable on its own: it carries meetings
    that have not happened yet."""
    entries = {e["day"]: e for e in parse_listing(LISTING)}
    for day in (date(2026, 10, 1), date(2026, 11, 5), date(2026, 12, 3)):
        assert entries[day]["agenda_url"] is None


def test_agenda_urls_are_absolute():
    entries = [e for e in parse_listing(LISTING) if e["agenda_url"]]
    assert entries
    assert all(e["agenda_url"].startswith("https://www.omahaipa.com/") for e in entries)


def test_a_changed_layout_raises_rather_than_returning_nothing():
    with pytest.raises(ScraperError, match="layout"):
        parse_listing("<html><body><p>no tables</p></body></html>")


# --- the agenda header -------------------------------------------------------


def test_the_time_and_place_come_off_the_header():
    assert parse_header(agenda_text()) == (
        (9, 0),
        "Metropolitan Community College, Bldg. 21, Room 112, "
        "5300 N. 30th Street, Omaha, NE 68111",
    )


def test_the_fixture_still_has_the_three_line_header():
    """The header is read by position, so this guards the shape it depends on:
    date, then place and time, then street."""
    lines = [l.strip() for l in agenda_text().splitlines() if l.strip()][:3]
    assert lines[0].startswith("April 2")
    assert "9:00 A.M." in lines[1]
    assert lines[2].startswith("5300 N. 30th Street")


def test_the_time_is_read_by_position_not_by_searching_the_page():
    """Agenda items further down carry times of their own. A loose regex over
    the whole document reports 1:45 PM for meetings held at 9:00."""
    text = agenda_text() + "\n7. Recess until 1:45 P.M.\n8. Adjourn at 3:15 P.M."
    assert parse_header(text)[0] == (9, 0)


def test_a_meeting_at_a_different_hour_and_room_is_read_correctly():
    """August 2026 was at 4:30 PM in the Swanson Conference Center -- which is
    why the agenda wins over the board's standing hour whenever there is one."""
    text = (
        "August 6th, 2026\n"
        "Metropolitan Community College, Swanson Conference Center, 4:30 P.M.\n"
        "5300 N. 30th Street, Bldg. 22 Rooms 201A-B, Omaha, NE 68111\n"
    )
    assert parse_header(text) == (
        (16, 30),
        "Metropolitan Community College, Swanson Conference Center, "
        "5300 N. 30th Street, Bldg. 22 Rooms 201A-B, Omaha, NE 68111",
    )


def test_a_header_written_with_an_at_sign_still_parses():
    text = (
        "May 7th, 2026\n"
        "Metropolitan Community College, Bldg. 21, Room 112 @ 9:00 A.M.\n"
        "5300 N. 30th Street, Omaha, NE 68111\n"
    )
    assert parse_header(text)[0] == (9, 0)
    assert "@" not in parse_header(text)[1]


def test_a_header_with_no_time_gives_nothing_rather_than_guessing():
    text = "April 2nd, 2026\nMetropolitan Community College - see attached.\n"
    assert parse_header(text) == (None, None)


def test_an_empty_agenda_gives_nothing():
    assert parse_header("") == (None, None)


# --- the upsert key ----------------------------------------------------------


def test_the_key_is_the_date_alone():
    """The time is left out deliberately -- it is the field that gets defaulted
    before an agenda exists, so keying on it would strand a record every time
    an agenda posted a non-standard hour."""
    assert external_id_for(date(2026, 10, 1)) == "oipa-2026-10-01"


def test_a_meeting_keeps_its_key_when_its_agenda_corrects_the_time():
    """The failure the date-only key exists to prevent: an agenda posts a
    non-standard hour, the time changes, and the record must be *updated*
    rather than filed again under a new id and the old one stranded."""
    _, defaulted = fetch_with(agenda_status=404, **WIDE)

    # the same run once the agenda exists and says 4:30 in another room
    with patch.object(
        OmahaPortAuthority,
        "read_agenda",
        return_value=((16, 30), "Swanson Conference Center, Omaha, NE 68111"),
    ):
        _, corrected = fetch_with(**WIDE)

    before = by_id(defaulted)["oipa-2026-09-03"]
    after = by_id(corrected)["oipa-2026-09-03"]
    assert before.external_id == after.external_id  # same record, updated
    assert before.starts_at.hour == 9 and after.starts_at.hour == 16
    assert after.location == "Swanson Conference Center, Omaha, NE 68111"


def test_every_key_is_distinct():
    _, meetings = fetch_with(**WIDE)
    assert len({m.external_id for m in meetings}) == len(meetings)


def test_two_meetings_in_one_month_keep_separate_keys():
    """September 2024 had meetings on the 5th and the 19th."""
    _, meetings = fetch_with(**WIDE)
    ids = by_id(meetings)
    assert "oipa-2024-09-05" in ids and "oipa-2024-09-19" in ids


# --- building meetings -------------------------------------------------------


def test_a_meeting_with_an_agenda_takes_its_time_from_it():
    _, meetings = fetch_with(since=date(2026, 9, 1), until=date(2026, 9, 30))
    # the fixture PDF is April's, standing in for whatever is fetched
    assert [m.starts_at.hour for m in meetings] == [9]


def test_a_meeting_with_no_agenda_takes_the_standing_hour(caplog):
    import logging

    caplog.set_level(logging.INFO)
    _, meetings = fetch_with(**WIDE)

    october = by_id(meetings)["oipa-2026-10-01"]
    assert october.starts_at.isoformat() == "2026-10-01T09:00:00-05:00"
    assert october.location == DEFAULT_PLACE
    assert october.agenda_url is None
    assert "standing 9:00 AM" in caplog.text


def test_a_dead_agenda_link_does_not_lose_the_meeting():
    """Seven of 24 agenda links 404 on the agency's own site. The meeting is
    still real, and the link is still the one the agency published.

    Uses a September meeting rather than an older one so the agenda is actually
    reached for -- anything past the lookback is never fetched at all."""
    _, meetings = fetch_with(agenda_status=404, **WIDE)
    september = by_id(meetings)["oipa-2026-09-03"]
    assert september.starts_at.hour == 9  # the standing hour stood in
    assert september.agenda_url.endswith(".pdf")


def test_old_meetings_do_not_pull_down_the_archive():
    """Agenda packets run to 4MB, so they are only opened for meetings recent
    enough for the exact hour to still matter."""
    scraper = OmahaPortAuthority(**WIDE)
    fetched = []

    def get(url, **_):
        response = Mock()
        response.raise_for_status = Mock()
        if url.endswith(".pdf"):
            fetched.append(url)
            response.content = AGENDA_PDF
        else:
            response.text = LISTING
        return response

    session = MagicMock()
    session.get = Mock(side_effect=get)
    with patch(
        "scrapers.agencies.omaha_port_authority.requests.Session", return_value=session
    ):
        scraper.fetch()
    # 29 rows carry agendas, but only the handful inside the lookback are read
    assert 0 < len(fetched) < 10


def test_a_winter_meeting_is_central_standard_time():
    _, meetings = fetch_with(**WIDE)
    assert by_id(meetings)["oipa-2026-12-03"].starts_at.isoformat() == (
        "2026-12-03T09:00:00-06:00"
    )


def test_the_window_filters_by_date():
    _, meetings = fetch_with(since=date(2026, 10, 1), until=date(2026, 12, 31))
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 10, 1),
        date(2026, 11, 5),
        date(2026, 12, 3),
    ]


def test_meetings_come_back_in_chronological_order():
    _, meetings = fetch_with(**WIDE)
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


def test_every_meeting_is_named_for_the_authority():
    _, meetings = fetch_with(**WIDE)
    assert {m.name for m in meetings} == {"Omaha Inland Port Authority Board Meeting"}


def test_a_dead_site_raises_a_scraper_error():
    import requests

    session = MagicMock()
    session.get = Mock(side_effect=requests.ConnectionError("down"))
    with patch(
        "scrapers.agencies.omaha_port_authority.requests.Session", return_value=session
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            OmahaPortAuthority().fetch()
