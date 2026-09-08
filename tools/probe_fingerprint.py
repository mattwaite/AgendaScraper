"""One-off experiment: work out what the API's importFingerprint covers.

The dedup rule is undocumented, and it matters in both directions. If `name` is
part of the fingerprint, changing our name format later duplicates every meeting
we have ever submitted. If the fingerprint were agency+name only, every meeting
after the first would be wrongly rejected.

Run once, read the summary, write the findings into docs/api-notes.md:

    python3 -m tools.probe_fingerprint --confirm

Step 5 creates one junk record (a real meeting under a bogus name) that has to
be removed by hand from the admin dashboard -- there is no delete endpoint.
"""

import argparse
import json
import sys
from pathlib import Path

from scrapers.client import PlatformClient
from scrapers.registry import get

SLUG = "lincoln_city_council"
RESULTS = Path(__file__).resolve().parent.parent / "docs" / "probe-results.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: this writes real records to the live platform",
    )
    args = parser.parse_args()
    if not args.confirm:
        parser.error("re-run with --confirm; this POSTs to the live platform")

    client = PlatformClient()
    scraper = get(SLUG)()
    meetings = scraper.fetch()
    if len(meetings) < 2:
        # Nothing in the default rolling window today, so reach back for the two
        # most recent real meetings instead of inventing data.
        scraper = get(SLUG)()
        scraper.since = None
        meetings = scraper.parse(
            _cached_html(scraper), days_back=400, days_forward=400
        )
    if len(meetings) < 2:
        sys.exit("Need at least two real meetings to probe with.")

    a, b = meetings[-1], meetings[-2]
    log: list[dict] = []

    def post(label: str, payload: dict) -> dict:
        result = client.create_meeting(payload)
        entry = {
            "step": label,
            "payload": payload,
            "id": result.id,
            "created": result.created,
            "reason": result.reason,
        }
        print(f"[{label}] created={result.created} reason={result.reason} id={result.id}")
        log.append(entry)
        return entry

    payload_a = a.to_payload(scraper.agency_id)
    payload_b = b.to_payload(scraper.agency_id)

    print("\n1. baseline POST")
    post("1-baseline", payload_a)

    print("\n2. read the stored record back")
    stored = client.list_meetings(scraper.agency_id)
    print(json.dumps(stored[:2], indent=2)[:2000])
    log.append({"step": "2-readback", "meetings": stored})

    print("\n3. identical repost -- expect created=false")
    post("3-identical", payload_a)

    print("\n4. same name, different dateTime -- expect created=true")
    post("4-different-datetime", payload_b)

    print("\n5. same dateTime, perturbed name -- the question")
    perturbed = dict(payload_a, name=payload_a["name"] + " (fingerprint probe)")
    entry = post("5-perturbed-name", perturbed)

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(log, indent=2) + "\n")

    print("\n--- conclusion ---")
    if entry["created"]:
        print(
            "`name` IS part of importFingerprint: the name format must stay frozen, "
            "or every future run duplicates every meeting.\n"
            f"Junk record to delete by hand: id={entry['id']}"
        )
    else:
        print(
            "`name` is NOT part of importFingerprint (dedup keys on agency + "
            "dateTime, and possibly agendaUrl). No junk record was created."
        )
    print(f"Raw log: {RESULTS}")
    return 0


def _cached_html(scraper) -> str:
    import asyncio

    return asyncio.run(scraper._load_html())


if __name__ == "__main__":
    sys.exit(main())
