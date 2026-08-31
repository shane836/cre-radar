# cre-radar

Sweeps 20 Los Angeles commercial real estate event sources every morning, scores
what it finds against your rules, and publishes what's worth your calendar three
ways: a **live site**, an email, and a dated Obsidian note.

**Live:** https://cre-radar.vercel.app

**No API key. No model. No per-run cost.** Everything is deterministic scripts —
the same inputs always produce the same digest.

## How this was built

Written with Claude Code, reviewed and directed by me. The design decisions are
mine — the rules-file scorer, the principal-not-operator filter that does most
of the work, the adapter contract, and the choice to keep a model out of the
runtime entirely. Most of the code implementing them is AI-generated.

Worth stating plainly given the line above: *no model runs when this runs.* One
was used to write it; none is used to operate it.

## How it works

```
sources.toml ─► fetch ─► condense ─► heuristic extract ─► RawEvent
                                                             │
                                              normalize()  (pure)
                                                             │
                             canonical fingerprint ──────────┤
                                                             ▼
             score (scoring.toml) ◄── SQLite ◄── persist (single writer,
                    │                              verification-level winner rule)
      ┌─────────────┼─────────────┐
  Vercel site     email      Obsidian note
```

Directory-by-directory map, module responsibilities, the storage schema and the
invariants: [`ARCHITECTURE.md`](ARCHITECTURE.md).

Four choices carry most of the weight:

**Extraction is generic, not per-site.** Pages are reduced to link-annotated text,
then a pattern parser reads the shape every event listing shares — a date, a
linked title, a time. Adding a source is a four-line block in `sources.toml`, not
a new file of CSS selectors. There is not one site-specific parser in the repo.

**Scoring is a rules file.** `scoring.toml` *is* the scorer. An event starts at
`base` and accumulates weights for its host, subject, geography and kind. Every
rule that fires is recorded, so the digest can tell you why something scored what
it did — and when something wrong gets through, you edit one line.

**The reader is a principal, not an operator.** This is the assumption doing most
of the filtering. The LA calendar is dominated by operator education — how to
evict a tenant, how to pass a seismic inspection, how to earn an IREM
certification — and none of it is for someone who allocates capital and hires
people for all of that. Getting this wrong put 23 property-management sessions in
a 40-event digest. `[org]` weights encode it: principal-and-capital bodies
(NAIOP, CSSA, ULI, Lusk, AIR CRE) score up; operator-and-vendor bodies (AAGLA,
IREM, BOMA) score down hard, whatever the session is about.

**Identity is content-derived, not URL-derived.** Bisnow, Connect CRE and NAIOP
writing up the same panel collapse to one row, because the fingerprint comes from
the event's own title, venue and local minute.

**Unchanged calendars are skipped.** Each page's link set is fingerprinted; if it
hasn't changed, nothing is re-extracted.

## The public page

A sticky navbar carries **About** and **Subscribe**, each opening a drawer
beneath it — what the tool does, who it filters for and who built it, plus a
mailing-list block using the copy from masonequitypartners.com. The listing
stays the page; the rest is one click away.

Both drawers render open and an inline `<head>` script collapses them, so the
content is still reachable with JS disabled. A closed drawer is
`visibility:hidden`, which is what keeps its links out of the tab order.

The contact address never appears in the page source. It ships reversed and
base64'd and the script rebuilds it on load, so a harvester scanning for
`user@host.tld` finds nothing; readers without JS get `name at host dot com`.
Publish a forwarding alias here, not the primary work address.

The mailing-list block is a field and a **Subscribe** button. It posts to
`api/subscribe.js` on this same site, which creates the subscription on
cre-radar's **own beehiiv publication** through beehiiv's v2 API, tagged
`utm_medium=cre-radar`. Nothing is stored in this repo — beehiiv is the list.

Its own publication, rather than the firm's `Website - Spec Contact & Insights`
audience: this digest has a different cadence and its own reason to
unsubscribe, and mixing the two would cost the firm's list a subscriber every
time someone tires of the events.

Posting same-origin keeps the page's zero-external-requests property. Without
JavaScript the form is a plain POST and the function answers with a page.
Setting `BEEHIIV_EMBED_URL` in `site.py` swaps the whole block for beehiiv's
own embedded iframe instead — an escape hatch, and the one thing that gives up
the zero-external-requests property.

Set these on the **Vercel project**, not in `.env` (which is never deployed):

| Variable | |
|---|---|
| `BEEHIIV_API_KEY` | required. beehiiv → Settings → API. |
| `BEEHIIV_PUBLICATION_ID` | required. The cre-radar publication, `pub_…`. |
| `BEEHIIV_SUBSCRIBE_URL` | optional. The publication's hosted signup page, where a failed signup is sent. Also set `SUBSCRIBE_URL` in `site.py` so the script offers the same link. |
| `RESEND_API_KEY`, `SUBSCRIBE_TO` | optional. Emails a copy of each signup to the radar alias. Best-effort: a mail failure never fails a signup beehiiv already accepted. |

Missing either required variable logs the names and returns a 500 rather than
showing a thank-you for a signup nobody received.

The four link constants at the top of `site.py` — `LINKEDIN_URL`,
`CONTACT_EMAIL`, `BEEHIIV_EMBED_URL`, `SUBSCRIBE_URL` — each omit their link
when left empty, rather than shipping a dead or wrong one.

## Setup

```bash
cd cre-radar
uv sync
uv run playwright install chromium      # 7 sources need a real browser
cp .env.example .env && chmod 600 .env  # fill in Resend + vault path
uv run cre-radar doctor                 # verifies everything before you rely on it
```

| Want | Set in `.env` |
|---|---|
| The Obsidian note | `OBSIDIAN_DIGEST_DIR` |
| The email | `RESEND_API_KEY`, `DIGEST_FROM`, `DIGEST_TO` |
| A different cut line | `CRE_MIN_SCORE` (default 55) |

## Use

```bash
uv run cre-radar run                # collect → score → publish → deliver. The cron entry point.

uv run cre-radar collect            # fetch + extract only
uv run cre-radar collect --force    # re-extract even unchanged calendars
uv run cre-radar score              # score anything unjudged
uv run cre-radar rescore            # re-score everything after editing scoring.toml
uv run cre-radar digest --dry-run   # see the digest without sending or marking

uv run cre-radar publish            # regenerate public/index.html
uv run cre-radar publish --deploy   # ...and push it to Vercel
uv run cre-radar doctor             # is everything configured?
uv run cre-radar status             # recent runs + what's queued
uv run cre-radar sources check --browser
```

### Daily

```bash
./scripts/install-cron.sh        # 07:00 by default; pass an hour to change it
crontab -l | grep cre-radar      # verify
tail -f cron.log                 # watch
```

## The site

`publish` renders `public/index.html` — one self-contained file, no build step, no
framework, no external requests. Vercel serves it as a static site, alongside a
single function at `api/subscribe.js` (the signup box, and the only server-side
code in the project).

It answers a different question from the email. The digest asks "what haven't I
told you yet"; the page asks "what is coming up", so events stay listed until
they happen whether or not they were emailed. It filters by category client-side
and follows your system light/dark setting.

Deploy config lives in `vercel.json` (framework detection **off** — otherwise
Vercel sees `pyproject.toml` and tries to build a Python app) and `.vercelignore`
(only the generated page and `api/` ship; the extractor never leaves your
machine).

```bash
vercel login                        # once
uv run cre-radar publish --deploy
```

## Tuning

**Something irrelevant got through** → add the term to `[negative]` in
`scoring.toml`, then `uv run cre-radar rescore`. No re-fetching.

**A whole class is wrong** → it is almost always the `[org]` weight, not the
keywords. One line there moves everything that body hosts.

**Something relevant was missed** → add it to `[positive]`, or raise the org's
weight, then `rescore`.

**Junk is being extracted as an event** → that is `heuristic.py`, not scoring.
Add the title to `_JUNK_TITLES` or the pattern to `_JUNK_PREFIXES`, then
`./scripts/rebuild.sh` — extraction rules only affect new extractions, so junk
already stored has to be rebuilt away.

`cre-radar digest --dry-run` after any change; it renders without consuming the
queue.

`tests/test_scoring.py` pins the calibration against real verdicts — the events
Shane endorsed, and the classes he rejected. Change a weight and those tests tell
you if you have walked the judgment back.

## Sources

20 enabled, 2 disabled. `sources check` reports **condensed characters**, not HTTP
status — a 200 serving a bot interstitial looks identical to a healthy fetch at
the transport layer, and that distinction is the whole point.

| Mode | Count | Sources |
|---|---|---|
| `html` | 11 | NAIOP SoCal, CREW LA, USC Lusk, AIR CRE, IREM, AAGLA, CSSA, SSA, CCA, BREAA, UCLA Anderson |
| `browser` | 8 | BOMA GLA, ICSC, USC Alumni, Eventbrite, Luma LA, Luma PropTech, Bisnow, Connect CRE |
| `rss` | 1 | Berkeley Haas Alumni |
| disabled | 2 | ULI LA (Cloudflare challenge), LABC (hard 403) |

Both disabled sources keep their entry and the reason, since a challenge policy
can be relaxed. Re-enable and re-run `sources check --browser` to retest.

## Contract

Ported from `sf-events-aggregator/lib/sources/types.ts` and `docs/IDENTITY.md`.
Every stream — pattern extraction, iCal, RSS, a future API — enters through the
adapter interface in `contracts.py`.

**Two layers of identity.** `(source, external_id)` answers "have I seen this row
from this source". `sha256(title|venue|local_minute)[:32]` answers "is this the
same event someone else already gave me".

**The winner rule.** When two sources describe one event, the higher
`verification_level` takes the visible fields and the loser is appended to
`secondary_sources` — so NAIOP's registration link beats Bisnow's write-up, and
you still see Bisnow covered it.

| Level | Meaning |
|---|---|
| `official` | the hosting org's own site |
| `trusted_partner` | the org's listing on a platform (Eventbrite, Luma) |
| `community` | editorial aggregators (Bisnow, Connect CRE) |

**Invariants**, enforced by `tests/test_contract.py`: `fetch()` is the only IO
method and never raises (failures are typed `SourceError`s); `normalize()` is
pure, with time supplied via `Provenance`; `persist.py` is the single writer;
every event carries a `source_url`.

**Timezone is required.** `starts_at` is stored UTC and rendered through
`identity.format_local_minute`. Slicing a UTC ISO string for a local date moves
an 18:00 PT event to the next day.

## Rubric

`rubrics/milestone-contract.md` follows the PGE method from
`sf-events-aggregator/docs/pge-rubric-guide-for-claude.md`: each dimension names
one specific failure mode and checks it deterministically — a test, a grep, or an
exit code, never a judgment call. The 118 tests implement it dimension by
dimension, and the heuristic tests run against real pages captured in `fixtures/`.

## What to edit

Most changes are a config file, not code. Work down this table — the first row
that matches is the cheapest fix.

| You want to change | Edit | Then run |
|---|---|---|
| What counts as relevant | `scoring.toml` | `cre-radar rescore` |
| Add / remove / disable a source | `sources.toml` | `./scripts/rebuild.sh` |
| The relevance floor (default 55) | `CRE_MIN_SCORE` in `.env` | `cre-radar rescore` |
| Who the email goes to | `DIGEST_TO` in `.env` | — |
| Where the Obsidian note lands | `OBSIDIAN_DIGEST_DIR` in `.env` | — |
| Anything on the public page | `src/cre_radar/site.py` | `cre-radar publish` |
| Your name, links, logo, list | constants at the top of `site.py` | `cre-radar publish` |
| The email or the note | `src/cre_radar/digest/render.py` | `cre-radar digest --dry-run` |
| How a page is parsed into events | `src/cre_radar/heuristic.py` | `./scripts/rebuild.sh` |

**Two kinds of tuning, two different commands.** Scoring changes re-judge rows
already in the database, so `rescore` is enough. Extraction changes only affect
*new* extractions — junk already stored stays until `rebuild.sh` re-runs it.

`uv run pytest -q` before and after. The calibration tests at the bottom of
`tests/test_scoring.py` encode a real verdict on a real digest; if a weight
change breaks them, the change is wrong.

## The public page, file by file

Everything the site is lives in **one module**, `src/cre_radar/site.py`, which
writes `public/index.html`. There is no framework, no build step and no
external request, so the page cannot break in a way the generator did not.

Read it top to bottom — it is ordered the way the page is:

| Lines | What |
|---|---|
| Constants | `LINKEDIN_URL`, `CONTACT_EMAIL`, `BEEHIIV_EMBED_URL`, `SUBSCRIBE_URL`, the logo paths, and the `BUILDER` / `FIRM` identity. **Most edits you want are here.** Any empty link constant omits its link rather than shipping a dead one. |
| `_mail_token` / `_mail_spelled` | The address obfuscation and its no-JS fallback |
| `_jsonld` | schema.org attribution for machines |
| `_data_uri` / `_logo` | Inlines `assets/mep-mark-*.png` as base64, one mark per theme |
| `_drawer` / `_nav` | The sticky bar and the two panels under it |
| `_subscribe` / `_about` | The contents of those panels |
| `_chip` / `_card` | One event row |
| `render` | The whole document — CSS, body, and the inline script, in one f-string |

Because `render` is a single f-string, **every literal brace in the CSS and JS
is doubled** (`{{` / `}}`). Miss one and you get a `KeyError` naming a CSS
property. `assets/` holds the source PNGs; they are inlined at render time and
never deployed.

## Layout

[`ARCHITECTURE.md`](ARCHITECTURE.md) is the full map — every directory, every
module, the storage schema, and the invariants with the test that enforces each.
The short version:

```
sources.toml              the source registry — edit to add a source
scoring.toml              the scorer — edit to retune relevance
.env                      secrets and per-machine paths (never committed)
assets/                   the firm's mark; inlined into the page, not deployed
public/index.html         the generated site — output, never edit by hand
rubrics/                  the PGE rubric the tests implement
fixtures/                 real pages, for offline extraction tests
scripts/                  install-cron.sh, rebuild.sh
tests/                    118 tests; conftest.py builds contract objects

src/cre_radar/
  cli.py, config.py, doctor.py    command surface, settings, pre-flight
  contracts.py                    the frozen adapter interface — read first
  sources/, adapters/, events.py  the registry, the streams, the harvest runner
  fetch.py, heuristic.py          get a page; read events out of it
  identity.py, persist.py, db.py  fingerprinting, the single writer, storage
  scoring.py, score.py            apply scoring.toml
  site.py, digest/                the page, the email, the vault note
```

**Four files carry the rules worth knowing before editing:** `contracts.py`
(the interface nothing may bypass), `persist.py` (the only writer — a write
around it skips cross-source dedupe), `identity.py` (must stay pure; a test
greps it for `datetime.now`), and `scoring.toml` (is the scorer). `CLAUDE.md`
lists the traps in each.
