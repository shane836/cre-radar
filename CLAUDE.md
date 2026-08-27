# CLAUDE.md — cre-radar

Finds LA commercial real estate events, scores them against `scoring.toml`, and
delivers a daily email + Obsidian note. Full design: [`README.md`](README.md).

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
./scripts/rebuild.sh                # after changing EXTRACTION rules
```

## Architecture

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

## The mailing list is not this project's

**One beehiiv audience, tagged by source.** `Website - Spec Contact & Insights`
in the vault is explicit: never run a second list. cre-radar signups are
`utm_medium=cre-radar` against the same publication the firm's site feeds.
Do not add a subscribers table here.

The block is a button by default, which is why the page still makes **zero
external requests**. Setting `BEEHIIV_EMBED_URL` trades that for an inline
form — a deliberate exception, not an oversight, and the only one.

Do not try to post directly to beehiiv: `/create` needs a per-session
`visit_token` a static page cannot mint, and `/subscribe?email=` does not
prefill, so a hand-rolled field makes the visitor type the address twice.
Both verified against the live page, Aug 2026.

## Vercel

`vercel.json` sets `framework: null` and `buildCommand: ""` deliberately — with
detection on, Vercel finds `pyproject.toml` and fails with "No python entrypoint
found". `.vercelignore` keeps everything but `public/` off the deployment. Deploy
from the repo root, never from `public/` (that creates a second project named
"public").

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
