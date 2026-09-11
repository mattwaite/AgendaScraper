# Working in this repo

Scrapers that pull government meeting schedules into the NE Civic Newsroom API,
where Flatwater Free Press editors assign reporters to cover them. The four
Lincoln agencies are done, as are Omaha Public Schools, Sarpy County, the
Inland Port Authority and OPPD; five Omaha bodies remain, three of them behind
an Akamai block awaiting the client's escalation.

**Read `docs/api-notes.md` before touching the API, and README.md's "Adding a
scraper" before writing one.** Both record behavior that was established
empirically and is not guessable from the code. The rest of this file is the
things those two don't say.

## The pattern most of these agencies follow

The system holding the **agendas** and the system holding the **schedule** are
different systems. Granicus, CivicPlus Agenda Center and SPARQ each list a
meeting only once its agenda is posted — about a week out, sometimes not at
all. That is a publishing habit, not an API limitation, and it is the whole
reason every scraper here reads two sources.

So: an agency that appears to have no future meetings almost certainly has them
somewhere else. Go and find the second source before concluding otherwise. It
has been an OpenCities calendar page, a CivicPlus iCalendar feed, a Thrillshare
events API and a Finalsite calendar element so far.

Two agencies break the pattern: Sarpy's CivicWeb service and the Inland Port
Authority's meetings page each return the archive and the schedule together, so
they need a single source. Check for that before building a merge — it is
simpler, and it changes the right `external_id`.

OPPD breaks it the other way and needs three. The lesson worth carrying is that
**a body governed by bylaws usually has to adopt its own schedule, and the
document that does it is a source.** OPPD's page lists only the meetings left
in the current year, so in December it approaches empty — but Article IV makes
the board adopt next year's schedule every September, and that resolution PDF
carries a date, a time and a place for all twelve months. It was the only way
to see past the end of the year. Before settling for a thin schedule page, look
in the archive for the meeting where the schedule itself was approved.

Where a source gives a date but no time, prefer reading the real time from the
agenda and standing in the body's published hour until that exists, rather than
skipping the meeting — the default self-corrects on the next run, and lead time
is the point. Keep the defaulted field *out of the `external_id`.*

Check `scrapers/sources/` first — those readers are agency-agnostic and one of
them likely already covers a new site's platform. Omaha Public Schools needed
no new agenda parser at all: it runs the same SPARQ portal as Lincoln's, and
`sources/sparq.py` read all 462 of its rows unchanged.

## Only the apex board

Editors want the governing body itself and nothing below or beside it: the
school board, not its committees; the county board, not a commission that
meets in the same room. A scraper that publishes committee meetings is giving
editors work to filter out by hand.

This is not a filter you can write once and share, because each source marks
the difference differently and some do not mark it at all:

- OPS types a non-board meeting "Unit" -- but eight rows of one committee are
  filed as Hearing and Special instead, so the name is checked as well.
- LPS types its committees exactly like board meetings. Only the name
  separates them, and one committee's title omits the word "Committee".
- Lancaster works the other way round, from an allowlist of the two series it
  recognises, so anything new is excluded until someone adds it.
- Sarpy's portal types are close but not reliable: four equalization meetings
  are filed under the legacy board type, so its meeting *type* is read off the
  name even though inclusion is decided by type.

So: enumerate the distinct titles in the archive before writing the filter,
count what each rule drops, and put the counts in the module docstring. Both
times this was done the naive rule was wrong -- see `is_apex_board` in the OPS
scraper and `NOT_THE_BOARD` in the LPS one for what the data actually said.

Separate *bodies* count as non-apex too, not just committees: another board
that happens to meet under the same roof (a pension board, an interlocal
board) is not this agency.

## Verify a second source against the first before trusting it

Where the two overlap, confirm they agree, and say so in the module docstring
with the date and the numbers. The council's two sources shared 30 dates with
zero time disagreements; Lancaster's had zero overlap, a clean seam at today.
That check is what makes "this source is safe to merge" a fact rather than a
hope.

## Do not work around bot challenges

`app.lincoln.ne.gov` sits behind a Cloudflare "verifying you are not a bot"
interstitial, and `www.lps.org` behind an Akamai one. Both were left alone. A
public agency's meeting schedule is public, but defeating an access control is
not ours to do — find the underlying feed instead, which has worked every time
(the LPS calendar page is gated; the Thrillshare API behind it is not).

Playwright is used for pages that merely need JavaScript or reject plain HTTP
with a 403, which is a different thing.

## Testing

Tests run offline against committed fixtures — no network, no browser. Trim a
saved page down before committing it if it is large. Keep it that way: a suite
that needs the internet stops being run.

When a test fails, check whether the *test* is wrong before changing the code.
That has been the answer more than once here.

## Live runs

Sandbox first (`ZZ Test Agency`, id in `docs/api-notes.md`), then `--dry-run`,
then the real thing. Read the dry run's output rather than skimming it — a
default address appearing where a parsed one should be is how two regex bugs
were caught.

Deletes are real and there is no undo. Verify each one returns `True`.
