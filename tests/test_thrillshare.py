"""The Thrillshare events reader, against a trimmed capture of the LPS feed."""

import json
from pathlib import Path

from scrapers.sources.thrillshare import Event, next_page, parse_events

PAYLOAD = json.loads(
    (Path(__file__).parent / "fixtures" / "lps_thrillshare_events.json").read_text()
)


def board():
    return [e for e in parse_events(PAYLOAD) if e.section == "Board of Education Calendar"]


def test_reads_every_event():
    assert len(parse_events(PAYLOAD)) == 19


def test_events_carry_the_section_they_belong_to():
    """One endpoint serves the whole district, and the filter it advertises does
    not work, so the section has to be read off each event."""
    assert len(board()) == 16
    assert {e.section for e in parse_events(PAYLOAD)} == {
        "Board of Education Calendar",
        "Academic Calendar",
    }


def test_a_timed_event_carries_a_central_time():
    first = min(board(), key=lambda e: e.starts_at)
    assert first.starts_at.isoformat() == "2026-09-22T18:00:00-05:00"
    assert first.all_day is False


def test_a_winter_event_is_central_standard_time():
    """These offsets are per-event and real -- November reads -06:00 while
    October reads -05:00. A feed emitting one fixed offset would put every
    winter meeting an hour out."""
    nov = [e for e in board() if e.starts_at.date().isoformat() == "2026-11-10"][0]
    assert nov.starts_at.isoformat() == "2026-11-10T18:00:00-06:00"


def test_an_all_day_event_has_no_time():
    """The API stamps these local midnight, which is a placeholder rather than
    an hour anyone meets at."""
    all_day = [e for e in parse_events(PAYLOAD) if e.all_day]
    assert all_day, "the fixture should keep a couple of all-day events"
    assert all(e.starts_at is None for e in all_day)
    raw = [e for e in PAYLOAD["events"] if e["all_day"]][0]
    assert raw["start_at"].endswith("T00:00:00.000-05:00"), "fixture shape changed"


def test_each_occurrence_of_a_recurring_meeting_is_its_own_event():
    """Recurrence needs no expanding here: the API sends one event per
    occurrence, each with its own id, and only `recurrency` links them."""
    recurring = [e for e in PAYLOAD["events"] if e.get("recurrency")]
    assert len(recurring) > 1
    assert len({e["id"] for e in recurring}) == len(recurring)
    assert len({e["recurrency"]["id"] for e in recurring}) < len(recurring)


def test_ids_are_strings_so_they_can_be_compared_and_keyed():
    assert all(isinstance(e.id, str) for e in parse_events(PAYLOAD))


def test_next_page_is_none_on_the_last_page():
    assert next_page(PAYLOAD) is None


def test_next_page_is_the_url_to_follow():
    payload = {"meta": {"links": {"next": "https://example.test/p2"}}}
    assert next_page(payload) == "https://example.test/p2"


def test_an_event_with_no_start_is_left_out_rather_than_crashing():
    events = parse_events({"events": [{"id": 1, "title": "x"}, {"id": 2, "title": "y"}]})
    assert events == []


def test_an_unreadable_start_is_left_out():
    events = parse_events(
        {"events": [{"id": 1, "title": "x", "start_at": "not a timestamp"}]}
    )
    assert events == []


def test_an_empty_response_is_empty_not_an_error():
    assert parse_events({}) == []
    assert parse_events({"events": None}) == []


def test_titles_and_venues_have_their_whitespace_collapsed():
    event = parse_events(
        {
            "events": [
                {
                    "id": 7,
                    "title": "  Board   of\n Education  ",
                    "start_at": "2026-10-13T18:00:00.000-05:00",
                    "venue": " Somewhere \n Else ",
                    "custom_section_name": "Board of Education Calendar",
                }
            ]
        }
    )[0]
    assert event == Event(
        id="7",
        title="Board of Education",
        starts_at=event.starts_at,
        section="Board of Education Calendar",
        venue="Somewhere Else",
    )
