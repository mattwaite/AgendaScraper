from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scrapers.agencies.lancaster_county_commissioners import (
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
    assert meeting.external_id == "lnc-agenda-2659"
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


def test_external_id_does_not_move_when_the_meeting_does():
    """The agenda id keys the upsert, so a rescheduled meeting updates its
    existing record rather than creating a second one."""
    _, before = build_one(AGENDA)
    _, after = build_one(
        AGENDA.replace("AT 9:00 AM", "AT 1:30 PM"),
        entry={
            "date": date(2026, 9, 9),
            "agenda_id": "2659",
            "agenda_url": "https://example.test/agenda",
            "title": "Board of Commissioners",
        },
    )
    assert before.external_id == after.external_id
    assert before.starts_at != after.starts_at
