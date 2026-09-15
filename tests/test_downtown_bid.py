import logging
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scrapers.agencies.downtown_bid import (
    DEFAULT_PLACE,
    INFERRED_NOTE,
    DowntownBid,
    external_id_for,
    last_tuesday,
    parse_archive,
    parse_details,
    parse_next_meeting,
)
from scrapers.base import ScraperError

LISTING = (Path(__file__).parent / "fixtures" / "odida_board_meetings.html").read_text()

# The fixture was saved 2026-09-14: the archive runs April 2025 to August 2026
# and the page announces September 29th as the next meeting.
TODAY = date(2026, 9, 14)
WIDE = dict(since=date(2025, 1, 1), until=date(2027, 12, 31))


def parse(listing=LISTING, today=TODAY, **kwargs):
    return DowntownBid(**kwargs).parse(listing, today=today)


def by_id(meetings):
    return {m.external_id: m for m in meetings}


def without(listing, needle):
    assert needle in listing, f"fixture no longer contains {needle!r}"
    return listing.replace(needle, "")


# --- the standing rule, as the page states it --------------------------------


def test_the_time_place_and_zoom_link_come_off_the_page():
    details = parse_details(LISTING)
    assert details["time"] == (17, 30)
    assert details["place"] == "RDG Planning & Design, 1302 Howard St, Omaha, NE 68102"
    assert details["livestream"] == "https://us02web.zoom.us/j/4029161796"


def test_the_page_still_states_the_rule_this_scraper_hardcodes():
    """The rule's shape -- last Tuesday, three skipped months -- is a constant.
    This pins the wording it was taken from, so a page that starts saying
    something else fails here rather than being silently overridden."""
    details = parse_details(LISTING)
    assert details["rule_matches"]
    assert details["skips_match"]


def test_a_changed_rule_is_warned_about_not_silently_kept(caplog):
    caplog.set_level(logging.WARNING)
    parse(without(LISTING, "Last Tuesday of each month at 5:30 PM"))
    assert "no longer says 'last Tuesday'" in caplog.text


def test_changed_skip_months_are_warned_about(caplog):
    caplog.set_level(logging.WARNING)
    parse(without(LISTING, "no meeting in July, Nov. or Dec."))
    assert "no longer says 'no meeting in July" in caplog.text


def test_a_missing_place_falls_back_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    meetings = parse(without(LISTING, "RDG Planning &amp; Design, 1302 Howard St, Omaha, NE 68102"))
    assert meetings[0].location == DEFAULT_PLACE
    assert "no place under Where:" in caplog.text


def test_last_tuesday():
    assert last_tuesday(2026, 9) == date(2026, 9, 29)
    assert last_tuesday(2026, 1) == date(2026, 1, 27)  # the month the board broke it
    assert last_tuesday(2027, 8) == date(2027, 8, 31)  # a month ending on a Tuesday
    assert all(last_tuesday(2027, m).weekday() == 1 for m in range(1, 13))


# --- the archive -------------------------------------------------------------


def test_the_archive_gives_thirteen_meetings_in_thirteen_months():
    """The premise of a month-keyed id."""
    rows = parse_archive(LISTING)
    assert len(rows) == 13
    assert len({r["day"] for r in rows.values()}) == 13


def test_twelve_of_thirteen_fell_on_the_last_tuesday():
    """And one did not: January 2026 was held on the Monday. That exception
    is why the computed date stays out of the key."""
    rows = parse_archive(LISTING)
    off = [r["day"] for r in rows.values() if r["day"] != last_tuesday(r["day"].year, r["day"].month)]
    assert off == [date(2026, 1, 26)]
    assert date(2026, 1, 26).weekday() == 0  # Monday


def test_an_abbreviated_month_still_parses():
    """2025's rows read "Oct. 28, 2025" and "Sept. 30, 2025"."""
    rows = parse_archive(LISTING)
    assert rows[(2025, 10)]["day"] == date(2025, 10, 28)
    assert rows[(2025, 9)]["day"] == date(2025, 9, 30)


def test_the_topic_and_agenda_link_are_read_off_the_row():
    row = parse_archive(LISTING)[(2026, 4)]
    assert row["topic"] == "ODIDA Annual Meeting"
    assert row["agenda_url"].startswith("https://acrobat.adobe.com/id/")


def test_no_meeting_rows_are_passed_over():
    rows = parse_archive(LISTING)
    assert (2025, 7) not in rows
    assert (2025, 11) not in rows and (2025, 12) not in rows


def test_a_second_meeting_in_one_month_is_skipped_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    # the June row becomes a second August one, listed after the real August 25
    listing = LISTING.replace("June 30, 2026", "August 11, 2026", 1)
    rows = parse_archive(listing)
    assert rows[(2026, 8)]["day"] == date(2026, 8, 25)  # the first row on the page wins
    assert "two archived meetings in 2026-08" in caplog.text


def test_a_changed_layout_raises_rather_than_returning_nothing():
    with pytest.raises(ScraperError, match="layout"):
        parse_details("<html><body><p>nothing</p></body></html>")


# --- the announced next meeting ----------------------------------------------


def test_the_next_meeting_line_gets_the_year_the_page_leaves_off():
    assert parse_next_meeting(LISTING, date(2026, 9, 14)) == date(2026, 9, 29)


def test_a_next_meeting_earlier_in_the_year_than_today_is_next_year():
    listing = LISTING.replace("September 29th", "January 26th")
    assert parse_next_meeting(listing, date(2026, 9, 14)) == date(2027, 1, 26)


def test_no_next_meeting_line_is_none_not_an_error():
    assert parse_next_meeting(without(LISTING, "September 29th"), TODAY) is None


def test_the_announced_date_is_checked_against_the_rule(caplog):
    """The only runtime signal that the inference is still good."""
    caplog.set_level(logging.WARNING)
    meetings = parse(LISTING.replace("September 29th", "September 22nd"))
    assert "last Tuesday of that month is 2026-09-29" in caplog.text
    assert by_id(meetings)["odida-2026-09"].starts_at.date() == date(2026, 9, 22)


def test_an_announced_meeting_in_a_skipped_month_is_published_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    meetings = parse(LISTING.replace("September 29th", "November 17th"))
    assert "odida-2026-11" in by_id(meetings)
    assert "in a month it says the board skips" in caplog.text


# --- the upsert key ----------------------------------------------------------


def test_the_key_is_the_month():
    assert external_id_for(2026, 1) == "odida-2026-01"


def test_a_computed_date_corrected_by_the_archive_keeps_its_key():
    """The failure the month key exists to prevent. Before January 2026 was
    archived the rule would have said the 27th; the archive says the 26th.
    Same record, updated -- not a second one with the first stranded."""
    before = parse(without(LISTING, "January 26, 2026"), today=date(2026, 1, 5))
    after = parse(today=date(2026, 1, 5))
    assert by_id(before)["odida-2026-01"].starts_at.date() == date(2026, 1, 27)
    assert by_id(after)["odida-2026-01"].starts_at.date() == date(2026, 1, 26)


def test_every_key_is_distinct():
    meetings = parse(**WIDE)
    assert len({m.external_id for m in meetings}) == len(meetings)


# --- the merge ---------------------------------------------------------------


def test_the_archive_wins_then_the_announcement_then_the_rule():
    meetings = by_id(parse(**WIDE))
    assert meetings["odida-2026-01"].starts_at.date() == date(2026, 1, 26)  # archive
    assert meetings["odida-2026-09"].starts_at.date() == date(2026, 9, 29)  # announced
    assert meetings["odida-2026-10"].starts_at.date() == date(2026, 10, 27)  # rule


def test_only_computed_meetings_carry_the_inferred_note():
    meetings = by_id(parse(**WIDE))
    assert meetings["odida-2026-10"].details == INFERRED_NOTE
    assert meetings["odida-2026-09"].details is None  # announced by the agency
    assert meetings["odida-2026-04"].details == "ODIDA Annual Meeting"  # archived


def test_the_skipped_months_produce_nothing():
    ids = by_id(parse(**WIDE))
    for year in (2025, 2026, 2027):
        for month in (7, 11, 12):
            assert f"odida-{year}-{month:02d}" not in ids


def test_the_default_window_reaches_about_a_year_ahead():
    meetings = parse()
    assert [m.starts_at.date() for m in meetings][:3] == [
        date(2026, 9, 29), date(2026, 10, 27), date(2027, 1, 26)
    ]
    assert meetings[-1].starts_at.date() == date(2027, 9, 28)


def test_the_zoom_link_is_the_livestream():
    assert all(
        m.livestream_url == "https://us02web.zoom.us/j/4029161796" for m in parse()
    )


def test_a_meeting_with_no_agenda_yet_still_goes_in():
    assert by_id(parse())["odida-2026-09"].agenda_url is None


def test_an_archived_meeting_carries_its_agenda():
    assert by_id(parse(**WIDE))["odida-2026-08"].agenda_url.startswith("https://acrobat.adobe.com/")


def test_a_winter_meeting_is_central_standard_time():
    assert by_id(parse())["odida-2027-01"].starts_at.isoformat() == "2027-01-26T17:30:00-06:00"


def test_meetings_come_back_in_chronological_order():
    meetings = parse(**WIDE)
    assert meetings == sorted(meetings, key=lambda m: m.starts_at)


def test_every_meeting_is_named_for_the_board():
    assert {m.name for m in parse()} == {"ODIDA Board of Directors Meeting"}


# --- failure modes -----------------------------------------------------------


def test_a_dead_site_raises_a_scraper_error():
    import requests

    with patch(
        "scrapers.agencies.downtown_bid.requests.get",
        side_effect=requests.ConnectionError("down"),
    ):
        with pytest.raises(ScraperError, match="Could not load"):
            DowntownBid().fetch()


def test_fetch_makes_one_request():
    response = Mock(text=LISTING)
    response.raise_for_status = Mock()
    with patch("scrapers.agencies.downtown_bid.requests.get", return_value=response) as get:
        DowntownBid().fetch()
    assert get.call_count == 1
