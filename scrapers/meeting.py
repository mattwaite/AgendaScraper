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

    `external_id` is our own stable id for the meeting -- the source system's
    own id, such as a Granicus clip_id. It is what makes submission an upsert:
    a meeting that moves or gets renamed updates its existing record instead of
    creating a second one. It must stay stable for the life of the meeting.

    Only name, starts_at and the agency are required by the API; a meeting whose
    agenda has not been posted yet can still be submitted, and a later run fills
    the agenda URL in.
    """

    name: str
    starts_at: datetime  # timezone-aware
    external_id: str
    location: str | None = None
    agenda_url: str | None = None
    meeting_type: str = "REGULAR"
    details: str | None = None
    livestream_url: str | None = None
    contact_person: str | None = None

    def __post_init__(self) -> None:
        if self.meeting_type not in MEETING_TYPES:
            raise ValueError(
                f"meeting_type {self.meeting_type!r} is not one of {MEETING_TYPES}"
            )
        if self.starts_at.tzinfo is None:
            raise ValueError(f"starts_at must be timezone-aware: {self.starts_at!r}")
        if not self.external_id:
            raise ValueError("external_id is required and cannot be empty")
        if not self.name:
            raise ValueError("name is required and cannot be empty")

    @property
    def date_time(self) -> str:
        """ISO 8601 with offset, e.g. 2026-07-15T19:30:00-05:00."""
        return self.starts_at.isoformat()

    def to_payload(self, agency_id: str) -> dict:
        """Build the POST /meetings body."""
        payload = {
            "name": self.name,
            "dateTime": self.date_time,
            "agencyId": agency_id,
            "externalId": self.external_id,
            "meetingType": self.meeting_type,
        }
        optional = {
            "location": self.location,
            "agendaUrl": self.agenda_url,
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
            "location": self.location or "",
            "agenda_url": self.agenda_url or "",
            "external_id": self.external_id,
        }
