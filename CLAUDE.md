# Working in this repo

Scrapers that pull government meeting schedules into the NE Civic Newsroom API,
where Flatwater Free Press editors assign reporters to cover them. The four
Lincoln agencies are done, as is Omaha Public Schools; eight Omaha bodies
remain.

**Read `docs/api-notes.md` before touching the API, and README.md's "Adding a
scraper" before writing one.** Both record behavior that was established
empirically and is not guessable from the code. The rest of this file is the
things those two don't say.

## The pattern that has held for all four agencies

The system holding the **agendas** and the system holding the **schedule** are
different systems. Granicus, CivicPlus Agenda Center and SPARQ each list a
meeting only once its agenda is posted — about a week out, sometimes not at
all. That is a publishing habit, not an API limitation, and it is the whole
reason every scraper here reads two sources.

So: an agency that appears to have no future meetings almost certainly has them
somewhere else. Go and find the second source before concluding otherwise. It
has been an OpenCities calendar page, a CivicPlus iCalendar feed, a Thrillshare
events API and a Finalsite calendar element so far.

Check `scrapers/sources/` first — those readers are agency-agnostic and one of
them likely already covers a new site's platform. Omaha Public Schools needed
no new agenda parser at all: it runs the same SPARQ portal as Lincoln's, and
`sources/sparq.py` read all 462 of its rows unchanged.

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
