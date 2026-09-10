from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from scrapers.agencies.lincoln_city_council import (
    LincolnCityCouncil,
    classify,
    parse_date_time,
)
from scrapers.meeting import CENTRAL

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 8)


def parse_fixture(name="granicus_synthetic.html", **kwargs):
    scraper = LincolnCityCouncil()
    html = (FIXTURES / name).read_text()
    return scraper, scraper.parse(html, today=TODAY, **kwargs)


def test_parse_date_time_winter_is_central_standard():
    parsed = parse_date_time("Jan 5, 2026 - 3:00 PM")
    assert parsed == datetime(2026, 1, 5, 15, 0, tzinfo=CENTRAL)
    assert parsed.isoformat() == "2026-01-05T15:00:00-06:00"


def test_parse_date_time_summer_is_central_daylight():
    parsed = parse_date_time("Aug 31, 2026 - 5:30 PM")
    assert parsed.isoformat() == "2026-08-31T17:30:00-05:00"


def test_parse_date_time_tolerates_granicus_whitespace():
    raw = "Sep 14, 2026\n           -\n              3:00 PM"
    assert parse_date_time(raw).isoformat() == "2026-09-14T15:00:00-05:00"


def test_parse_date_time_returns_none_when_unparseable():
    assert parse_date_time("Currently there are no Upcoming Events") is None
    assert parse_date_time("Sep 14, 2026") is None  # no time component


@pytest.mark.parametrize(
    "title,expected",
    [
        ("City Council - Action", "REGULAR"),
        ("City Council - Special Meeting", "SPECIAL"),
        ("City Council - Public Hearing", "HEARING"),
        ("City Council - Pre-Council", "WORKSHOP"),
        ("City Council - Emergency Session", "EMERGENCY"),
    ],
)
def test_classify(title, expected):
    assert classify(title) == expected


def test_reads_upcoming_and_archive_tables():
    _, meetings = parse_fixture()
    # The upcoming rows, minus the second Sep 14 meeting (see the same-day test
    # below). Both archive rows (Aug 31, Jan 5) fall outside the 7-day lookback.
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 14),
        date(2026, 9, 21),
        date(2027, 1, 11),
    ]


def test_archive_rows_inside_the_lookback_are_kept():
    _, meetings = parse_fixture(days_back=10)
    assert date(2026, 8, 31) in [m.starts_at.date() for m in meetings]


def test_a_second_meeting_on_one_day_is_reported_rather_than_dropped():
    """Meetings are keyed on their date, so a day holding two of them cannot be
    represented. Across 45 archived meetings this has never happened; if it
    does, the run says so instead of one quietly overwriting the other."""
    scraper, meetings = parse_fixture()
    sep14 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 14)]
    assert len(sep14) == 1
    assert len(scraper.skipped) == 1
    assert "shares the day" in scraper.skipped[0]


def test_meeting_without_an_agenda_is_still_submitted():
    """agendaUrl is optional, so a meeting goes in as soon as it is scheduled
    and picks up its agenda URL on a later run."""
    _, meetings = parse_fixture()
    sep21 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 21)]
    assert len(sep21) == 1
    assert sep21[0].agenda_url is None
    assert sep21[0].meeting_type == "SPECIAL"


def test_external_id_is_the_date_whatever_the_row_carries():
    """A meeting is identified the same way whether it came from Granicus, which
    offers a clip_id, or the city calendar, which does not. Keying on the clip
    would change its identity as it moved from one source to the other."""
    _, meetings = parse_fixture()
    assert [m.external_id for m in meetings] == [
        "lnk-2026-09-14",
        "lnk-2026-09-21",
        "lnk-2027-01-11",
    ]


def test_a_row_with_no_clip_id_is_keyed_the_same_way_as_any_other():
    scraper = LincolnCityCouncil()
    html = """<table class="listingTable"><tr class="listingRow">
      <td>City Council - Action</td><td>Sep 28, 2026 - 3:00 PM</td>
      <td>&nbsp;</td><td>&nbsp;</td>
    </tr></table>"""
    meetings = scraper.parse(html, today=TODAY)
    assert meetings[0].external_id == "lnk-2026-09-28"
    assert meetings[0].agenda_url is None


def test_a_meeting_listed_in_both_tables_appears_once():
    _, meetings = parse_fixture()
    assert [m.external_id for m in meetings].count("lnk-2026-09-14") == 1


def test_external_id_is_stable_when_a_meetings_time_changes():
    """A meeting moved to a new time on the same day keeps its id, so the
    platform updates that record rather than filing a second one. A move to a
    different *day* is a different matter: the date is the identity, so that
    reads as a new meeting and leaves the old record for a human to remove."""
    scraper = LincolnCityCouncil()
    row = """<table class="listingTable"><tr class="listingRow">
      <td>City Council - Action</td><td>Sep 14, 2026 - {time}</td>
      <td><a href="//lnklan.granicus.com/AgendaViewer.php?view_id=2&clip_id=430">Agenda</a></td>
    </tr></table>"""
    before = scraper.parse(row.format(time="3:00 PM"), today=TODAY)[0]
    after = scraper.parse(row.format(time="5:30 PM"), today=TODAY)[0]
    assert before.external_id == after.external_id
    assert before.starts_at != after.starts_at


def test_since_overrides_the_rolling_window():
    scraper = LincolnCityCouncil(since=date(2026, 1, 1))
    meetings = scraper.parse(
        (FIXTURES / "granicus_synthetic.html").read_text(), today=TODAY
    )
    assert date(2026, 1, 5) in [m.starts_at.date() for m in meetings]


def test_names_are_constant_per_meeting_type():
    """Names feed importFingerprint, so they must not vary per meeting."""
    _, meetings = parse_fixture()
    regular = {m.name for m in meetings if m.meeting_type == "REGULAR"}
    assert regular == {"Lincoln City Council Regular Meeting"}
    for meeting in meetings:
        assert str(meeting.starts_at.year) not in meeting.name


def test_agenda_urls_are_absolute():
    _, meetings = parse_fixture()
    found = [m.agenda_url for m in meetings if m.agenda_url]
    assert found and all(url.startswith("https://") for url in found)


def test_live_capture_still_parses():
    """Guards against a Granicus layout change going unnoticed."""
    _, meetings = parse_fixture("lnklan_viewpublisher.html", days_back=400)
    assert len(meetings) >= 20
    assert all(m.location.startswith("Council Chambers") for m in meetings)


# --- merging in the city's calendar ------------------------------------------
#
# Granicus alone can see no future meetings at all: it lists one only once its
# agenda is posted. The city's calendar page carries the schedule months ahead.

CALENDAR = (FIXTURES / "cc_calendar.html").read_text()


def merged(granicus_meetings=None, calendar=CALENDAR, **kwargs):
    scraper = LincolnCityCouncil(**kwargs)
    meetings = (
        scraper.parse(
            (FIXTURES / "granicus_synthetic.html").read_text(), today=TODAY
        )
        if granicus_meetings is None
        else granicus_meetings
    )
    with patch.object(scraper, "_in_window", side_effect=lambda d: WINDOW(d)):
        return scraper, scraper.merge_calendar(meetings, calendar)


def WINDOW(day):
    from datetime import timedelta

    return TODAY - timedelta(days=7) <= day <= TODAY + timedelta(days=400)


def test_the_calendar_supplies_meetings_granicus_has_not_published():
    """The whole reason for the second source."""
    _, meetings = merged([])
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 14),
        date(2026, 9, 21),
        date(2026, 9, 28),
        date(2026, 10, 5),
        date(2026, 10, 19),
    ]
    assert all(m.agenda_url is None for m in meetings)
    assert all(m.meeting_type == "REGULAR" for m in meetings)


def test_calendar_meetings_carry_the_evening_start_where_the_page_says_so():
    """Fourth Mondays start at 5:30, and the times are read per meeting rather
    than assumed from the first one."""
    _, meetings = merged([])
    by_date = {m.starts_at.date(): m.starts_at.isoformat() for m in meetings}
    assert by_date[date(2026, 9, 14)] == "2026-09-14T15:00:00-05:00"
    assert by_date[date(2026, 9, 28)] == "2026-09-28T17:30:00-05:00"


def test_a_granicus_meeting_is_not_duplicated_by_the_calendar():
    """Both sources list Sep 14; it must land once, keyed the same either way."""
    _, meetings = merged()
    sep14 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 14)]
    assert len(sep14) == 1
    assert sep14[0].external_id == "lnk-2026-09-14"


def test_granicus_wins_on_the_details_where_both_describe_a_meeting():
    """It is the system the agenda hangs off, so its agenda link survives."""
    _, meetings = merged()
    sep14 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 14)][0]
    assert sep14.agenda_url is not None


def test_a_time_disagreement_is_logged_rather_than_silently_resolved(caplog):
    import logging

    caplog.set_level(logging.INFO)
    calendar = CALENDAR.replace('data-start-hour="15"', 'data-start-hour="19"', 1)
    merged(calendar=calendar)
    assert "keeping Granicus" in caplog.text


def test_the_address_comes_from_the_calendar_page():
    _, meetings = merged([])
    assert meetings[0].location == (
        "Council Chambers, County/City Building, 555 South 10th Street, "
        "Lincoln 68508"
    )


def test_calendar_dates_outside_the_window_are_left_alone():
    _, meetings = merged([])
    assert date(2026, 1, 5) not in [m.starts_at.date() for m in meetings]


def test_meetings_come_back_in_chronological_order():
    _, meetings = merged()
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)
