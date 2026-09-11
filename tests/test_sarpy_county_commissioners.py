import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scrapers.agencies.sarpy_county_commissioners import (
    SarpyCountyCommissioners,
    canonical_name,
    classify,
    clean_location,
    external_id_for,
)
from scrapers.base import ScraperError

FEED = (
    Path(__file__).parent / "fixtures" / "sarpy_civicweb_meetings.json"
).read_text()

# The fixture is fixed in time; pinning the window keeps these tests from
# depending on how far away today happens to be.
WIDE = dict(since=date(2015, 1, 1), until=date(2027, 12, 31))


def fetch_with(feed=FEED, **kwargs):
    """Run fetch() with the one HTTP call stubbed out."""
    scraper = SarpyCountyCommissioners(**kwargs)
    response = Mock(text=feed)
    response.raise_for_status = Mock()
    with patch(
        "scrapers.agencies.sarpy_county_commissioners.requests.get",
        return_value=response,
    ):
        return scraper, scraper.fetch()


def by_id(meetings):
    return {m.external_id: m for m in meetings}


# --- one source, not two -----------------------------------------------------


def test_one_call_covers_the_archive_and_the_schedule():
    """The first agency here that needs a single source: the same feed carries
    meetings already held and meetings still to come."""
    _, meetings = fetch_with(**WIDE)
    assert any(m.starts_at.date() < date(2026, 9, 11) for m in meetings)
    assert any(m.starts_at.date() > date(2026, 9, 11) for m in meetings)


def test_only_one_request_is_made():
    scraper = SarpyCountyCommissioners(**WIDE)
    response = Mock(text=FEED)
    response.raise_for_status = Mock()
    with patch(
        "scrapers.agencies.sarpy_county_commissioners.requests.get",
        return_value=response,
    ) as get:
        scraper.fetch()
    assert get.call_count == 1


# --- the upsert key ----------------------------------------------------------


def test_the_key_is_the_portals_own_meeting_id():
    """The opposite of every other scraper here, and deliberate: there is one
    source, so a meeting cannot change identity by moving between systems, and
    an id survives a reschedule where a date-and-time key would strand the old
    record."""
    assert external_id_for("5261") == "sarpy-5261"
    _, meetings = fetch_with(**WIDE)
    assert "sarpy-5261" in by_id(meetings)


def test_a_rescheduled_meeting_keeps_its_key():
    """The reason for keying on the id. Move a meeting and the same record is
    updated rather than a second one created."""
    moved = json.loads(FEED)
    for row in moved:
        if row["Id"] == 5263:
            row["MeetingDateTime"] = "2026-09-30 09:00"
            row["MeetingDate"] = "2026-09-30"
    _, meetings = fetch_with(json.dumps(moved), **WIDE)

    kept = by_id(meetings)["sarpy-5263"]
    assert kept.starts_at.isoformat() == "2026-09-30T09:00:00-05:00"
    assert len([m for m in meetings if m.external_id == "sarpy-5263"]) == 1


def test_every_key_is_distinct():
    _, meetings = fetch_with(**WIDE)
    assert len({m.external_id for m in meetings}) == len(meetings)


# --- only the apex board -----------------------------------------------------


def test_separate_bodies_are_left_out():
    """The portal carries the Planning Commission, the Wastewater Agency and
    several other boards. None of them is this agency."""
    _, meetings = fetch_with(**WIDE)
    names = " | ".join(m.name for m in meetings)
    assert "Planning Commission" not in names
    assert "Wastewater" not in names


def test_the_board_of_equalization_is_kept():
    """The same commissioners under a different statutory hat, matching the
    decision made for Lancaster County."""
    _, meetings = fetch_with(**WIDE)
    equalization = [m for m in meetings if "Equalization" in m.name]
    assert len(equalization) == 3
    assert all(m.meeting_type == "HEARING" for m in equalization)


def test_equalization_is_found_under_the_legacy_type_too():
    """Four equalization meetings sit under the legacy board type rather than
    the equalization one, which is why the type is read off the name."""
    _, meetings = fetch_with(**WIDE)
    assert by_id(meetings)["sarpy-5112"].meeting_type == "HEARING"


def test_cancelled_meetings_are_left_out():
    """The portal keeps these in the feed with "NO MEETING" in the name."""
    _, meetings = fetch_with(**WIDE)
    assert not [m for m in meetings if "NO MEETING" in m.name.upper()]
    assert "sarpy-5226" not in by_id(meetings)  # the dedicated cancellation type


def test_test_rows_are_left_out():
    _, meetings = fetch_with(**WIDE)
    assert not [m for m in meetings if m.name.lower().endswith("test")]


def test_the_expected_meetings_survive():
    _, meetings = fetch_with(**WIDE)
    assert len(meetings) == 13  # 20 rows, less 3 cancelled, 2 test, 2 other bodies


# --- names, types and places -------------------------------------------------


def test_the_portals_date_label_is_not_part_of_the_name():
    assert canonical_name("Board Meetings - Sep 01 2026") == (
        "Sarpy County Board of Commissioners"
    )


def test_a_qualifier_after_the_date_survives():
    assert canonical_name("Board Meetings - Aug 25 2026 Budget") == (
        "Sarpy County Board of Commissioners (Budget)"
    )


def test_meeting_types_come_from_the_name():
    assert classify("Board of Equalization - Jul 21 2026") == "HEARING"
    assert classify("Joint Public Hearing - Sep 23 2026") == "HEARING"
    assert classify("Board Retreat/Strategic Planning Session") == "WORKSHOP"
    assert classify("Special Board Meeting - May 18 2021") == "SPECIAL"
    assert classify("Board Meetings - Sep 01 2026") == "REGULAR"


def test_the_boardroom_gains_the_town_the_feed_leaves_off():
    assert clean_location("County Boardroom, 1210 Golden Gate Drive") == (
        "County Boardroom, 1210 Golden Gate Drive, Papillion, NE 68046"
    )


def test_another_venue_is_left_as_the_portal_wrote_it():
    """Only the one known address gets a town added; anywhere else may not be
    in Papillion at all."""
    assert clean_location("Bellevue University 1040 Bruin Blvd. Bellevue, NE 68005") == (
        "Bellevue University 1040 Bruin Blvd. Bellevue, NE 68005"
    )


def test_a_placeholder_location_becomes_nothing():
    """Two board retreats say "TBD", which is not a place to send anyone."""
    assert clean_location("TBD") is None
    assert clean_location("") is None
    _, meetings = fetch_with(**WIDE)
    assert by_id(meetings)["sarpy-5194"].location is None


# --- agendas -----------------------------------------------------------------


def test_a_published_meeting_links_to_its_agenda_page():
    _, meetings = fetch_with(**WIDE)
    assert by_id(meetings)["sarpy-5261"].agenda_url == (
        "https://sarpy.civicweb.net/Portal/MeetingInformation.aspx?Id=5261"
    )


def test_a_scheduled_meeting_goes_in_without_an_agenda():
    """The lead time this project exists for: the meeting is filed as soon as
    it is scheduled, and a later run adds the agenda."""
    _, meetings = fetch_with(**WIDE)
    assert by_id(meetings)["sarpy-5263"].agenda_url is None


# --- the window and failure modes --------------------------------------------


def test_the_window_filters_by_date():
    _, meetings = fetch_with(since=date(2026, 9, 1), until=date(2026, 9, 30))
    assert [m.starts_at.date() for m in meetings] == [
        date(2026, 9, 1),
        date(2026, 9, 23),
        date(2026, 9, 29),
    ]


def test_meetings_come_back_in_chronological_order():
    _, meetings = fetch_with(**WIDE)
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


def test_a_meeting_with_no_time_is_skipped_rather_than_given_one():
    rows = json.loads(FEED)
    for row in rows:
        if row["Id"] == 5263:
            row["MeetingDateTime"] = ""
    scraper, meetings = fetch_with(json.dumps(rows), **WIDE)
    assert "sarpy-5263" not in by_id(meetings)
    assert any("no usable start time" in reason for reason in scraper.skipped)


def test_an_empty_service_raises_rather_than_reporting_no_meetings():
    with pytest.raises(ScraperError, match="No meetings at all"):
        fetch_with("[]", **WIDE)


def test_a_dead_service_raises_a_scraper_error():
    import requests

    with patch(
        "scrapers.agencies.sarpy_county_commissioners.requests.get",
        side_effect=requests.ConnectionError("down"),
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            SarpyCountyCommissioners().fetch()
