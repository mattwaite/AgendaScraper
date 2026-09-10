from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from scrapers.agencies.planning_commission import (
    DEFAULT_PLACE,
    PlanningCommission,
    parse_agenda_links,
    parse_calendar,
    parse_location,
)
from scrapers.base import ScraperError

FIXTURES = Path(__file__).parent / "fixtures"
CALENDAR = (FIXTURES / "pc_calendar.html").read_text()
LANDING = (FIXTURES / "pc_landing.html").read_text()
TODAY = date(2026, 9, 10)


def fetch_with(calendar=CALENDAR, landing=LANDING, **kwargs):
    """Run fetch() with both page loads stubbed out."""
    scraper = PlanningCommission(**kwargs)
    with patch.object(scraper, "_load_pages", return_value=(calendar, landing)):
        with patch(
            "scrapers.agencies.planning_commission.datetime"
        ) as fake_datetime:
            from datetime import datetime as real_datetime

            fake_datetime.side_effect = real_datetime
            fake_datetime.now.return_value.date.return_value = TODAY
            return scraper.fetch()


# --- the calendar ------------------------------------------------------------


def test_reads_times_from_the_data_attributes():
    """The visible text is a range; the attributes hold the start."""
    starts = parse_calendar(CALENDAR)
    assert starts[3].isoformat() == "2026-09-16T13:00:00-05:00"


def test_reads_both_upcoming_and_past_dates():
    assert len(parse_calendar(CALENDAR)) == 8


def test_a_november_meeting_is_central_standard_time():
    november = [
        d for d in parse_calendar(CALENDAR) if (d.year, d.month) == (2026, 11)
    ][0]
    assert november.isoformat() == "2026-11-18T13:00:00-06:00"


def test_dates_come_back_sorted_and_deduplicated():
    starts = parse_calendar(CALENDAR)
    assert starts == sorted(set(starts))


def test_a_changed_calendar_layout_raises():
    with pytest.raises(ScraperError, match="layout"):
        parse_calendar("<html><body><p>no dates</p></body></html>")


# --- agenda links ------------------------------------------------------------


def test_agenda_link_is_keyed_by_the_date_in_its_filename():
    links = parse_agenda_links(LANDING)
    assert set(links) == {date(2026, 9, 16)}
    assert links[date(2026, 9, 16)].endswith(
        "/agenda-packets/2026/20260916.pdf"
    )
    assert links[date(2026, 9, 16)].startswith("https://www.lincoln.ne.gov/")


def test_no_agenda_links_is_not_an_error():
    """Only the next meeting's agenda is ever linked here."""
    assert parse_agenda_links("<html><body>nothing</body></html>") == {}


# --- location ----------------------------------------------------------------


def test_reads_the_address_from_the_page():
    assert parse_location(CALENDAR) == (
        "County/City Building, 555 S. 10th Street, City Council Chambers, "
        "Lincoln, 68508"
    )


def test_location_survives_the_dots_in_the_street_name():
    """A regex that stops at a full stop stops inside "555 S. 10th Street"."""
    assert "10th Street" in parse_location(CALENDAR)


def test_a_missing_address_falls_back_to_the_known_one():
    meetings = fetch_with(calendar=CALENDAR.replace("68508", "00000"))
    assert meetings[0].location == DEFAULT_PLACE


# --- assembling meetings -----------------------------------------------------


def test_publishes_meetings_that_have_no_agenda_yet():
    """The point of this source: the schedule is out months ahead of agendas,
    which is the lead time an editor needs to assign a reporter."""
    meetings = fetch_with()
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 16),
        date(2026, 9, 30),
        date(2026, 10, 14),
        date(2026, 10, 28),
        date(2026, 11, 18),
    ]
    with_agenda = [m for m in meetings if m.agenda_url]
    assert len(with_agenda) == 1
    assert with_agenda[0].starts_at.date() == date(2026, 9, 16)
    assert all(m.agenda_url is None for m in meetings[1:])


def test_old_dates_fall_outside_the_default_window():
    assert date(2024, 11, 20) not in [m.starts_at.date() for m in fetch_with()]


def test_since_reaches_back_to_the_past_dates():
    meetings = fetch_with(since=date(2024, 1, 1), until=date(2024, 12, 31))
    assert [m.starts_at.date() for m in meetings] == [
        date(2024, 11, 20),
        date(2024, 12, 4),
        date(2024, 12, 18),
    ]


def test_external_id_is_built_from_the_date():
    """Neither page gives a meeting id, so the date is the only identifier
    available -- see the module docstring for what that costs."""
    assert fetch_with()[0].external_id == "llcpc-2026-09-16"


def test_every_meeting_is_named_and_typed_the_same_way():
    meetings = fetch_with()
    assert {m.name for m in meetings} == {
        "Lincoln-Lancaster County Planning Commission Regular Meeting"
    }
    assert {m.meeting_type for m in meetings} == {"REGULAR"}
