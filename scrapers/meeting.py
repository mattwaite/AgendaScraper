"""The normalized meeting record every scraper returns."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Every agency in this project is in Central time. Using a named zone rather than
# a fixed offset keeps -06:00 in winter and -05:00 in summer without extra work.
CENTRAL = ZoneInfo("America/Chicago")

MEETING_TYPES = ("REGULAR", "SPECIAL", "EMERGENCY", "WORKSHOP", "HEARING")


@dataclass(frozen=True)
class Meeting:
    """One scheduled meeting, in the shape the platform API expects.

    `name` is deliberately a constant per (agency, meeting type) rather than
    something that varies per meeting -- see docs/api-notes.md. The date lives
    in `starts_at` only.
    """

    name: str
    starts_at: datetime  # timezone-aware
    location: str
    agenda_url: str
    meeting_type: str = "REGULAR"
    details: str | None = None
    livestream_url: str | None = None
    contact_person: str | None = None
    source_id: str | None = None  # e.g. Granicus clip_id; local bookkeeping only

    def __post_init__(self) -> None:
        if self.meeting_type not in MEETING_TYPES:
            raise ValueError(
                f"meeting_type {self.meeting_type!r} is not one of {MEETING_TYPES}"
            )
        if self.starts_at.tzinfo is None:
            raise ValueError(f"starts_at must be timezone-aware: {self.starts_at!r}")
        if not self.agenda_url:
            raise ValueError("agenda_url is required by the API and cannot be empty")

    @property
    def date_time(self) -> str:
        """ISO 8601 with offset, e.g. 2026-07-15T19:30:00-05:00."""
        return self.starts_at.isoformat()

    def fingerprint(self, agency_id: str) -> str:
        """Reproduce the platform's importFingerprint exactly.

        Observed format (see docs/api-notes.md):
            agencyId::lowercased name::epoch milliseconds

        Computing it locally lets us skip meetings the platform already has
        without a POST, and it is what makes `name` a frozen value.
        """
        epoch_ms = int(self.starts_at.timestamp() * 1000)
        return f"{agency_id}::{self.name.lower()}::{epoch_ms}"

    def to_payload(self, agency_id: str) -> dict:
        """Build the POST /meetings body."""
        payload = {
            "name": self.name,
            "dateTime": self.date_time,
            "agencyId": agency_id,
            "location": self.location,
            "agendaUrl": self.agenda_url,
            "meetingType": self.meeting_type,
        }
        optional = {
            "details": self.details,
            "livestreamUrl": self.livestream_url,
            "contactPerson": self.contact_person,
        }
        payload.update({k: v for k, v in optional.items() if v})
        return payload

    def to_row(self) -> dict:
        """Flat dict for CSV/JSON output."""
        return {
            "name": self.name,
            "date": self.starts_at.strftime("%Y-%m-%d"),
            "time": self.starts_at.strftime("%-I:%M %p"),
            "date_time": self.date_time,
            "meeting_type": self.meeting_type,
            "location": self.location,
            "agenda_url": self.agenda_url,
            "source_id": self.source_id or "",
        }
