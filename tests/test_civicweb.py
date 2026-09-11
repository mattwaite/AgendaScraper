"""The CivicWeb meetings-service reader, against a slice of Sarpy's feed."""

import json
from pathlib import Path

import pytest

from scrapers.base import ScraperError
from scrapers.sources.civicweb import (
    parse_meetings,
    service_url,
    short_name,
    split_label,
)

FEED = (Path(__file__).parent / "fixtures" / "sarpy_civicweb_meetings.json").read_text()


def test_reads_every_row():
    assert len(parse_meetings(FEED)) == 20


def test_a_meeting_carries_a_central_time():
    """The feed gives a naive wall time with no offset, so it is read as
    Central rather than converted from something."""
    row = [m for m in parse_meetings(FEED) if m.id == "5261"][0]
    assert row.starts_at.isoformat() == "2026-09-01T15:00:00-05:00"


def test_a_winter_meeting_is_central_standard_time():
    row = [m for m in parse_meetings(FEED) if m.id == "5194"][0]
    assert row.starts_at.isoformat() == "2026-01-20T12:00:00-06:00"


def test_published_says_whether_the_agenda_is_up():
    """False on meetings that are only scheduled -- which is the lead time this
    project is after, not a draft to skip."""
    rows = {m.id: m.published for m in parse_meetings(FEED)}
    assert rows["5261"] is True  # Sep 1, already held
    assert rows["5263"] is False  # Sep 29, scheduled only


def test_a_cancelled_meeting_is_flagged_not_removed():
    """The portal leaves these in the feed with the words in the name."""
    cancelled = [m for m in parse_meetings(FEED) if m.cancelled]
    assert len(cancelled) == 3
    assert all("NO MEETING" in m.name.upper() for m in cancelled)


def test_a_cancellation_written_as_no_board_meetings_is_caught_too():
    rows = parse_meetings(
        '[{"Id": 1, "Name": "NO Board Meetings - Jan 19 2021 HOLIDAY",'
        ' "MeetingDateTime": "2021-01-19 00:00"}]'
    )
    assert rows[0].cancelled


def test_ids_are_strings_so_they_can_key_a_record():
    assert all(isinstance(m.id, str) for m in parse_meetings(FEED))


def test_a_row_with_no_time_has_no_start():
    rows = parse_meetings('[{"Id": 9, "Name": "x", "MeetingDateTime": ""}]')
    assert rows[0].starts_at is None


def test_an_unreadable_time_has_no_start_rather_than_crashing():
    rows = parse_meetings('[{"Id": 9, "Name": "x", "MeetingDateTime": "not a time"}]')
    assert rows[0].starts_at is None


def test_an_empty_feed_is_empty_not_an_error():
    assert parse_meetings("[]") == []


def test_html_instead_of_json_raises():
    with pytest.raises(ScraperError, match="did not return JSON"):
        parse_meetings("<html><body>Access Denied</body></html>")


def test_an_object_instead_of_a_list_raises():
    with pytest.raises(ScraperError, match="not a list"):
        parse_meetings('{"Message": "An error has occurred."}')


# --- the portal's date labels ------------------------------------------------


def test_the_trailing_date_label_is_split_off():
    assert split_label("Board Meetings - Aug 25 2026") == ("Board Meetings", "")


def test_text_after_the_date_is_kept():
    """"Board Meetings - Aug 25 2026 Budget" is the budget meeting, and an
    editor loses that if the whole tail is thrown away."""
    assert split_label("Board Meetings - Aug 25 2026 Budget") == (
        "Board Meetings",
        "Budget",
    )


def test_a_date_written_without_a_dash_is_still_a_label():
    assert split_label("Board Retreat/Strategic Planning Session Jan 20, 2026") == (
        "Board Retreat/Strategic Planning Session",
        "",
    )


def test_a_name_with_no_date_is_left_alone():
    assert short_name("Board Meeting Sarpy 101") == "Board Meeting Sarpy 101"


def test_service_url_takes_dates_or_strings():
    from datetime import date

    assert service_url("https://x.civicweb.net", date(2015, 1, 1)) == (
        "https://x.civicweb.net/Services/MeetingsService.svc/meetings"
        "?from=2015-01-01&to=9999-12-31"
    )
    assert "from=2020-02-02" in service_url("https://x.civicweb.net", "2020-02-02")
