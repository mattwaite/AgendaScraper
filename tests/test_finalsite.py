"""The Finalsite calendar reader, against a real month of the OPS calendar."""

from datetime import date
from pathlib import Path

from scrapers.sources.finalsite import (
    Event,
    element_url,
    months_covering,
    parse_events,
)

MONTH = (
    Path(__file__).parent / "fixtures" / "ops_finalsite_september.html"
).read_text()


def test_reads_every_event_in_the_month():
    assert len(parse_events(MONTH)) == 4


def test_an_event_carries_a_central_time():
    first = min(parse_events(MONTH), key=lambda e: e.starts_at)
    assert first.starts_at.isoformat() == "2026-09-10T17:00:00-05:00"
    assert first.title == "Board Budget Hearing"
    assert first.all_day is False


def test_two_events_on_one_day_are_kept_apart_by_their_times():
    """The case that decides how this calendar reconciles with SPARQ: a 5:00
    budget hearing and the 6:00 board meeting that follows it."""
    sep10 = sorted(
        (e for e in parse_events(MONTH) if e.starts_at.date() == date(2026, 9, 10)),
        key=lambda e: e.starts_at,
    )
    assert [e.starts_at.strftime("%H%M") for e in sep10] == ["1700", "1800"]
    assert len({e.occur_id for e in sep10}) == 2


def test_the_location_comes_through():
    first = min(parse_events(MONTH), key=lambda e: e.starts_at)
    assert "3215 Cuming St" in first.location


def test_the_occurrence_id_is_unique_per_event():
    events = parse_events(MONTH)
    assert len({e.occur_id for e in events}) == len(events)


def test_the_series_id_is_the_number_in_front_of_the_occurrence_id():
    """Kept only as a breadcrumb for the logs -- never as a key, since the
    calendar is one of two sources and SPARQ has no equivalent."""
    first = min(parse_events(MONTH), key=lambda e: e.starts_at)
    assert first.occur_id.startswith(first.series_id + "_")
    assert first.series_id.isdigit()


def test_an_all_day_event_has_no_time():
    """These have no <time> element at all. Reading one as midnight would put a
    made-up hour in front of an editor."""
    stripped = MONTH.replace("fsStartTime", "fsNotATime")
    events = parse_events(stripped)
    assert events and all(e.all_day for e in events)
    assert all(e.starts_at is None for e in events)


def test_an_unreadable_timestamp_drops_the_event_rather_than_crashing():
    broken = MONTH.replace("2026-09-10T17:00:00-05:00", "not a timestamp")
    assert len(parse_events(broken)) == 3


def test_a_page_with_no_events_is_empty_not_an_error():
    assert parse_events("<html><body><p>nothing here</p></body></html>") == []


def test_titles_have_their_whitespace_collapsed():
    html = (
        '<div class="fsCalendarEvent">'
        '<a class="fsCalendarEventLink" data-occur-id="9_x_y"> Board   \n Meeting </a>'
        '<time class="fsStartTime" datetime="2026-09-10T18:00:00-05:00"></time>'
        "</div>"
    )
    assert parse_events(html) == [
        Event(
            occur_id="9_x_y",
            title="Board Meeting",
            starts_at=parse_events(html)[0].starts_at,
            location="",
        )
    ]


# --- the month range ---------------------------------------------------------


def test_months_covering_reaches_one_past_each_end():
    """A month view carries a few days of its neighbours, so an event just
    inside the window may be rendered only in a month outside it."""
    months = months_covering(date(2026, 9, 15), date(2026, 11, 20))
    assert months == [
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
        date(2026, 12, 1),
    ]


def test_months_covering_crosses_a_year_boundary():
    assert months_covering(date(2027, 1, 5), date(2027, 1, 20)) == [
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]


def test_months_covering_handles_a_december_end():
    assert months_covering(date(2026, 12, 1), date(2026, 12, 31)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
    ]


def test_element_url_names_the_month():
    assert element_url("https://www.ops.org", 2039, date(2026, 11, 1)) == (
        "https://www.ops.org/fs/elements/2039?cal_date=2026-11-01"
    )
