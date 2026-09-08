"""CLI: scrape one agency and push its meetings to the platform API.

    python -m scrapers.run --list
    python -m scrapers.run lincoln_city_council --dry-run
    python -m scrapers.run lincoln_city_council
    python -m scrapers.run lincoln_city_council --format csv --out meetings.csv
"""

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime, timedelta

from . import registry
from .base import BaseScraper, ScraperError
from .client import PlatformClient, PlatformError
from .meeting import CENTRAL, Meeting

log = logging.getLogger("scrapers")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date in YYYY-MM-DD form"
        ) from None


def existing_keys(client: PlatformClient, scraper: BaseScraper, meetings: list[Meeting]) -> set:
    """Keys for meetings the platform already has, so we never POST a known dupe.

    Two kinds of key, because they catch different things:

    * the importFingerprint (agencyId::lowercased name::epoch ms) -- an exact
      match on what the server itself dedups with.
    * (name, local date) -- catches a meeting whose *time* changed at the source.
      A new time is a new fingerprint, so the API would happily create a second
      record, and there is no PATCH or DELETE to clean up after it.

    The second key is a heuristic, because GET /meetings returns only id, name,
    dateTime, location and importFingerprint -- no agendaUrl, and nothing else
    that would identify the same real-world meeting across a time change.
    """
    if not meetings:
        return set()
    # Pad a day either side: the API's `to` bound cuts at midnight, so an
    # unpadded query silently omits meetings on the last day of the range.
    window_start = min(m.starts_at for m in meetings).date() - timedelta(days=1)
    window_end = max(m.starts_at for m in meetings).date() + timedelta(days=1)
    existing = client.list_meetings(
        scraper.agency_id, from_=window_start, to=window_end
    )

    keys = set()
    for record in existing:
        if fingerprint := record.get("importFingerprint"):
            keys.add(("fingerprint", fingerprint))
        raw_dt, name = record.get("dateTime"), record.get("name")
        if raw_dt and name:
            try:
                stored = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            except ValueError:
                continue
            keys.add(name_date_key(name, stored.astimezone(CENTRAL).date()))
    return keys


def name_date_key(name: str, day: date) -> tuple:
    return ("name-date", name.lower(), day.isoformat())


def fingerprint_key(meeting: Meeting, agency_id: str) -> tuple:
    return ("fingerprint", meeting.fingerprint(agency_id))


def write_rows(meetings: list[Meeting], fmt: str, out: str | None) -> None:
    rows = [m.to_row() for m in meetings]
    handle = open(out, "w", newline="") if out else sys.stdout
    try:
        if fmt == "json":
            json.dump(rows, handle, indent=2)
            handle.write("\n")
        else:
            writer = csv.DictWriter(
                handle, fieldnames=list(rows[0].keys()) if rows else ["date"]
            )
            writer.writeheader()
            writer.writerows(rows)
    finally:
        if out:
            handle.close()
    if out:
        print(f"Wrote {len(rows)} meetings to {out}")


def submit(
    client: PlatformClient,
    scraper: BaseScraper,
    meetings: list[Meeting],
    dry_run: bool,
    allow_same_day: bool = False,
) -> dict[str, int]:
    counts = {"new": 0, "duplicate": 0, "time-changed": 0, "failed": 0}
    known = existing_keys(client, scraper, meetings)

    for meeting in meetings:
        if fingerprint_key(meeting, scraper.agency_id) in known:
            log.debug("already on the platform: %s", meeting.date_time)
            counts["duplicate"] += 1
            continue

        same_day = name_date_key(meeting.name, meeting.starts_at.date())
        if same_day in known and not allow_same_day:
            log.warning(
                "%s on %s is already on the platform at a different time. Its "
                "time appears to have changed; submitting would create a second "
                "record that cannot be deleted, so it is being skipped. Fix the "
                "time by hand, or re-run with --allow-same-day if this really is "
                "a second meeting that day.",
                meeting.name,
                meeting.starts_at.date().isoformat(),
            )
            counts["time-changed"] += 1
            continue

        payload = meeting.to_payload(scraper.agency_id)
        if dry_run:
            log.info("would POST %s", json.dumps(payload))
            counts["new"] += 1
            continue

        try:
            result = client.create_meeting(payload)
        except PlatformError as exc:
            log.error("POST failed for %s: %s", meeting.date_time, exc)
            counts["failed"] += 1
            continue

        if result.created:
            log.info("created %s (%s)", meeting.date_time, result.id)
            counts["new"] += 1
        else:
            log.debug(
                "duplicate per API (%s): %s", result.reason, meeting.date_time
            )
            counts["duplicate"] += 1
    return counts


def verify_agency(client: PlatformClient, scraper: BaseScraper) -> None:
    """Confirm the hardcoded cuid still exists, and warn if its name drifted.

    Looking up by id rather than name is deliberate: agency names get edited
    (the platform currently lists "Omaha Streetcar Authoridy").
    """
    agency = client.get_agency(scraper.agency_id)
    if agency is None:
        raise PlatformError(
            f"Agency id {scraper.agency_id} for {scraper.slug} was not found. "
            "It may have been deleted or recreated -- check GET /agencies."
        )
    if agency.get("name") != scraper.agency_name:
        log.warning(
            "Agency %s is now named %r, expected %r. Data still goes to the "
            "right id; update agency_name when convenient.",
            scraper.agency_id,
            agency.get("name"),
            scraper.agency_name,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", nargs="?", help="which scraper to run")
    parser.add_argument("--list", action="store_true", help="list known scrapers")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scrape and dedup-check, print payloads, POST nothing",
    )
    parser.add_argument(
        "--format",
        choices=("api", "csv", "json"),
        default="api",
        help="api submits to the platform; csv/json just write the meetings out",
    )
    parser.add_argument("--out", help="file to write for --format csv/json")
    parser.add_argument("--since", type=parse_date, help="earliest date, YYYY-MM-DD")
    parser.add_argument("--until", type=parse_date, help="latest date, YYYY-MM-DD")
    parser.add_argument(
        "--limit",
        type=int,
        help="keep only the earliest N meetings -- meant for --dry-run and "
        "--format csv/json, since submitting the oldest few is rarely what "
        "you want and records cannot be deleted",
    )
    parser.add_argument(
        "--allow-same-day",
        action="store_true",
        help="submit a meeting even when one with the same name already exists "
        "on that date at a different time (see --help for --limit's warning)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.list:
        for slug in sorted(registry.SCRAPERS):
            print(slug)
        return 0
    if not args.slug:
        parser.error("give a scraper slug, or --list")

    try:
        scraper = registry.get(args.slug)(since=args.since, until=args.until)
    except KeyError as exc:
        parser.error(str(exc))

    try:
        meetings = scraper.fetch()
    except ScraperError as exc:
        log.error("scrape failed: %s", exc)
        return 1

    if args.limit:
        meetings = meetings[: args.limit]

    if args.format in ("csv", "json"):
        write_rows(meetings, args.format, args.out)
        print(
            f"scraped {len(meetings)} / "
            f"skipped-no-agenda {scraper.skipped_no_agenda}"
        )
        return 0

    try:
        client = PlatformClient()
        verify_agency(client, scraper)
        counts = submit(
            client, scraper, meetings, args.dry_run, args.allow_same_day
        )
    except PlatformError as exc:
        log.error("%s", exc)
        return 1

    prefix = "DRY RUN: " if args.dry_run else ""
    print(
        f"{prefix}scraped {len(meetings)} / new {counts['new']} / "
        f"duplicate {counts['duplicate']} / "
        f"time-changed {counts['time-changed']} / "
        f"skipped-no-agenda {scraper.skipped_no_agenda} / "
        f"failed {counts['failed']}"
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
