import json
from datetime import UTC, date, datetime
from unittest.mock import Mock, patch

import pytest

from scrapers.client import AuthError, PlatformClient, PlatformError
from scrapers.meeting import CENTRAL, Meeting
from scrapers.run import existing_keys, fingerprint_key, name_date_key

AGENCY = "cmryg8k2p0001s91mclkzpdnq"


def make_meeting(**kwargs):
    defaults = dict(
        name="Lincoln City Council Regular Meeting",
        starts_at=datetime(2026, 8, 31, 17, 30, tzinfo=CENTRAL),
        location="Council Chambers",
        agenda_url="https://lnklan.granicus.com/AgendaViewer.php?clip_id=426",
    )
    return Meeting(**{**defaults, **kwargs})


def make_client():
    return PlatformClient(api_key="pk_test", base_url="https://example.test/api/v1")


def fake_response(status, body=None):
    response = Mock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.json.return_value = body or {}
    response.text = json.dumps(body or {})
    return response


# --- Meeting -----------------------------------------------------------------


def test_payload_has_every_required_api_field():
    payload = make_meeting().to_payload(AGENCY)
    assert payload == {
        "name": "Lincoln City Council Regular Meeting",
        "dateTime": "2026-08-31T17:30:00-05:00",
        "agencyId": AGENCY,
        "location": "Council Chambers",
        "agendaUrl": "https://lnklan.granicus.com/AgendaViewer.php?clip_id=426",
        "meetingType": "REGULAR",
    }


def test_optional_fields_are_omitted_when_empty():
    assert "details" not in make_meeting().to_payload(AGENCY)
    assert make_meeting(details="Budget").to_payload(AGENCY)["details"] == "Budget"


def test_fingerprint_matches_the_value_the_api_returned():
    """Observed live on 2026-09-08; see docs/api-notes.md."""
    assert make_meeting().fingerprint(AGENCY) == (
        f"{AGENCY}::lincoln city council regular meeting::1788215400000"
    )


def test_invalid_meeting_type_is_rejected():
    with pytest.raises(ValueError, match="meeting_type"):
        make_meeting(meeting_type="ANNUAL")


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_meeting(starts_at=datetime(2026, 8, 31, 17, 30))


def test_empty_agenda_url_is_rejected():
    with pytest.raises(ValueError, match="agenda_url"):
        make_meeting(agenda_url="")


# --- PlatformClient ----------------------------------------------------------


def test_missing_key_fails_with_a_useful_message(monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "")
    with patch("scrapers.client.load_dotenv"):
        with pytest.raises(PlatformError, match="PLATFORM_API_KEY"):
            PlatformClient()


def test_201_counts_as_created():
    client = make_client()
    with patch.object(
        client.session, "request", return_value=fake_response(201, {"id": "x", "created": True})
    ):
        result = client.create_meeting({})
    assert (result.created, result.id) == (True, "x")


def test_200_with_created_false_is_a_duplicate_not_an_error():
    client = make_client()
    body = {"id": "x", "created": False, "reason": "duplicate"}
    with patch.object(client.session, "request", return_value=fake_response(200, body)):
        result = client.create_meeting({})
    assert result.created is False
    assert result.reason == "duplicate"


def test_401_raises_auth_error():
    client = make_client()
    with patch.object(client.session, "request", return_value=fake_response(401)):
        with pytest.raises(AuthError):
            client.create_meeting({})


def test_unexpected_status_raises():
    client = make_client()
    with patch.object(client.session, "request", return_value=fake_response(422)):
        with pytest.raises(PlatformError, match="422"):
            client.create_meeting({})


def test_get_agency_matches_on_id_not_name():
    client = make_client()
    agencies = [{"id": AGENCY, "name": "Lincoln City Council (renamed)"}]
    with patch.object(client, "list_agencies", return_value=agencies):
        assert client.get_agency(AGENCY)["name"] == "Lincoln City Council (renamed)"
        assert client.get_agency("nope") is None


# --- dedup -------------------------------------------------------------------


class FakeScraper:
    agency_id = AGENCY


def as_stored(meeting):
    """A record shaped the way GET /meetings really answers.

    Captured live 2026-09-08: no agendaUrl, no source id -- name, dateTime,
    location and importFingerprint are all we get back.
    """
    return {
        "id": "cm_stored",
        "name": meeting.name,
        "dateTime": meeting.starts_at.astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
        "location": meeting.location,
        "importFingerprint": meeting.fingerprint(AGENCY),
    }


def keys_for(client, meetings, stored):
    with patch.object(client, "list_meetings", return_value=stored):
        return existing_keys(client, FakeScraper(), meetings)


def test_existing_fingerprint_blocks_a_repost():
    meeting = make_meeting()
    known = keys_for(make_client(), [meeting], [as_stored(meeting)])
    assert fingerprint_key(meeting, AGENCY) in known


def test_rescheduled_meeting_is_caught_by_name_and_date():
    """A new time is a new fingerprint, so the API would create a second record
    and there is no endpoint to delete it. Only name + date can catch this --
    the response carries nothing else that identifies the same meeting."""
    stored = make_meeting()
    moved = make_meeting(starts_at=datetime(2026, 8, 31, 19, 0, tzinfo=CENTRAL))
    assert moved.fingerprint(AGENCY) != stored.fingerprint(AGENCY)

    known = keys_for(make_client(), [moved], [as_stored(stored)])
    assert fingerprint_key(moved, AGENCY) not in known
    assert name_date_key(moved.name, moved.starts_at.date()) in known


def test_stored_utc_times_map_back_to_the_central_date():
    """A 5:30 PM Central meeting is stored as 22:30 UTC -- same calendar day.
    An 8:00 PM one is stored as 01:00 UTC the next day, and must not be read as
    belonging to that next day."""
    late = make_meeting(starts_at=datetime(2026, 8, 31, 20, 0, tzinfo=CENTRAL))
    stored = as_stored(late)
    assert stored["dateTime"].startswith("2026-09-01T01:00")

    known = keys_for(make_client(), [late], [stored])
    assert name_date_key(late.name, date(2026, 8, 31)) in known
    assert name_date_key(late.name, date(2026, 9, 1)) not in known


def test_a_genuinely_new_meeting_is_not_flagged():
    new = make_meeting(
        starts_at=datetime(2026, 9, 14, 15, 0, tzinfo=CENTRAL),
        agenda_url="https://lnklan.granicus.com/AgendaViewer.php?clip_id=430",
    )
    known = keys_for(make_client(), [new], [as_stored(make_meeting())])
    assert fingerprint_key(new, AGENCY) not in known
    assert name_date_key(new.name, new.starts_at.date()) not in known


def test_same_day_meetings_of_different_types_do_not_collide():
    """Pre-council at 1:00 and the regular session at 3:00 have different names,
    so the name+date guard leaves both alone."""
    regular = make_meeting(starts_at=datetime(2026, 9, 14, 15, 0, tzinfo=CENTRAL))
    workshop = make_meeting(
        name="Lincoln City Council Work Session",
        meeting_type="WORKSHOP",
        starts_at=datetime(2026, 9, 14, 13, 0, tzinfo=CENTRAL),
    )
    known = keys_for(make_client(), [workshop], [as_stored(regular)])
    assert name_date_key(workshop.name, workshop.starts_at.date()) not in known
