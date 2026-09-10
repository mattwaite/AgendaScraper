"""The iCalendar reader, against a trimmed capture of Lancaster's feed."""

from datetime import date
from pathlib import Path

from scrapers.sources.civicplus_ical import parse_events

FEED = (Path(__file__).parent / "fixtures" / "lancaster_calendar.ics").read_text()


def by_summary(text):
    return [e for e in parse_events(FEED) if text in e.summary]


def test_reads_every_event():
    assert len(parse_events(FEED)) == 7


def test_a_timed_event_carries_a_central_time():
    event = [e for e in by_summary("Commissioners Meeting") if e.day.year == 2026][0]
    assert event.starts_at.isoformat() == "2026-09-29T09:00:00-05:00"
    assert event.all_day is False


def test_a_winter_event_is_central_standard_time():
    event = [e for e in by_summary("Commissioners Meeting") if e.day.year == 2019][0]
    assert event.starts_at.isoformat() == "2019-12-03T09:00:00-06:00"


def test_an_all_day_event_has_a_date_but_no_time():
    """This is how the county records a meeting whose time isn't set. Reading it
    as midnight would put a made-up hour in front of an editor."""
    staff = by_summary("Staff Meeting")[0]
    assert staff.all_day is True
    assert staff.starts_at is None
    assert staff.day == date(2026, 9, 24)


def test_event_ids_come_from_the_description_link():
    """The feed's own URL property points at the whole calendar, not the event."""
    event = [e for e in by_summary("Commissioners Meeting") if e.day.year == 2026][0]
    assert event.eid == "2138"
    assert event.uid == "2138"


def test_folded_lines_are_rejoined():
    """iCalendar wraps long lines with a leading space. This event's EID is
    split across the fold ("...calend\\n ar.aspx?EID=194"), so it only reads
    correctly if the lines are rejoined first."""
    assert "ar.aspx?EID=194" in FEED  # the fixture still has the fold in it
    assert by_summary("Christmas")[0].eid == "194"


def test_trailing_whitespace_in_a_summary_is_trimmed():
    """The feed pads some summaries, and the series classifier matches exactly."""
    assert any(e.summary == "Board of Commissioners Meeting" for e in parse_events(FEED))


def test_a_feed_with_no_events_is_empty_not_an_error():
    assert parse_events("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n") == []
