# Platform API notes

Things learned by probing the live API that the [scraper API
guide](https://necivicnewsroom.up.railway.app/docs/scraper-api-guide) does not
say. Probed 2026-09-08 with `tools/probe_fingerprint.py`; raw log in
`probe-results.json`.

## Base URL

`https://necivicnewsroom.up.railway.app/api/v1` — the published guide shows a
`https://your-domain.com/api/v1` placeholder.

## Available methods

`OPTIONS /meetings` returns `allow: GET, HEAD, OPTIONS, POST`.

**There is no PATCH, PUT, or DELETE.** A meeting cannot be corrected or removed
through the API once submitted, which drives most of the caution below.

## How deduplication actually works

`GET /meetings` returns each record's `importFingerprint`, and its format is:

```
{agencyId}::{name, lowercased}::{dateTime as epoch milliseconds}
```

Observed value:

```
cmryg8k2p0001s91mclkzpdnq::lincoln city council regular meeting::1788215400000
```

Confirmed by experiment:

| Change from a stored meeting | Result |
|---|---|
| nothing — identical repost | `200 {created: false, reason: "duplicate"}` |
| different `dateTime`, same `name` | `201 created: true` |
| same `dateTime`, different `name` | `201 created: true` |

So `agencyId`, `name` and `dateTime` are all part of the fingerprint, and
`location`, `agendaUrl`, and `meetingType` are not.

### Consequence 1 — meeting names are frozen

Because `name` is in the fingerprint, **changing a scraper's name strings
re-creates every meeting it has ever submitted.** Names therefore live in a
`TYPE_NAMES` constant per agency (one string per meeting type), never vary per
meeting, and carry no date. Treat editing one as a data migration, not a tweak.

Case is safe to change — the fingerprint lowercases — but wording is not.

### Consequence 2 — a rescheduled meeting would double up

If Lincoln moves a meeting from 3:00 PM to 5:30 PM, the new time is a different
fingerprint, so the API creates a second record and there is no endpoint to
delete the stale one.

Guarding against that is harder than it looks, because **`GET /meetings` returns
only `id`, `name`, `dateTime`, `location` and `importFingerprint`** — no
`agendaUrl`, and nothing carrying our own `clip_id`. There is no reliable way to
recognize the same real-world meeting across a time change.

The runner therefore falls back to a heuristic: if a meeting with the same
`name` already exists on the same Central-time date at a different time, it logs
a warning, counts it as `time-changed`, and skips it. Skipping is the
conservative choice given nothing can be deleted; the corrected time has to be
entered by hand. `--allow-same-day` overrides it for a body that genuinely holds
two same-type meetings in one day. See `existing_keys` in `scrapers/run.py`.

Getting `agendaUrl` into the `GET /meetings` response would let us replace the
heuristic with an exact match — see the open questions.

### Consequence 3 — pad the date window

`GET /meetings?from=&to=` appears to bound on midnight, so a query ending on the
day of a meeting omits that meeting. The runner pads the range one day either
side before building its key set.

## Field notes

- `dateTime` accepts ISO 8601 with an offset and is stored as UTC — submitting
  `2026-08-31T17:30:00-05:00` reads back as `2026-08-31T22:30:00.000Z`.
- `agendaUrl` is **required**, so a scheduled meeting whose agenda has not been
  posted yet cannot be submitted at all. Those are counted as
  `skipped-no-agenda` and picked up by a later run.
- Valid `meetingType` values: `REGULAR`, `SPECIAL`, `EMERGENCY`, `WORKSHOP`,
  `HEARING`.
- `GET /meetings` requires `agencyId`; without it the API returns `400`.

## Agency ids

Resolve agencies by id, never by name — the platform currently lists
`"Omaha Streetcar Authoridy"`, and any name-matching code breaks the day that
typo is fixed. Ids as of 2026-09-08:

| Agency | id | Region |
|---|---|---|
| Lancaster County Board of Commissioners | `cmrygb9pe0003s91meavxc3s2` | Lincoln |
| Lincoln City Council | `cmryg8k2p0001s91mclkzpdnq` | Lincoln |
| Lincoln Public Schools Board of Education | `cmrygddn60005s91mb1posjj9` | Lincoln |
| Lincoln-Lancaster County Planning Commission | `cmrygfnxn0007s91mpsxoovti` | Lincoln |
| Blackstone Business Improvement District | `cmryg1xyx0005qf1mf2sg6qjy` | Omaha |
| Douglas County Board of Commissioners | `cmry22x9f0001pl1mh4733m2t` | Omaha |
| Downtown Business Improvement District | `cmryfsmz00003qf1mleq7pvp4` | Omaha |
| Omaha City Council | `cmrxzx7r60003mg0pvee2844t` | Omaha |
| Omaha Port Authority | `cmryfih4a0009pl1m9pf7zf1j` | Omaha |
| Omaha Public Power District | `cmryfo71d0001qf1m109oj1uz` | Omaha |
| Omaha Public Schools Board of Education | `cmryf502m0003pl1m3pqf52bt` | Omaha |
| Omaha Streetcar Authoridy *(sic)* | `cmryf87zn0005pl1mxukk5dsl` | Omaha |
| Sarpy County Board of Commissioners | `cmryfe0vv0007pl1ms6i9ijh0` | Omaha |

Regenerate with `python3 -c "from scrapers.client import PlatformClient;
print(PlatformClient().list_agencies())"`.

## Open questions for Ben

1. **Please delete the probe record** `cmttb83n20008qi1y51wyrj2b` — "Lincoln City
   Council Regular Meeting (fingerprint probe)" on 2026-08-31. It exists only to
   answer the `name`-in-fingerprint question and there is no delete endpoint.
2. **Can `agendaUrl` become optional?** As it stands, a meeting can't enter the
   system until its agenda is posted — often only days out, which is exactly the
   lead time an editor needs to assign a reporter. Lincoln's Granicus "Upcoming
   Events" table was empty on 2026-09-08, so the scraper had nothing to submit.
3. **Is a PATCH or DELETE endpoint possible?** Without one, a rescheduled meeting
   or a bad import can't be corrected by the scraper.
4. **Could `GET /meetings` include `agendaUrl`?** It's the one stable identifier
   we submit, and having it back would turn the rescheduled-meeting heuristic
   above into an exact match.
5. Is there a test agency we can probe against without putting junk in live data?
6. Agency name typo: `"Omaha Streetcar Authoridy"` → `"Omaha Streetcar Authority"`.
7. Any rate limits we should respect?
