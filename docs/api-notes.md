# Platform API notes

How this project uses the NE Civic Newsroom API, and what has been verified
against production rather than assumed. The [scraper API
guide](https://civicnewsroom.org/docs/scraper-api-guide) is the source of truth;
this file records the parts that affect scraper design.

Last verified 2026-09-10 with `tools/verify_upsert.py`.

> **Historical note.** Before 2026-09-10 this file documented a very different
> API: no `externalId`, no `PATCH`/`DELETE`, and deduplication on an
> `importFingerprint` that covered the meeting's `name` — which meant meeting
> names had to be frozen forever and a rescheduled meeting could not be fixed.
> All of that is gone. `docs/probe-results.json` is the record of how those
> rules were worked out, kept because it explains why earlier commits look the
> way they do. Do not design against it.

## Base URL

`https://civicnewsroom.org/api/v1`

Use the apex domain. The `necivicnewsroom.up.railway.app` host works but is
Railway's hostname for the service and isn't guaranteed to survive a platform
change. `/api` is deliberately exempt from the `www` → apex redirect, because
both curl and `requests` drop the `Authorization` header when a redirect changes
host — an API call on the wrong hostname is served rather than turned into a
confusing 401.

## externalId is what makes this safe

Every meeting we submit carries an `externalId`: our own stable id for that
meeting, unique per agency.

With it, `POST /meetings` is an upsert keyed on that id:

| Resubmitted with | Response |
|---|---|
| nothing changed | `200 {created: false, reason: "duplicate"}` |
| a new time, name, location or agenda URL | `200 {created: false, updated: true, changed: [...]}` — same record |
| a first submission | `201 {created: true}` |

Verified end to end against ZZ Test Agency: create → repost → reschedule →
rename leaves exactly one row holding the latest values.

**So the runner does no local deduplication at all.** It submits every scraped
meeting and reports what the API says it did. The platform is the authority on
what it already has, and its answer is more trustworthy than anything we could
reconstruct locally.

Two consequences worth knowing:

- **Meeting names are free to change.** `TYPE_NAMES` in a scraper can be edited
  without duplicating anything, because matching is on `externalId`, not name.
- **`external_id` must stay stable for the life of a meeting.** It is the one
  value a scraper must never let drift.

### Choosing an externalId

Most agencies here read two sources — one with the agendas, one with the
forward schedule — so the id has to be something *both* sources can produce. A
source's own meeting id is therefore usually the wrong choice, however stable
it looks: the record would change identity the moment the meeting moved from
the calendar to the agenda system, orphaning the first copy. Each two-source
scheme below is the narrowest key those sources agree on.

Sarpy County is the exception that shows what the rule is for. Its CivicWeb
service returns the archive *and* the schedule in one call, so nothing can move
between systems, and the portal's own id is the better key: it survives a
reschedule and updates the record in place, where every date-derived key files
a new record and strands the old one.

| Agency | Scheme | Why not narrower |
|---|---|---|
| Lincoln City Council | `lnk-{date}` | No two of 45 archived meetings shared a date |
| Lancaster County | `lnc-{series}-{date}` | Two series meet the same morning |
| LPS Board of Education | `lps-{date}-{HHMM}` | 170 of 489 dates carry more than one meeting |
| Planning Commission | `llcpc-{date}` | One meeting per date |
| OPS Board of Education | `ops-{date}-{HHMM}` | 51 of 409 dates carry more than one meeting |
| Sarpy County | `sarpy-{portal id}` | One source covers past and future, so the id is safe — and beats a date key |
| Omaha Inland Port Authority | `oipa-{date}` | One meeting per date; the time is *excluded* on purpose — see below |
| OPPD Board of Directors | `oppd-{date}` | At most one meeting a month; the time is *excluded* for the same reason |

Where a scheme cannot represent two meetings that collide, the scraper skips
the second with a warning rather than silently overwriting the first.

**Never key on a field the scraper itself supplies a default for.** The Inland
Port Authority's listing gives a date and no time, so a meeting with no agenda
yet is filed at the board's standing 9:00 AM. Putting that time in the key
would strand the record every time an agenda posted a non-standard hour — the
August 2026 meeting was at 4:30 — turning a routine correction into a
duplicate. The date alone is the stable part.

OPPD is the same rule from the other direction. Its page says "Meetings start
at 5 p.m. unless otherwise noted," and the exception is real — the board met at
6:00 on January 18, 2024. There the time is not defaulted so much as *supplied
by a different document than the date*, and a later-arriving agenda can revise
it. Either way it is a field this scraper decides rather than reads off the
schedule, so it stays out of the key.

### The failure the upsert cannot see: stranded records

Any id that encodes something about the meeting drifts when that thing changes
upstream. If LPS moves a meeting from 6:00 to 5:30, the run files
`lps-2026-10-13-1730` and `lps-2026-10-13-1800` stays behind — an editor now
sees the meeting twice, once at an hour nobody is meeting at. The platform
cannot help: from its side those are simply two meetings.

So after submitting, the runner compares the ids it produced against the
records the platform holds over the same dates, and warns about any it did not
account for:

```
WARNING lps-2026-10-13-1800 (2026-10-13T23:00:00.000Z) is on the platform but
this run did not produce it ... should be deleted by hand (id cmtw1pjb...)
```

It reports rather than deletes, because a source that drops a meeting for one
run would otherwise take a real record with it. Verified against ZZ Test
Agency: file at 18:00, re-file at 17:30, the 18:00 record is reported.

Records with no `externalId` are never reported — those are an editor's own
work, or predate the import.

Two limits worth knowing: only the scraped date range is checked, so a meeting
moved to a *different day* is not caught, and `--dry-run` reports nothing
because it never reads the platform.

### Adoption: the first run with an externalId

Meetings submitted before `externalId` existed are matched on name + time and
have the id attached, rather than being duplicated. The guide calls this
`reason: "adopted"`; in practice the API returned `updated: true` with an empty
`changed` array for our two records, and both kept their original record ids.
Either way `created` stays 0, which is the signal that matters.

Adoption depends on the scraper's names still producing exactly what was
submitted before. Run the first pass against an agency with
`--stop-on-unexpected-create`: a `created` where an adoption was expected means
the names drifted and the run is about to duplicate everything.

## A POST replaces the record; it does not merge into it

**Leaving an optional field out of a submission clears whatever the platform had
in it.** Verified 2026-09-10: submitting a meeting with an `agendaUrl`, then
resubmitting the same `externalId` without one, answered
`updated, changed: ["agendaUrl"]` and left the stored value `null`.

The guide says `PATCH` is partial. It does not say the opposite about `POST`,
and the upsert language ("resubmission with a changed time, name, location or
agenda URL → updated") reads as though only what you send is considered.

This bites any source that stops exposing a value it used to. The planning
commission links an agenda for the *next* meeting only, so without care every
later run would erase the agenda from the meeting before it.

`carry_forward` in `scrapers/run.py` handles it: the runner reads the agency's
existing meetings once per run and, for any optional field the scrape didn't
find but the platform already holds, puts the stored value back. Required
fields are never carried forward — a stale name or time must not leak back in.

Erasing something an editor can see is worse than keeping a value that has gone
slightly stale, and a wrong one can still be cleared by hand.

## Required and optional fields

Required: `name`, `dateTime`, `agencyId`.

Everything else is optional, including `location` and `agendaUrl`. **A meeting
can be submitted as soon as it is scheduled**, and a later run fills the agenda
URL in when it is posted.

For Lincoln City Council this changes nothing observable: Granicus does not list
a meeting in "Upcoming Events" before its agenda exists, so there is nothing to
submit early. The lead time this buys is real for portals that publish a
calendar ahead of agendas.

- `dateTime` takes ISO 8601 with an offset and is stored as UTC —
  `2026-08-31T17:30:00-05:00` reads back as `2026-08-31T22:30:00.000Z`.
- `meetingType` must be one of `REGULAR`, `SPECIAL`, `EMERGENCY`, `WORKSHOP`,
  `HEARING`. An invalid value returns `400` (it used to fall back to `REGULAR`
  silently). `Meeting` validates this before anything is sent.
- `from` / `to` on `GET /meetings` accept bare `YYYY-MM-DD` and cover that whole
  calendar day in America/Chicago, so no padding is needed.
- `GET /meetings` requires `agencyId`, defaults to `limit=50`, and caps at 200.

## Recovery

`PATCH` and `DELETE` both exist on `/api/v1/meetings/{id}`:

```bash
# fix one field
curl -X PATCH "https://civicnewsroom.org/api/v1/meetings/$ID" \
  -H "Authorization: Bearer $PLATFORM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agendaUrl": "https://..."}'

# remove a bad record; frees its fingerprint for re-import
curl -X DELETE "https://civicnewsroom.org/api/v1/meetings/$ID" \
  -H "Authorization: Bearer $PLATFORM_API_KEY"
```

`PATCH` is partial — send only what changed. `agencyId` is immutable. `DELETE`
refuses with `409 {assignmentCount}` when an editor has already attached an
assignment, since those carry freelancer applications and payment records; a
human has to clear them first.

The runner never calls either. Both are for fixing things by hand.

### The one case that needs a human: `conflict`

A run reports `conflict` on a `409`, which means the payload's fingerprint
belongs to a record filed under a *different* `externalId`. In practice that
means a meeting's external id changed between runs — for this scraper, a row
that had no `clip_id` to borrow on one run (so it got the `lnk-date-...`
fallback) and gained one later. That has never been observed on Lincoln's
portal, but if it happens: find the existing record, `PATCH` it with the correct
values, and `DELETE` the stale one.

## Rate limits

None enforced; the platform asks for under ~60 requests a minute. The client
throttles writes to one per second and leaves reads unthrottled. With no local
pre-check a run POSTs every meeting in its window, so a full year for one agency
takes about 30 seconds, and roughly 6–7 minutes once all 13 agencies exist.

## Agency ids

Resolve agencies by id, never by name. The `"Omaha Streetcar Authoridy"` typo
was fixed on 2026-09-10 and the id did not change — which is exactly why
id-matching was the right call. (Its public URL slug is still
`omaha-streetcar-authoridy`, left alone so the existing link keeps working.)

| Agency | id | Region |
|---|---|---|
| Lancaster County Board of Commissioners | `cmrygb9pe0003s91meavxc3s2` | Lincoln |
| Lincoln City Council | `cmryg8k2p0001s91mclkzpdnq` | Lincoln |
| Lincoln Public Schools Board of Education | `cmrygddn60005s91mb1posjj9` | Lincoln |
| Lincoln-Lancaster County Planning Commission | `cmrygfnxn0007s91mpsxoovti` | Lincoln |
| ZZ Test Agency | `cmtvrv1xn0001lu1y1itrgs8y` | Lincoln |
| Blackstone Business Improvement District | `cmryg1xyx0005qf1mf2sg6qjy` | Omaha |
| Douglas County Board of Commissioners | `cmry22x9f0001pl1mh4733m2t` | Omaha |
| Downtown Business Improvement District | `cmryfsmz00003qf1mleq7pvp4` | Omaha |
| Omaha City Council | `cmrxzx7r60003mg0pvee2844t` | Omaha |
| Omaha Port Authority | `cmryfih4a0009pl1m9pf7zf1j` | Omaha |
| Omaha Public Power District | `cmryfo71d0001qf1m109oj1uz` | Omaha |
| Omaha Public Schools Board of Education | `cmryf502m0003pl1m3pqf52bt` | Omaha |
| Omaha Streetcar Authority | `cmryf87zn0005pl1mxukk5dsl` | Omaha |
| Sarpy County Board of Commissioners | `cmryfe0vv0007pl1ms6i9ijh0` | Omaha |

Regenerate with `python3 -c "from scrapers.client import PlatformClient;
print(PlatformClient().list_agencies())"`.

**ZZ Test Agency** is a live, normal agency used as a sandbox. Point
`tools/verify_upsert.py` at it after any API change, and before the first real
run of a new scraper.

## Open with Ben

1. The guide documents adoption as `200 reason: "adopted"`, but our two
   pre-`externalId` records came back as `updated: true` with an empty `changed`
   array. Same outcome, and nothing duplicated — worth knowing which is
   intended, since it's the signal a first run is checked against.
2. **Is POST-as-full-replace intended?** Omitting an optional field clears it
   (see above). It's a quiet way to lose data: a scraper that simply doesn't
   find an agenda URL this week erases the one an editor was relying on. If
   omitted-means-unchanged were the rule, the `carry_forward` workaround could
   go. Either way it's worth a line in the guide.
