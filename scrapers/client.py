"""The only place in this project that talks HTTP to the platform API."""

import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://necivicnewsroom.up.railway.app/api/v1"
TIMEOUT = 30


class PlatformError(RuntimeError):
    """Any non-recoverable problem talking to the platform API."""


class AuthError(PlatformError):
    """401 -- key is missing, invalid, or revoked."""


class AgencyNotFoundError(PlatformError):
    """404 -- the agencyId does not exist."""


@dataclass(frozen=True)
class SubmitResult:
    id: str | None
    created: bool
    reason: str | None = None


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader -- avoids a dependency for four lines of parsing.

    Existing environment variables always win, so `PLATFORM_API_KEY=... python -m
    scrapers.run` overrides the file.
    """
    path = path or Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class PlatformClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        load_dotenv()
        self.api_key = api_key or os.environ.get("PLATFORM_API_KEY", "")
        if not self.api_key:
            raise PlatformError(
                "PLATFORM_API_KEY is not set. Copy .env.example to .env and add "
                "the key from the NE Civic Newsroom admin dashboard."
            )
        self.base_url = (
            base_url or os.environ.get("PLATFORM_API_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "flatwater-agenda-scraper/1.0",
            }
        )
        # Retry transient failures only. A 4xx is our bug and should surface at once.
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise PlatformError(f"{method} {url} failed: {exc}") from exc

        if response.status_code == 401:
            raise AuthError(
                f"401 from {url}. The API key is invalid or has been revoked."
            )
        if response.status_code == 404:
            raise AgencyNotFoundError(f"404 from {url}: {response.text[:200]}")
        return response

    def list_agencies(self, search: str | None = None) -> list[dict]:
        params = {"search": search} if search else None
        response = self._request("GET", "/agencies", params=params)
        if not response.ok:
            raise PlatformError(
                f"GET /agencies returned {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    def get_agency(self, agency_id: str) -> dict | None:
        """Look up an agency by cuid. Never match on name -- names get edited."""
        for agency in self.list_agencies():
            if agency.get("id") == agency_id:
                return agency
        return None

    def list_meetings(
        self,
        agency_id: str,
        from_: date | None = None,
        to: date | None = None,
        limit: int = 200,
    ) -> list[dict]:
        params: dict[str, str | int] = {"agencyId": agency_id, "limit": limit}
        if from_:
            params["from"] = from_.isoformat()
        if to:
            params["to"] = to.isoformat()
        response = self._request("GET", "/meetings", params=params)
        if not response.ok:
            raise PlatformError(
                f"GET /meetings returned {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    def create_meeting(self, payload: dict) -> SubmitResult:
        """POST one meeting.

        A duplicate is a normal outcome, not an error: the API answers 201 with
        created:true for a new meeting and 200 with created:false when its
        importFingerprint already exists.
        """
        response = self._request("POST", "/meetings", json=payload)
        if response.status_code in (200, 201):
            body = response.json()
            return SubmitResult(
                id=body.get("id"),
                created=bool(body.get("created")),
                reason=body.get("reason"),
            )
        raise PlatformError(
            f"POST /meetings returned {response.status_code}: {response.text[:300]}\n"
            f"payload: {payload}"
        )
