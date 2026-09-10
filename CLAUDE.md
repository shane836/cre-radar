# CLAUDE.md — cre-radar

Finds Southern California commercial real estate events, scores them against
`scoring.toml`, and delivers a daily email + Obsidian note. Full design:
[`README.md`](README.md).

**`cre-radar` is the command; `SoCal CRE Events` is the product.** The public
name lives in `APP_NAME` (`src/cre_radar/__init__.py`), imported by `site.py`
and `digest/render.py`, and repeated once in `api/subscribe.js` because that
file is not Python. Do not hardcode it anywhere else, and do not rename the
CLI, the repo or the cron entry to match it.

**This app uses no model and no API key at runtime, by design.** Do not
reintroduce one. Extraction is `heuristic.py`; scoring is `scoring.toml`.

Stack: Python 3.11+ via `uv`, SQLite, Playwright, Resend.

## Commands

```bash
uv sync && uv run playwright install chromium
uv run pytest -q                    # tests
uv run ruff check .                 # lint
uv run cre-radar doctor             # config pre-flight
uv run cre-radar run                # the whole pipeline
uv run cre-radar digest --dry-run   # safe: renders without delivering
uv run cre-radar newsletter --dry-run --force   # the public weekly issue
./scripts/rebuild.sh                # after changing EXTRACTION rules
./scripts/install-launchd.sh        # install the daily 07:00 agent
```

## Architecture

Full map — directories, modules, schema, invariants: [`ARCHITECTURE.md`](ARCHITECTURE.md).

Every stream enters through the adapter contract in `contracts.py`, ported from
`sf-events-aggregator`. Read that and `identity.py` before touching ingestion.
`rubrics/milestone-contract.md` is the PGE rubric the tests implement — add a
dimension there before adding a test.

## Things to get right

- **`sources.toml` and `scoring.toml` are the control surface.** Adding a source
  or retuning relevance must never require a code change.
- **`persist.py` is the single writer.** Adapters never touch the DB. A write
  that bypasses it bypasses cross-source dedupe and the winner rule.
- **`normalize()` must stay pure.** No clock, no network, no DB — time arrives via
  `Provenance`. `tests/test_contract.py` greps for `datetime.now` inside it.
- **`fetch()` must not raise.** Failures are `SourceError` values. A source that
  returns zero events *and* an error is a failed run, not a quiet one.
- **Never slice a UTC ISO string for a local date.** `starts_at` is UTC; every
  display path goes through `identity.format_local_minute`.
- **Don't strip punctuation in `normalize_title`.** False splits are recoverable;
  false merges silently delete an event.
- **Don't add tags to `fetch._STRIP_TAGS` casually.** `form` used to be there and
  silently reduced selfstorage.org to its `<title>` — ASP.NET wraps the whole
  body in one `<form>`. Any addition needs a `sources check` run.
- **`sources check` measures condensed characters, not HTTP status.** A bot
  interstitial returns 200. Keep it that way.
- **Email HTML must be ASCII.** An email body has no `<head>` for a charset, so
  `render._esc` converts non-ASCII to numeric references. A literal `·` or `—`
  renders as `Â·` / `â€"` in real clients. Don't "simplify" that away.
- **`digest` marks rows surfaced.** Use `--dry-run` while iterating.

## The public page's nav and drawers

About and Subscribe live in a **sticky navbar**, each opening a drawer beneath
it. The listing is the page; everything else is one click away.

- **Drawers ship open and the script collapses them.** An inline script in
  `<head>` stamps `js` on the root element and the `.js` rules do the
  collapsing. Render them collapsed instead and a reader without JS gets two
  dead buttons and no route to the contact details at all. Stamping before
  first paint is what stops them flashing open on load.
- **`visibility:hidden` on a closed drawer is load-bearing.** `max-height:0`
  alone hides it visually while leaving its links in the tab order, stranding a
  keyboard user on an invisible LinkedIn link. `tests/test_site.py` asserts it.
- **The drawers sit outside the sticky element.** Inside it, an open drawer
  pins itself over the listing it is meant to sit above. Opening one scrolls
  the page to the top instead.
- `aria-expanded` and `aria-controls` are not decoration — they are the only
  thing telling a screen reader the button reveals a panel.

`site.py` carries Shane's name and contact details. Three rules hold it together:

- **The address is never in the page source.** It ships reversed-then-base64'd
  in `data-a` and the script rebuilds it; `tests/test_site.py` asserts no
  `user@host.tld` matches anywhere in the output. Do not "simplify" that into a
  plain `mailto:` — the page is indexed and harvested.
- **Publish a forwarding alias, never the primary work address.** An alias can
  be switched off the day it starts pulling spam; a primary address cannot.
  (This file is public — that is why it names neither.)
- **Empty link constants omit the link.** `LINKEDIN_URL`, `CONTACT_EMAIL` and
  `BEEHIIV_EMBED_URL` all default to `""` and the renderer skips them rather
  than emitting `href=""`. Do not add a placeholder value to "fix" a blank spot.

`--on-accent` exists because `--accent` inverts in dark mode: white on the dark
blue is 6.9:1, but white on the light blue is **2.5:1**, a WCAG failure. Any new
accent-filled control uses `var(--on-accent)` for its text, never `#fff`.

## The mailing list

**MailerLite is the list, and it is what sends.** That reverses the August 2026
decision to run cre-radar on its own beehiiv publication, for one reason:
beehiiv on the free plan cannot send programmatically at all — its Create Post
endpoint is Max/Enterprise — so the signup box promised a weekly digest for
weeks while nothing on earth was going to deliver one.

Shane's call, Sept 2026: run it on the same MailerLite account and the same
authenticated sending domain (`news.masonequitypartners.com`) the firm is
already warming for the associates blast. One domain, one reputation, one
warm-up to manage rather than two.

**The two audiences are two groups, never one.** `MAILERLITE_GROUP_ID` is not
optional anywhere it appears. A campaign scoped to the account rather than to
the group would mail the events newsletter to the firm's associates list.

**Still no subscribers table here.** MailerLite is the list; this repo stores
nothing. Do not add one.

**beehiiv is a mirror, and only until the migration finishes.** `api/subscribe.js`
still writes to it when `BEEHIIV_API_KEY` and `BEEHIIV_PUBLICATION_ID` are set,
best-effort and logged — MailerLite already has the subscriber by then, so a
beehiiv failure is a stale mirror, not a lost signup. Deleting those two
variables from the Vercel project retires the leg with no code change. That is
the intended end state, not a misconfiguration.

The block is a field and a Subscribe button posting to `api/subscribe.js` on
this same origin, which is why the page still makes **zero external requests**.
Setting `BEEHIIV_EMBED_URL` trades that for beehiiv's iframe — a deliberate
escape hatch, not an oversight, and the only one.

Four things in that function are load-bearing:

- **A missing env var is a 500, never a thank-you.** `MAILERLITE_API_KEY` and
  `MAILERLITE_GROUP_ID` live in the **Vercel** project env, not the local
  `.env`, which is not deployed. Swallowing the miss would drop real signups
  behind a message the visitor believes.
- **The Resend receipt is best-effort and must stay that way.** The list already
  has the subscriber by the time it runs; a mail failure is logged, never
  surfaced. Turning it into an error would report a successful signup as a
  failure.
- **The plain form POST has to keep working.** No JavaScript means a real
  navigation to `/api/subscribe`, so the function answers `text/html` unless
  the caller sent `X-Requested-With: fetch`.
- **The `company` field is a honeypot**, positioned off-screen rather than
  `display:none` — a bot that skips hidden inputs walks past the trap. A filled
  one gets a cheerful 200 and no subscription.

**Never let the fallback point at the firm's publication.** `SUBSCRIBE_URL` in
`site.py` and `BEEHIIV_SUBSCRIBE_URL` in the function both default to empty and
omit the link, because a wrong link here puts an events reader on the firm's
investor list. Fill them with cre-radar's own hosted page or leave them blank.

## The weekly issue

`cre-radar newsletter` sends it; `digest/newsletter.py` is the transport and
`render.html_newsletter` the body. **Thursdays**, a 14-day window, both settable
via `NEWSLETTER_DAY` and `NEWSLETTER_HORIZON_DAYS` and both pinned by tests —
the box promises weekly, so the cadence is the promise.

**None of the MailerLite calls have ever run.** The account does not exist yet;
the requests are written from the v2 docs. Standing up the account is tracked
under *Future scope* in the README, and until it is done `doctor` warns and the
newsletter step no-ops. Treat the request shapes here as unverified: the first
live send should go to a group containing only Shane's address.

- **It runs inside the daily job, not on a scheduler of its own.** There is
  exactly one scheduler by design (see *The daily run*), and a second launchd
  agent firing at 07:00 would race the daily one for the lock on a single
  SQLite file. `run` calls `newsletter` every day and the weekday gate inside
  is what makes it weekly. Do not "fix" this by adding a second plist.
- **It runs last, after the digest.** A MailerLite outage must not cost Shane
  the vault note and the daily email.
- **An empty issue is never sent.** A fortnight with no events would train
  subscribers to ignore the sender and spend reputation on a domain that is
  still warming. Shane's copy still goes out saying so, because a quiet week
  must not look like a broken pipeline.
- **`render.UNSUBSCRIBE_TOKEN` must reach MailerLite verbatim.** `{$unsubscribe}`
  is ASCII with no HTML-special characters, so `_esc` passes it through — but
  that is a property, not an accident, and `tests/test_newsletter.py` pins it.
  `send_campaign` refuses a body without it rather than letting MailerLite
  reject the campaign after creating it and leaving a draft behind.
- **`newsletter_events` ignores `surfaced_at`.** That column records what went
  to *Shane's* digest; a subscriber has seen none of it. Using `pending_events`
  here would silently send subscribers only the events Shane had not yet read.
- **Shane's daily digest carries no opt-out and must not.** It goes to one known
  address through Resend. A merge token in it renders as literal braces.
- **The postal address is empty by default and omits the line.** Bulk mail is
  required to carry one; a placeholder in a compliance footer is worse than no
  line, because it looks filled in. `doctor` warns while it is unset.

## Publishing

**Never a bare `vercel deploy`.** It uploads the sources and builds them
remotely, and the upload skips gitignored paths — `public/` is generated and
therefore gitignored, so the page never reaches the builder. The deployment
comes out with `api/subscribe` and no static files, Vercel marks it Ready and
aliases it, and the site 404s. That happened on 2026-08-30 and the page was
down for eleven hours. `publish --deploy` runs `vercel build` locally and ships
it with `--prebuilt`.

**A green exit code is not evidence the site works.** `publish --deploy` fetches
`SITE_URL` afterwards and checks for the page's own furniture — the title plus
either a week block or the empty-state note. Vercel's 404 is an HTML document
served from the same host, so a status code alone proves nothing.

**The deploy is retried once.** The 07:00 run on 2026-08-31 failed with a bare
"Error: Not authorized" that has never reproduced — not under the same stripped
environment launchd uses, not since. One transient refusal should not cost a
day's publish.

## The daily run

**launchd, not cron.** `scripts/install-launchd.sh` writes
`~/Library/LaunchAgents/com.masonequity.cre-radar.plist`. cron skips a run
outright when the Mac is asleep at the appointed minute; launchd runs it on the
next wake. There is exactly one scheduler — the installer strips any leftover
`cre-radar` crontab line, because two schedulers means two processes writing
one SQLite file.

`scripts/daily.sh` is the wrapper the agent invokes, and each part of it is
load-bearing:

- **The `mkdir` lock.** macOS ships no `flock(1)`, and `mkdir` is atomic
  everywhere. A slow run must never have the next morning's run start on top of
  it. A lock whose pid is dead is cleared, not honoured, or one power cut would
  wedge the schedule permanently.
- **No `set -e`.** A non-zero exit has to be logged and returned, not lost to
  the shell exiting first.
- **`uv run --directory`, not `cd`.** It sets uv's project root *and* the
  working directory, so `.env` and the relative `CRE_DB` both resolve. The
  plist's `WorkingDirectory` is `$HOME` deliberately: the repo is under
  Dropbox, whose directory handle can be transiently unavailable, and launchd
  fails the whole job if its chdir fails.
- **`PATH` in both the plist and the script.** launchd reads no shell profile.
  `publish --deploy` shells out to `vercel`, which lives in
  `/opt/homebrew/bin` — omit it and the deploy fails nightly and silently.
- **`RunAtLoad` is false.** True would fetch every source on install and again
  at every login.

Dropbox is also the risk: it can copy `cre_radar.db` mid-write. If the database
ever comes back corrupt, that is the first thing to suspect.

## Vercel

`vercel.json` sets `framework: null` and `buildCommand: ""` deliberately — with
detection on, Vercel finds `pyproject.toml` and fails with "No python entrypoint
found". `.vercelignore` keeps everything but `public/` and `api/` off the
deployment. Deploy from the repo root, never from `public/` (that creates a
second project named "public").

## Who this is for

A principal and capital allocator — he raises capital, buys assets, and operates
self-storage in LA. He is **not** a property manager, small landlord, or building
engineer. Most of the LA event calendar is operator education aimed at exactly
those people, so `[org]` weights in `scoring.toml` push AAGLA / IREM / BOMA down
hard and NAIOP / CSSA / ULI / Lusk / AIR CRE up. Don't "fix" that as a bug.

The calibration tests at the bottom of `tests/test_scoring.py` encode his actual
verdict on a real digest. If a weight change breaks them, the change is wrong.

## Two kinds of tuning, two different fixes

- **Scoring** (`scoring.toml`) → `cre-radar rescore`. No re-fetching.
- **Extraction** (`heuristic.py`, `sources.toml`) → `./scripts/rebuild.sh`.
  Extraction rules only affect new extractions; junk already stored stays until
  rebuilt.
