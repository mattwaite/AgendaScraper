import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from scrapers.client import (
    AuthError,
    CollisionError,
    PlatformClient,
    PlatformError,
    SubmitResult,
)
from scrapers.meeting import CENTRAL, Meeting
from scrapers.run import UnexpectedCreate, carry_forward, submit

AGENCY = "cmryg8k2p0001s91mclkzpdnq"


def make_meeting(**kwargs):
    defaults = dict(
        name="Lincoln City Council Regular Meeting",
        starts_at=datetime(2026, 8, 31, 17, 30, tzinfo=CENTRAL),
        external_id="lnk-clip-426",
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


class FakeScraper:
    agency_id = AGENCY
    slug = "fake"


# --- Meeting -----------------------------------------------------------------


def test_payload_has_every_field_the_api_expects():
    assert make_meeting().to_payload(AGENCY) == {
        "name": "Lincoln City Council Regular Meeting",
        "dateTime": "2026-08-31T17:30:00-05:00",
        "agencyId": AGENCY,
        "externalId": "lnk-clip-426",
        "location": "Council Chambers",
        "agendaUrl": "https://lnklan.granicus.com/AgendaViewer.php?clip_id=426",
        "meetingType": "REGULAR",
    }


def test_a_meeting_with_no_agenda_yet_is_still_submittable():
    """Only name, dateTime and agencyId are required, so a meeting can go in as
    soon as it is scheduled and gain its agenda URL on a later run."""
    payload = make_meeting(agenda_url=None).to_payload(AGENCY)
    assert "agendaUrl" not in payload
    assert payload["dateTime"] and payload["name"] and payload["agencyId"]


def test_optional_fields_are_omitted_when_empty():
    assert "details" not in make_meeting().to_payload(AGENCY)
    assert make_meeting(details="Budget").to_payload(AGENCY)["details"] == "Budget"


def test_invalid_meeting_type_is_rejected_before_it_reaches_the_api():
    with pytest.raises(ValueError, match="meeting_type"):
        make_meeting(meeting_type="ANNUAL")


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_meeting(starts_at=datetime(2026, 8, 31, 17, 30))


def test_external_id_is_required():
    with pytest.raises(ValueError, match="external_id"):
        make_meeting(external_id="")


# --- SubmitResult ------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"created": True}, "created"),
        ({"created": False, "reason": "adopted"}, "adopted"),
        ({"created": False, "updated": True, "changed": ["dateTime"]}, "updated"),
        ({"created": False, "reason": "duplicate"}, "duplicate"),
        ({"created": False}, "duplicate"),
    ],
)
def test_outcome_reads_every_documented_response_shape(kwargs, expected):
    assert SubmitResult(id="x", **kwargs).outcome == expected


# --- PlatformClient ----------------------------------------------------------


def test_missing_key_fails_with_a_useful_message(monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "")
    with patch("scrapers.client.load_dotenv"):
        with pytest.raises(PlatformError, match="PLATFORM_API_KEY"):
            PlatformClient()


def test_201_is_a_create():
    client = make_client()
    body = {"id": "x", "created": True}
    with patch.object(client.session, "request", return_value=fake_response(201, body)):
        assert client.submit_meeting({}).outcome == "created"


def test_200_updated_carries_the_changed_fields():
    client = make_client()
    body = {"id": "x", "created": False, "updated": True, "changed": ["dateTime"]}
    with patch.object(client.session, "request", return_value=fake_response(200, body)):
        result = client.submit_meeting({})
    assert result.outcome == "updated"
    assert result.changed == ["dateTime"]


def test_200_adopted_is_reported_as_such():
    client = make_client()
    body = {"id": "x", "created": False, "reason": "adopted"}
    with patch.object(client.session, "request", return_value=fake_response(200, body)):
        assert client.submit_meeting({}).outcome == "adopted"


def test_409_raises_a_collision_naming_the_external_id():
    client = make_client()
    with patch.object(client.session, "request", return_value=fake_response(409)):
        with pytest.raises(CollisionError, match="lnk-clip-426"):
            client.submit_meeting({"externalId": "lnk-clip-426"})


def test_401_raises_auth_error():
    client = make_client()
    with patch.object(client.session, "request", return_value=fake_response(401)):
        with pytest.raises(AuthError):
            client.submit_meeting({})


def test_unexpected_status_raises():
    client = make_client()
    with patch.object(client.session, "request", return_value=fake_response(422)):
        with pytest.raises(PlatformError, match="422"):
            client.submit_meeting({})


def test_delete_returns_false_when_an_assignment_blocks_it():
    client = make_client()
    body = {"assignmentCount": 2}
    with patch.object(client.session, "request", return_value=fake_response(409, body)):
        assert client.delete_meeting("x") is False


def test_delete_succeeds():
    client = make_client()
    body = {"deleted": True}
    with patch.object(client.session, "request", return_value=fake_response(200, body)):
        assert client.delete_meeting("x") is True


def test_get_agency_matches_on_id_not_name():
    client = make_client()
    agencies = [{"id": AGENCY, "name": "Lincoln City Council (renamed)"}]
    with patch.object(client, "list_agencies", return_value=agencies):
        assert client.get_agency(AGENCY)["name"] == "Lincoln City Council (renamed)"
        assert client.get_agency("nope") is None


def test_reads_are_not_throttled():
    """The courtesy rate limit is for writes; a run's GETs shouldn't pay it."""
    client = make_client()
    with patch.object(client, "_throttle") as throttle:
        with patch.object(
            client.session, "request", return_value=fake_response(200, [])
        ):
            client.list_agencies()
    throttle.assert_not_called()


def test_writes_are_throttled():
    client = make_client()
    with patch.object(client, "_throttle") as throttle:
        with patch.object(
            client.session, "request", return_value=fake_response(201, {"id": "x"})
        ):
            client.submit_meeting({})
    throttle.assert_called_once()


# --- submit ------------------------------------------------------------------


def submit_with(outcomes, stored=None, **kwargs):
    """Run submit() over N meetings, with the API returning `outcomes` in turn."""
    client = make_client()
    meetings = [
        make_meeting(external_id=f"lnk-clip-{i}") for i in range(len(outcomes))
    ]
    with patch.object(client, "list_meetings", return_value=stored or []):
        with patch.object(client, "submit_meeting", side_effect=outcomes):
            return submit(client, FakeScraper(), meetings, dry_run=False, **kwargs)


def test_counts_every_outcome_separately():
    counts = submit_with(
        [
            SubmitResult(id="a", created=True),
            SubmitResult(id="b", created=False, reason="adopted"),
            SubmitResult(id="c", created=False, updated=True, changed=["dateTime"]),
            SubmitResult(id="d", created=False, reason="duplicate"),
        ]
    )
    assert counts["created"] == 1
    assert counts["adopted"] == 1
    assert counts["updated"] == 1
    assert counts["duplicate"] == 1
    assert counts["failed"] == 0


def test_a_collision_is_counted_apart_from_a_failure():
    counts = submit_with([CollisionError("409 for lnk-clip-0")])
    assert counts["conflict"] == 1
    assert counts["failed"] == 0


def test_other_errors_count_as_failures_without_stopping_the_run():
    counts = submit_with(
        [PlatformError("boom"), SubmitResult(id="b", created=True)]
    )
    assert counts["failed"] == 1
    assert counts["created"] == 1


def test_stop_on_unexpected_create_aborts_the_run():
    """The guard for a first run against an agency that already has meetings:
    they should be adopted, and a create means the run is about to duplicate."""
    with pytest.raises(UnexpectedCreate, match="created rather than adopted"):
        submit_with(
            [SubmitResult(id="a", created=True), SubmitResult(id="b", created=True)],
            stop_on_unexpected_create=True,
        )


def test_dry_run_never_calls_the_api():
    client = make_client()
    with patch.object(client, "submit_meeting") as api:
        counts = submit(client, FakeScraper(), [make_meeting()], dry_run=True)
    api.assert_not_called()
    assert counts["created"] == 1


# --- carrying values forward -------------------------------------------------
#
# Submitting replaces the whole record rather than merging into it, so a payload
# that omits agendaUrl clears the stored one. Verified against the live API on
# 2026-09-10; see docs/api-notes.md.


def test_a_value_the_scrape_missed_is_kept_from_the_stored_record():
    payload = make_meeting(agenda_url=None).to_payload(AGENCY)
    assert "agendaUrl" not in payload

    merged = carry_forward(payload, {"agendaUrl": "https://example.test/old.pdf"})
    assert merged["agendaUrl"] == "https://example.test/old.pdf"


def test_a_freshly_scraped_value_wins_over_the_stored_one():
    payload = make_meeting(agenda_url="https://example.test/new.pdf").to_payload(AGENCY)
    merged = carry_forward(payload, {"agendaUrl": "https://example.test/old.pdf"})
    assert merged["agendaUrl"] == "https://example.test/new.pdf"


def test_nothing_is_invented_when_the_platform_has_nothing():
    payload = make_meeting(agenda_url=None).to_payload(AGENCY)
    assert "agendaUrl" not in carry_forward(payload, {})
    assert "agendaUrl" not in carry_forward(payload, None)


def test_every_optional_field_is_carried_forward():
    payload = make_meeting(agenda_url=None, location=None).to_payload(AGENCY)
    stored = {
        "agendaUrl": "https://example.test/a.pdf",
        "location": "Room 112",
        "details": "budget",
        "livestreamUrl": "https://example.test/live",
        "contactPerson": "Jane Doe",
    }
    merged = carry_forward(payload, stored)
    for field, value in stored.items():
        assert merged[field] == value


def test_required_fields_are_never_taken_from_the_stored_record():
    """A stale name or time must not leak back in -- those come from the scrape."""
    payload = make_meeting().to_payload(AGENCY)
    merged = carry_forward(
        payload, {"name": "Old Name", "dateTime": "1999-01-01T00:00:00-06:00"}
    )
    assert merged["name"] == "Lincoln City Council Regular Meeting"
    assert merged["dateTime"] == "2026-08-31T17:30:00-05:00"


def test_submit_reads_existing_meetings_once_and_merges_them_in():
    client = make_client()
    meetings = [make_meeting(agenda_url=None)]
    stored = [
        {
            "externalId": "lnk-clip-426",
            "agendaUrl": "https://example.test/kept.pdf",
        }
    ]
    with patch.object(client, "list_meetings", return_value=stored) as reader:
        with patch.object(
            client, "submit_meeting", return_value=SubmitResult(id="x", created=False)
        ) as writer:
            submit(client, FakeScraper(), meetings, dry_run=False)
    reader.assert_called_once()
    assert writer.call_args[0][0]["agendaUrl"] == "https://example.test/kept.pdf"


def test_submit_carries_on_when_existing_meetings_cannot_be_read():
    client = make_client()
    with patch.object(client, "list_meetings", side_effect=PlatformError("down")):
        with patch.object(
            client, "submit_meeting", return_value=SubmitResult(id="x", created=True)
        ):
            counts = submit(client, FakeScraper(), [make_meeting()], dry_run=False)
    assert counts["created"] == 1
