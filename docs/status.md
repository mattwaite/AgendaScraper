# Where things stand

Last updated **2026-09-11**, after OPPD (PR #12) merged.

`main` is clean, 330 tests pass offline, no open PRs and no stray branches.

## The eight that are live

| Agency | Slug | Meetings on the platform | Future |
|---|---|---|---|
| Lancaster County Board of Commissioners | `lancaster_county_commissioners` | 23 | 22 |
| Lincoln City Council | `lincoln_city_council` | 5 | 5 |
| Lincoln Public Schools Board of Education | `lps_board_of_education` | 17 | 16 |
| Lincoln-Lancaster County Planning Commission | `planning_commission` | 5 | 5 |
| Omaha Inland Port Authority | `omaha_port_authority` | 3 | 3 |
| Omaha Public Power District | `oppd_board_of_directors` | 4 | 4 |
| Omaha Public Schools Board of Education | `ops_board_of_education` | 25 | 23 |
| Sarpy County Board of Commissioners | `sarpy_county_commissioners` | 6 | 6 |

**91 meetings, 84 of them in the future.** Counted 2026-09-11.

Nothing here runs on a schedule. Every one of these is a manual
`python -m scrapers.run <slug>`, and the numbers above go stale on their own —
a meeting that has happened stops being a future meeting whether or not anyone
re-runs anything. Re-running is safe and idempotent; that is the whole design.

## The five that are not

### Blocked on access, not on code

Three sites sit behind an **Akamai edge block** — `server: AkamaiGHost`, a bare
"Access Denied" body. They refuse plain `curl`, real headless Chromium and
WebFetch alike, so this is not a user-agent problem and not something a
different HTTP client solves.

| Agency | Site |
|---|---|
| Omaha City Council | cityofomaha.org |
| Douglas County Board of Commissioners | douglascounty-ne.gov |
| Omaha Streetcar Authority | omahastreetcar.org |

**These were left alone deliberately.** A public agency's meeting schedule is
public, but defeating an access control is not ours to do — the standing rule
in CLAUDE.md. Matt has raised it with the client, who has more standing to ask
than we do, being a journalism organization with lawyers.

Worth knowing for when that lands: **both cityofomaha.org and
douglascounty-ne.gov are WordPress**, so `/wp-json/wp/v2/` would very likely be
a clean JSON feed the moment the block is lifted. That is the first thing to
try, not the last.

### Probably not worth building yet

| Agency | What is actually there |
|---|---|
| Downtown BID (ODIDA) | Site reachable, no sign it publishes agendas at all |
| Blackstone BID | Same |

Both are reachable — the block is not the issue. Neither appears to post
meeting agendas or a schedule anywhere public. **Confirm with the client that
these bodies publish anything before spending time on them.** A BID is not
subject to the same notice requirements as a county board, and it is possible
the answer is that there is nothing to scrape.

## Three records on the platform this repo did not create

| Agency | dateTime | name | externalId |
|---|---|---|---|
| Douglas County | 2026-08-25T14:00Z | `Douglas County Board of Commissioners Meeting` | `None` |
| Omaha City Council | 2026-08-26T00:00Z | `Omaha City Council Meeting` | `None` |
| Omaha City Council | 2026-09-01T19:00Z | `Regular City Council Meeting` | `None` |

All three were entered by hand — they carry **no `externalId`**, which no
scraper here would produce. Note the two council records do not even share a
name format, so they did not come from one consistent process.

**This is a trap for whoever builds those two scrapers.** Adoption matches on
*name + time* (see "Adoption: the first run with an externalId" in
`api-notes.md`), so a new scraper adopts these only if it emits exactly those
names at exactly those times. It will not. The likely outcome is two extra
records per agency rather than adoption.

So: **run the first pass for Douglas County and Omaha City Council with
`--stop-on-unexpected-create`**, look at what happens to these three, and
decide deliberately whether to delete them by hand or let them stand as
historical rows. Do not discover this mid-run.

## Standing open items

- **Questions for Ben** are collected at the bottom of `api-notes.md`. Most are
  answered; read that list before adding more.
- **The platform's agency name for the Inland Port Authority omits "Inland".**
  It is recorded as `Omaha Port Authority`; the body's own site and agendas use
  `Omaha Inland Port Authority`. Cosmetic, but worth telling Ben. The scraper
  hardcodes the platform's spelling in `agency_name` on purpose, because agency
  lookup is by cuid and the name is only checked for drift.
- **The `Omaha Streetcar Authoridy` typo is fixed** upstream — the API now
  returns `Omaha Streetcar Authority`. That open question can be closed.
- **The backfill decision is still Leah's and Ben's, not ours.** Every scraper
  takes `--since`, and several could load years of history. Nobody has said
  whether the platform wants it. Future meetings are the product; history is a
  nice-to-have that costs API records. Ask before backfilling anything.

## If you are picking this up cold

Read `CLAUDE.md` first — it is short and it is the accumulated "do not redo
this" list. Then `api-notes.md` before touching the API, and README.md's
"Adding a scraper" before writing one.

The one thing worth internalizing before anything else: **the schedule and the
agendas almost always live in different systems**, and an agency that looks
like it has no future meetings nearly always has them somewhere else. Going to
find that second source is most of the work in every scraper here. OPPD needed
a third, and the place it was hiding — a resolution the board is required by
its own bylaws to adopt every September — is the kind of thing worth checking
for elsewhere.
