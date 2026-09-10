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
from datetime import date, datetime

from . import registry
from .base import BaseScraper, ScraperError
from .client import CollisionError, PlatformClient, PlatformError
from .meeting import Meeting

log = logging.getLogger("scrapers")


class UnexpectedCreate(RuntimeError):
    """A meeting was created when an existing record was expected.

    Only raised under --stop-on-unexpected-create, which exists for the first
    run against an agency that already has meetings on the platform: those
    should come back `adopted`, and a `created` means they were not matched and
    the run is about to duplicate them.
    """


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date in YYYY-MM-DD form"
        ) from None


OUTCOMES = ("created", "adopted", "updated", "duplicate")


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
    stop_on_unexpected_create: bool = False,
) -> dict[str, int]:
    """Upsert every scraped meeting and tally what the API said it did.

    There is no local pre-check: submission is keyed on externalId, so the
    platform itself decides between creating, adopting, updating and ignoring,
    and its answer is more trustworthy than anything we could work out here.
    """
    counts = dict.fromkeys((*OUTCOMES, "conflict", "failed"), 0)

    for meeting in meetings:
        payload = meeting.to_payload(scraper.agency_id)
        if dry_run:
            log.info("would POST %s", json.dumps(payload))
            counts["created"] += 1
            continue

        try:
            result = client.submit_meeting(payload)
        except CollisionError as exc:
            log.error(
                "collision on %s: %s. The meeting's externalId has changed "
                "since it was filed. Resolve it by hand -- see the recovery "
                "section of docs/api-notes.md.",
                meeting.date_time,
                exc,
            )
            counts["conflict"] += 1
            continue
        except PlatformError as exc:
            log.error("POST failed for %s: %s", meeting.date_time, exc)
            counts["failed"] += 1
            continue

        outcome = result.outcome
        counts[outcome] = counts.get(outcome, 0) + 1

        if outcome == "updated":
            log.info(
                "updated %s (%s): %s changed",
                meeting.date_time,
                meeting.external_id,
                ", ".join(result.changed) or "something",
            )
        elif outcome == "duplicate":
            log.debug("unchanged %s (%s)", meeting.date_time, meeting.external_id)
        else:
            log.info("%s %s (%s)", outcome, meeting.date_time, result.id)

        if outcome == "created" and stop_on_unexpected_create:
            raise UnexpectedCreate(
                f"{meeting.name} on {meeting.date_time} was created rather than "
                "adopted, which means the meeting was not recognized as one "
                "already on the platform. Stopping before this run can create "
                "duplicates of everything else. Re-run without "
                "--stop-on-unexpected-create once you have checked why."
            )
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
        "--stop-on-unexpected-create",
        action="store_true",
        help="abort the moment a meeting is created rather than adopted -- use "
        "on the first run against an agency whose meetings were submitted "
        "before externalId existed",
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
        print(f"scraped {len(meetings)}")
        return 0

    try:
        client = PlatformClient()
        verify_agency(client, scraper)
        counts = submit(
            client, scraper, meetings, args.dry_run, args.stop_on_unexpected_create
        )
    except UnexpectedCreate as exc:
        log.error("%s", exc)
        return 1
    except PlatformError as exc:
        log.error("%s", exc)
        return 1

    prefix = "DRY RUN: " if args.dry_run else ""
    tally = " / ".join(f"{name} {counts[name]}" for name in OUTCOMES)
    print(
        f"{prefix}scraped {len(meetings)} / {tally} / "
        f"conflict {counts['conflict']} / failed {counts['failed']}"
    )
    return 1 if counts["failed"] or counts["conflict"] else 0


if __name__ == "__main__":
    sys.exit(main())
