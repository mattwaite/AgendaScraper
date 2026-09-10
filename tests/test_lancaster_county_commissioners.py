from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scrapers.agencies.lancaster_county_commissioners import (
    DEFAULT_PLACE,
    LancasterCountyCommissioners,
    classify,
    parse_agenda_text,
    parse_listing,
)
from scrapers.base import ScraperError

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "lancaster_agenda_center.html").read_text(errors="replace")
AGENDA = (FIXTURES / "lancaster_agenda_2659.txt").read_text()


# --- the listing -------------------------------------------------------------


def test_reads_every_agenda_row():
    entries = parse_listing(LISTING)
    assert len(entries) == 36
    assert entries[0]["date"] == date(2026, 9, 8)
    assert entries[-1]["date"] == date(2026, 1, 6)


def test_entry_carries_a_stable_agenda_id_and_absolute_url():
    first = parse_listing(LISTING)[0]
    assert first["agenda_id"] == "2659"
    assert first["agenda_url"] == (
        "https://www.lancaster.ne.gov/AgendaCenter/ViewFile/Agenda/_09082026-2659"
    )


def test_the_upload_timestamp_is_not_mistaken_for_a_meeting_time():
    """Rows read "Sep 8, 2026 - Posted Sep 4, 2026 3:52 PM". The 3:52 PM is when
    the file was uploaded; the meeting time only exists in the agenda itself."""
    first = parse_listing(LISTING)[0]
    assert first["date"] == date(2026, 9, 8)
    assert "3:52" not in str(first)


def test_a_changed_layout_raises_rather_than_returning_nothing():
    with pytest.raises(ScraperError, match="layout"):
        parse_listing("<html><body>nothing here</body></html>")


# --- the agenda PDF ----------------------------------------------------------


def test_reads_time_and_room_from_the_agenda_header():
    start, place = parse_agenda_text(AGENDA)
    assert start == (9, 0)
    assert place == (
        "County-City Building, Room 112, 555 South 10th Street, Lincoln 68508"
    )


@pytest.mark.parametrize(
    "header,expected",
    [
        ("MEETING\nTUESDAY, MAY 5, 2026, AT 9:00 AM\nROOM 112", (9, 0)),
        ("MEETING\nTUESDAY, MAY 5, 2026, AT 1:30 PM\nROOM 112", (13, 30)),
        ("MEETING\nAT 12:15 PM\nROOM 112", (12, 15)),
        ("MEETING\nAT 12:30 A.M.\nROOM 112", (0, 30)),
        ("MEETING\nAT 8:30 a.m.\nROOM 112", (8, 30)),
    ],
)
def test_parses_the_clock_formats_the_county_uses(header, expected):
    assert parse_agenda_text(header)[0] == expected


def test_missing_time_is_reported_rather_than_guessed():
    start, _ = parse_agenda_text("LANCASTER COUNTY BOARD\nROOM 112\nno clock here")
    assert start is None


def test_a_venue_that_is_not_the_county_city_building_keeps_its_own_wording():
    """No street address is invented for somewhere we do not know."""
    _, place = parse_agenda_text("BOARD\nAT 9:00 AM\nZOOM MEETING")
    assert place == "Zoom Meeting"
    assert "Street" not in place


def test_the_body_name_is_not_mistaken_for_the_venue():
    _, place = parse_agenda_text(
        "LANCASTER COUNTY BOARD OF COMMISSIONERS\nAT 9:00 AM\nCOUNTY-CITY BUILDING, ROOM 112"
    )
    assert place.startswith("County-City Building")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Board of Commissioners", "REGULAR"),
        ("SPECIAL MEETING of the board", "SPECIAL"),
        ("BOARD OF EQUALIZATION", "HEARING"),
        ("Staff Meeting", "WORKSHOP"),
        ("EMERGENCY MEETING", "EMERGENCY"),
    ],
)
def test_classify(text, expected):
    assert classify(text) == expected


# --- assembling a Meeting ----------------------------------------------------


def build_one(agenda_text, entry=None):
    """Run _build with the network stubbed out."""
    scraper = LancasterCountyCommissioners()
    entry = entry or {
        "date": date(2026, 9, 8),
        "agenda_id": "2659",
        "agenda_url": "https://example.test/agenda",
        "title": "Board of Commissioners",
    }
    with patch.object(scraper, "_agenda_text", return_value=agenda_text):
        return scraper, scraper._build(Mock(), entry)


def test_builds_a_meeting_from_a_row_and_its_agenda():
    scraper, meeting = build_one(AGENDA)
    assert meeting.name == "Lancaster County Board of Commissioners Regular Meeting"
    assert meeting.starts_at.isoformat() == "2026-09-08T09:00:00-05:00"
    assert meeting.external_id == "lnc-commissioners-2026-09-08"
    assert meeting.agenda_url == "https://example.test/agenda"
    assert scraper.skipped == []


def test_a_january_meeting_is_central_standard_time():
    scraper, meeting = build_one(
        "BOARD\nTUESDAY, JANUARY 6, 2026, AT 9:00 AM\nROOM 112",
        entry={
            "date": date(2026, 1, 6),
            "agenda_id": "2489",
            "agenda_url": "https://example.test/a",
            "title": "Board of Commissioners",
        },
    )
    assert meeting.starts_at.isoformat() == "2026-01-06T09:00:00-06:00"


def test_an_agenda_with_no_time_is_skipped_with_a_reason():
    scraper, meeting = build_one("BOARD OF COMMISSIONERS\nROOM 112")
    assert meeting is None
    assert len(scraper.skipped) == 1
    assert "no start time" in scraper.skipped[0]


def test_an_unreadable_agenda_is_skipped_with_a_reason():
    scraper, meeting = build_one(None)
    assert meeting is None
    assert "could not read the agenda" in scraper.skipped[0]


def test_external_id_does_not_move_when_the_meetings_time_changes():
    """A meeting whose time moves within the day keeps its id, so the platform
    updates that record rather than creating a second one. The series is part of
    the key because the board and the Board of Equalization sit the same
    morning; the agenda file's own id is not, because the calendar half of this
    scraper has no such id and the meeting must read the same either way."""
    _, before = build_one(AGENDA)
    _, after = build_one(AGENDA.replace("AT 9:00 AM", "AT 1:30 PM"))
    assert before.external_id == after.external_id
    assert before.starts_at != after.starts_at


# --- the calendar half -------------------------------------------------------
#
# The Agenda Center stops at the last meeting held; the iCalendar feed carries
# the ones still to come. On 2026-09-10 the two did not overlap at all.

FEED = (FIXTURES / "lancaster_calendar.ics").read_text()
TODAY = date(2026, 9, 10)


def from_calendar(existing=None, feed=FEED, **kwargs):
    scraper = LancasterCountyCommissioners(**kwargs)
    with patch.object(
        scraper, "_in_window", side_effect=lambda d: WINDOW(scraper, d)
    ):
        return scraper, scraper.from_calendar(feed, existing or {})


def WINDOW(scraper, day):
    from datetime import timedelta

    earliest = scraper.since or TODAY - timedelta(days=7)
    latest = scraper.until or TODAY + timedelta(days=400)
    return earliest <= day <= latest


def test_the_calendar_supplies_the_future_meetings():
    """The reason for the second source: the Agenda Center has none."""
    _, meetings = from_calendar()
    assert [(m.starts_at.date(), m.meeting_type) for m in meetings] == [
        (date(2026, 9, 29), "REGULAR"),
        (date(2026, 10, 27), "REGULAR"),
        (date(2026, 9, 15), "HEARING"),
    ]
    assert all(m.agenda_url is None for m in meetings)


def test_equalization_sits_the_same_morning_and_keeps_its_own_identity():
    """The board and the Board of Equalization meet on one date, so the date
    alone cannot key a meeting -- the series has to be part of it."""
    _, meetings = from_calendar()
    boe = [m for m in meetings if m.meeting_type == "HEARING"][0]
    assert boe.external_id == "lnc-equalization-2026-09-15"
    assert boe.name == "Lancaster County Board of Equalization"


def test_a_meeting_the_agenda_center_already_has_is_not_repeated():
    existing = {"lnc-commissioners-2026-09-29": object()}
    _, meetings = from_calendar(existing)
    assert date(2026, 9, 29) not in [m.starts_at.date() for m in meetings]


def test_staff_meetings_are_left_out_with_one_line_not_seventy(caplog):
    """The county lists them as all-day entries with no start time."""
    scraper, meetings = from_calendar()
    assert all("Staff" not in m.name for m in meetings)
    assert scraper.skipped == []  # a known gap, not a per-meeting failure
    assert "staff meetings left out" in caplog.text


def test_another_bodys_meetings_are_ignored():
    """The Public Building Commission shares this calendar and is not us."""
    _, meetings = from_calendar(until=date(2027, 12, 31))
    assert all("Building Commission" not in m.name for m in meetings)


def test_holidays_are_not_meetings():
    _, meetings = from_calendar(until=date(2050, 1, 1))
    assert all("Christmas" not in m.name for m in meetings)


def test_dates_outside_the_window_are_left_alone():
    _, meetings = from_calendar()
    assert date(2019, 12, 3) not in [m.starts_at.date() for m in meetings]


def test_the_feeds_own_location_is_not_used():
    """It reads "- Lincoln NE 68502", which would tell a reporter nothing."""
    _, meetings = from_calendar()
    assert all(m.location == DEFAULT_PLACE for m in meetings)
