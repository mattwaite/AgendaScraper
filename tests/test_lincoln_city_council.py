from datetime import date, datetime
from pathlib import Path

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
    # All four upcoming rows, including Sep 21 which has no agenda yet. Both
    # archive rows (Aug 31, Jan 5) fall outside the 7-day lookback from Sep 8.
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 14),
        date(2026, 9, 14),
        date(2026, 9, 21),
        date(2027, 1, 11),
    ]


def test_archive_rows_inside_the_lookback_are_kept():
    _, meetings = parse_fixture(days_back=10)
    assert date(2026, 8, 31) in [m.starts_at.date() for m in meetings]


def test_two_meetings_on_the_same_day_both_survive():
    """The old script keyed dedup on date alone and dropped one of these."""
    _, meetings = parse_fixture()
    sep14 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 14)]
    assert len(sep14) == 2
    assert {m.external_id for m in sep14} == {"lnk-clip-430", "lnk-clip-431"}
    assert {m.meeting_type for m in sep14} == {"REGULAR", "WORKSHOP"}


def test_meeting_without_an_agenda_is_still_submitted():
    """agendaUrl is optional, so a meeting goes in as soon as it is scheduled
    and picks up its agenda URL on a later run."""
    _, meetings = parse_fixture()
    sep21 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 21)]
    assert len(sep21) == 1
    assert sep21[0].agenda_url is None
    assert sep21[0].meeting_type == "SPECIAL"


def test_external_id_falls_back_to_the_date_with_no_links_to_borrow():
    """No clip_id anywhere in the row. Never seen on this portal, but the id
    still has to be deterministic rather than absent."""
    _, meetings = parse_fixture()
    sep21 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 21)][0]
    assert sep21.external_id == "lnk-date-2026-09-21"


def test_external_id_comes_from_any_link_carrying_a_clip_id():
    """Granicus puts clip_id in the minutes and video links too, so a row keeps
    a stable id even when its agenda link is missing."""
    scraper = LincolnCityCouncil()
    html = """<table class="listingTable"><tr class="listingRow">
      <td>City Council - Action</td><td>Sep 28, 2026 - 3:00 PM</td>
      <td>&nbsp;</td>
      <td><a href="//lnklan.granicus.com/MinutesViewer.php?view_id=2&clip_id=433">Minutes</a></td>
    </tr></table>"""
    meetings = scraper.parse(html, today=TODAY)
    assert meetings[0].external_id == "lnk-clip-433"
    assert meetings[0].agenda_url is None


def test_a_meeting_listed_in_both_tables_appears_once():
    _, meetings = parse_fixture()
    assert [m.external_id for m in meetings].count("lnk-clip-430") == 1


def test_external_id_is_stable_when_a_meeting_moves():
    """The whole point of externalId: a rescheduled meeting keeps its id, so
    the platform updates that record instead of filing a second one."""
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
