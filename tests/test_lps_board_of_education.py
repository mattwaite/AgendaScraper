import re
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scrapers.agencies.lps_board_of_education import (
    LpsBoardOfEducation,
    parse_listing,
)
from scrapers.base import ScraperError

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "lps_sparq_meetings.html").read_text()


def entry_for(meeting_id):
    return next(e for e in parse_listing(LISTING) if e["meeting_id"] == meeting_id)


def fetch_with(html=LISTING, **kwargs):
    """Run fetch() with the HTTP call stubbed out."""
    scraper = LpsBoardOfEducation(**kwargs)
    response = Mock(text=html)
    response.raise_for_status = Mock()
    with patch("scrapers.agencies.lps_board_of_education.requests.get", return_value=response):
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


# --- building meetings -------------------------------------------------------


def test_maps_the_portals_meeting_types_onto_the_api_vocabulary():
    _, meetings = fetch_with(since=date(2011, 1, 1))
    by_id = {m.external_id: m.meeting_type for m in meetings}
    assert by_id["lps-meeting-763426"] == "REGULAR"
    assert by_id["lps-meeting-759410"] == "SPECIAL"
    assert by_id["lps-meeting-2533"] == "HEARING"
    # Working, Staff and Finance all describe a session that isn't a formal
    # meeting, and the API has one word for that.
    assert by_id["lps-meeting-620220"] == "WORKSHOP"
    assert by_id["lps-meeting-750709"] == "WORKSHOP"
    assert by_id["lps-meeting-468109"] == "WORKSHOP"


def test_an_unmapped_type_is_kept_as_regular_with_a_warning(caplog):
    # The label and its value sit either side of a </b> in the markup.
    html = re.sub(
        r"(Meeting Type:\s*</b>\s*)Regular", r"\1Unheardof", LISTING, count=1
    )
    assert "Unheardof" in html, "the fixture's markup changed shape"

    _, meetings = fetch_with(html, since=date(2011, 1, 1))
    assert len(meetings) == 10  # nothing dropped
    assert "unmapped meeting type" in caplog.text
    assert [m for m in meetings if m.external_id == "lps-meeting-763426"][
        0
    ].meeting_type == "REGULAR"


def test_names_carry_the_district_and_the_meetings_own_title():
    _, meetings = fetch_with(since=date(2011, 1, 1))
    names = {m.external_id: m.name for m in meetings}
    assert names["lps-meeting-763426"] == (
        "Lincoln Public Schools Board of Education Regular Meeting"
    )
    assert "Wellness, American Civics" in names["lps-meeting-736918"]


def test_esu_18_meetings_are_included_and_distinguishable():
    """The ESU 18 board is the same people meeting the same night, so both land
    on the same date and only the name tells them apart."""
    _, meetings = fetch_with(since=date(2011, 1, 1))
    sep8 = [m for m in meetings if m.starts_at.date() == date(2026, 9, 8)]
    assert len(sep8) == 2
    assert sum("ESU 18" in m.name for m in sep8) == 1
    assert len({m.external_id for m in sep8}) == 2


def test_the_window_filters_by_date():
    _, meetings = fetch_with(since=date(2026, 1, 1), until=date(2026, 6, 30))
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 1, 27),
        date(2026, 3, 24),
        date(2026, 5, 18),
        date(2026, 6, 25),
    ]


def test_meetings_come_back_in_chronological_order():
    _, meetings = fetch_with(since=date(2011, 1, 1))
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


def test_a_dead_portal_raises_a_scraper_error():
    import requests

    scraper = LpsBoardOfEducation()
    with patch(
        "scrapers.agencies.lps_board_of_education.requests.get",
        side_effect=requests.ConnectionError("down"),
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            scraper.fetch()
