"""Prove the API's upsert behaves as documented, against the test agency.

Everything in this project rests on one assumption: submitting the same
externalId twice updates one record rather than creating two. This exercises
that end to end -- create, repost unchanged, reschedule, rename -- against
ZZ Test Agency, then deletes what it made.

Run it after any API change, and before the first real run of a new scraper:

    python3 -m tools.verify_upsert

Replaces tools/probe_fingerprint.py, which worked out the old fingerprint rules
by experiment before the API documented them. Its findings are kept in
docs/probe-results.json for the record.
"""

import sys
from datetime import datetime, timedelta

from scrapers.client import PlatformClient
from scrapers.meeting import CENTRAL, Meeting

TEST_AGENCY_NAME = "ZZ Test Agency"


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {detail}")
    return ok


def main() -> int:
    client = PlatformClient()

    agencies = client.list_agencies(search="zz test")
    if not agencies:
        sys.exit(f"{TEST_AGENCY_NAME} not found -- check GET /agencies.")
    agency_id = agencies[0]["id"]
    print(f"Using {agencies[0]['name']} ({agency_id})\n")

    # A unique id per run, so repeated runs never collide with each other.
    stamp = datetime.now(CENTRAL).strftime("%Y%m%d%H%M%S")
    external_id = f"zz-verify-{stamp}"
    starts_at = datetime.now(CENTRAL).replace(
        hour=15, minute=0, second=0, microsecond=0
    ) + timedelta(days=30)

    meeting = Meeting(
        name="ZZ Verification Meeting",
        starts_at=starts_at,
        external_id=external_id,
        location="Nowhere",
        agenda_url="https://example.invalid/agenda",
    )

    passed = True
    meeting_id = None
    try:
        print("1. first submission")
        result = client.submit_meeting(meeting.to_payload(agency_id))
        meeting_id = result.id
        passed &= check("creates", result.outcome == "created", result.outcome)

        print("2. resubmit, nothing changed")
        result = client.submit_meeting(meeting.to_payload(agency_id))
        passed &= check("is a duplicate", result.outcome == "duplicate", result.outcome)
        passed &= check("same record", result.id == meeting_id, str(result.id))

        print("3. resubmit with a new time")
        moved = Meeting(
            name=meeting.name,
            starts_at=starts_at + timedelta(hours=2, days=1),
            external_id=external_id,
            location=meeting.location,
            agenda_url=meeting.agenda_url,
        )
        result = client.submit_meeting(moved.to_payload(agency_id))
        passed &= check("updates", result.outcome == "updated", result.outcome)
        passed &= check("same record", result.id == meeting_id, str(result.id))
        passed &= check(
            "reports dateTime changed", "dateTime" in result.changed, str(result.changed)
        )

        print("4. resubmit with a new name")
        renamed = Meeting(
            name="ZZ Verification Meeting (renamed)",
            starts_at=moved.starts_at,
            external_id=external_id,
            location=meeting.location,
            agenda_url=meeting.agenda_url,
        )
        result = client.submit_meeting(renamed.to_payload(agency_id))
        passed &= check("updates", result.outcome == "updated", result.outcome)
        passed &= check("same record", result.id == meeting_id, str(result.id))

        print("5. only one record exists")
        rows = [
            m
            for m in client.list_meetings(agency_id)
            if m.get("externalId") == external_id
        ]
        passed &= check("exactly one row", len(rows) == 1, f"{len(rows)} row(s)")
        if rows:
            passed &= check(
                "holds the latest values",
                rows[0]["name"] == renamed.name,
                rows[0]["name"],
            )
    finally:
        if meeting_id:
            print("\ncleaning up")
            deleted = client.delete_meeting(meeting_id)
            check("deleted the test meeting", deleted, str(deleted))

    print("\nPASS -- upsert behaves as documented" if passed else "\nFAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
