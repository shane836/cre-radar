# Architecture

How cre-radar is put together: what each directory is for, what each module owns,
and which rules a change must not break.

For *what it does and how to run it*, see [`README.md`](README.md). For the traps
that bite when editing, see [`CLAUDE.md`](CLAUDE.md).

## The shape of it

A batch pipeline. Five stages, each one command, each stage's output the next
stage's input, with SQLite as the boundary between them.

```
sources.toml
     │
  ┌──▼──────────┐   fetch (http | headless) ─► condense ─► heuristic extract
  │  COLLECT    │                                                │
  └─────────────┘                                             RawEvent
                                                                 │
                                                    normalize()  │  pure
                                                                 │
                                        canonical fingerprint ───┤
                                                                 ▼
  ┌─────────────┐                                       persist.py (sole writer:
  │  STORE      │  ◄──────────────────────────────────   dedupe + winner rule)
  └──────┬──────┘        SQLite: events, runs, source_state
         │
  ┌──────▼──────┐   scoring.toml ─► weights ─► score + reason + topics
  │  SCORE      │
  └──────┬──────┘
         │
  ┌──────▼──────────────────────────┐
  │  PUBLISH            DELIVER     │
  │  public/index.html  email + note│
  └─────────────────────────────────┘
```

Nothing in the runtime calls a model or needs an API key. The extractor is
pattern matching (`heuristic.py`); the scorer is a rules file (`scoring.toml`).
Same inputs, same digest, every time.

Stages are separate commands on purpose: retuning relevance re-judges rows
already stored and never re-fetches, and a source outage never leaves a
half-judged database.

| Stage | Command | Owns | Reads | Writes |
|---|---|---|---|---|
| Collect | `cre-radar collect` | fetch, condense, extract, normalize | `sources.toml` | `events`, `runs`, `source_state` |
| Score | `cre-radar score` / `rescore` | relevance judgment | `scoring.toml` | `events.score/reason/topics` |
| Publish | `cre-radar publish` | the public page | `events` | `public/index.html` |
| Deliver | `cre-radar digest` | email + vault note | `events` | Resend, the vault, `events.surfaced_at` |
| Everything | `cre-radar run` | the cron entry point | — | — |

## Directories

| Path | Purpose |
|---|---|
| `src/cre_radar/` | the whole application. See the module map below. |
| `src/cre_radar/adapters/` | one class per *kind of stream* (page, feed), not per site. Each implements the `SourceAdapter` protocol; none imports another. |
| `src/cre_radar/sources/` | the registry loader — turns `sources.toml` rows into `Source` objects. Sources are data; this is the only code that reads them. |
| `src/cre_radar/digest/` | the outbound channels: layout (`render.py`), email transport (`email.py`), vault file (`obsidian.py`). Layout is separate from transport so `--dry-run` renders without sending. |
| `tests/` | 118 tests. `conftest.py` builds contract objects so tests never hand-roll a `NormalizedEvent`. |
| `fixtures/` | real captured pages (AAGLA, BREAA, NAIOP SoCal). The extractor tests run offline against these — extraction regressions are caught without a network. |
| `rubrics/` | `milestone-contract.md`, the PGE rubric the tests implement. Add a dimension there *before* adding a test. |
| `scripts/` | `rebuild.sh` (drop and re-extract after an extraction change), `install-cron.sh` (the daily 07:00 entry). |
| `assets/` | the firm's mark, light and dark. Inlined as base64 at render time — never deployed. |
| `public/` | generated output. `index.html` is written by `publish` and served by Vercel. Never edit by hand; it is gitignored. |

Top-level files:

| File | Purpose |
|---|---|
| `sources.toml` | the source registry. Adding a source is a four-line block, never code. |
| `scoring.toml` | **is** the scorer. Base score plus weights for org, subject, geography, kind. |
| `.env` | secrets and per-machine paths. Never committed; `.env.example` carries blank placeholders. |
| `vercel.json` | `framework: null` and an empty `buildCommand`, deliberately — with detection on, Vercel finds `pyproject.toml` and fails. |
| `.vercelignore` | only `public/` ships. The extractor and the database never leave the machine. |
| `cre_radar.db` | the SQLite store. Gitignored; `rebuild.sh` keeps a `.bak`. |

## Module map

Ordered the way data moves.

### Entry and configuration

| Module | Responsibility |
|---|---|
| `cli.py` | the command surface. Typer. `run` is what cron calls; every other command is one stage of it. |
| `config.py` | every setting, read from the environment / `.env`. No secret and no absolute path is hardcoded, and each getter has a working default so an unconfigured channel no-ops instead of crashing. |
| `doctor.py` | pre-flight: "will `run` work, and if not, why". Every failure it reports is configuration, so every check names the fix. |

### Ingestion

| Module | Responsibility |
|---|---|
| `contracts.py` | the frozen adapter interface — `RawEvent`, `NormalizedEvent`, `FetchResult`, `SourceError`, `Provenance`, `SourceAdapter`. **Read this before touching ingestion.** Ported from `sf-events-aggregator/lib/sources/types.ts`. |
| `sources/registry.py` | loads `sources.toml`, returns enabled `Source` objects in file order. |
| `events.py` | the harvest runner. Deliberately dull: it orchestrates and reports, and all the behaviour lives behind the contract. Sources fail independently — the runner wraps each adapter so a contract violation cannot abort the batch. |
| `fetch.py` | HTTP and headless fetching, `condense()`, `link_fingerprint()`. `condense` strips chrome and markup but **keeps anchor hrefs inline**, which is what makes generic extraction able to return a real registration URL. |
| `adapters/page.py` | fetch → condense → extract → `RawEvent`. Used by `html` and `browser` sources. Also carries `build_adapter`, which picks an adapter from the source's mode. |
| `adapters/feed.py` | RSS/iCal. The structured path — no extraction at all. Same contract, so the runner and the persister cannot tell the difference. |
| `adapters/timeparse.py` | a source's date string → aware UTC datetime. Shared, so the interpretation is identical everywhere: a wall-clock string is local to *that source's* timezone, never UTC. |
| `heuristic.py` | the pattern extractor. Reads the shape every event listing shares — a date, a linked title, a time — plus explicit handling for the failure modes actually observed (stale dates, nav links, unlinked titles, dates below the title). The largest single piece of logic in the repo and the one `rebuild.sh` exists for. |
| `places.py` | the city, from whatever text a listing gives. A gazetteer, longest name first, so "West Hollywood" beats "Hollywood". Separate from `scoring.toml`'s `socal_cities` on purpose: one is a fact about the page, the other a preference you tune. |

### Identity and storage

| Module | Responsibility |
|---|---|
| `identity.py` | title/venue normalization, `fingerprint()`, and timezone-correct local formatting. **Pure — no clock, no network, no DB.** |
| `models.py` | the pydantic types shared across stages (`EventIn`, `Verdict`). |
| `persist.py` | **the single writer.** Cross-source dedupe by fingerprint, plus the verification-level winner rule. A write that bypasses it bypasses both. |
| `db.py` | schema, `normalize_url()` (the URL dedupe chokepoint), and every read query. |

### Scoring

| Module | Responsibility |
|---|---|
| `scoring.py` | applies `scoring.toml`. Pure. Records every rule that fired, so the digest's reason line is a list of what actually matched rather than a generated sentence. |
| `score.py` | walks unjudged rows through `scoring.py`; `rescore` re-judges everything. |

### Output

| Module | Responsibility |
|---|---|
| `site.py` | the whole public page, in one module, written to `public/index.html`. No framework, no build step, no external request. |
| `digest/render.py` | the email HTML and the Markdown note. Email HTML is tables and inline styles — mail clients drop `<style>` blocks — and ASCII-only, because an email body has no `<head>` to declare a charset. |
| `digest/email.py` | Resend. Unconfigured is not an error: the send is skipped and reported, the note still lands, the run still exits clean. |
| `digest/obsidian.py` | writes the dated vault note. Never overwrites — a second run the same day gets a numbered suffix. |

## Identity: three layers

The reason the same panel written up by three outlets is one row.

| Layer | Key | Answers |
|---|---|---|
| Source | `(source, external_id)` | have I seen this row from this source? |
| Canonical | `sha256(title \| venue \| local_minute)[:32]` | is this the same event someone else gave me? |
| URL | `db.normalize_url()` | is this the same link wearing different tracking params? |

The canonical fingerprint is derived from the event's own content, so two
adapters produce the same value with **no shared state**. Minute granularity is
deliberate: a morning and an evening session of one program are different events.

**The winner rule.** On a fingerprint collision the higher `verification_level`
takes the visible fields; the loser is appended to `secondary_sources` rather
than dropped. NAIOP's registration link beats Bisnow's write-up, and you still
see that Bisnow covered it.

| Level | Meaning | Rank |
|---|---|---|
| `official` | the hosting org's own site | 3 |
| `trusted_partner` | the org's listing on a platform (Eventbrite, Luma) | 2 |
| `community` | editorial aggregators (Bisnow, Connect CRE) | 1 |
| `unverified` | anything else | 0 |

## Storage

Three tables, all in `db.py`.

| Table | Holds | Why |
|---|---|---|
| `events` | one row per canonical event, including its score, reason, `secondary_sources`, and `surfaced_at` | `canonical_fingerprint` is `UNIQUE` — the dedupe is enforced by the schema, not by convention |
| `runs` | one row per source per run: ok, found, inserted, error | a source that returns zero events *and* an error is a failed run, not a quiet one, and this is where that shows |
| `source_state` | last page fingerprint per source | an unchanged calendar is skipped entirely; `--force` ignores it |

`starts_at` is stored **UTC**. Every display path goes through
`identity.format_local_minute` — slicing a UTC ISO string for a local date moves
an 18:00 PT event onto the next day.

`pending_events` and `upcoming_events` answer different questions. The digest
asks "what haven't I told you yet" (unsurfaced only); the site asks "what is
coming up" (everything future, surfaced or not).

## Invariants

Each is enforced, not merely documented. `tests/test_contract.py` implements the
rubric in `rubrics/milestone-contract.md`.

| Invariant | Enforced by |
|---|---|
| `fetch()` never raises — failures are `SourceError` values | `test_contract.py`, and the runner wraps adapters anyway |
| `normalize()` is pure; time arrives via `Provenance` | `test_contract.py` greps the source for `datetime.now` |
| `persist.py` is the single writer | `test_persist.py`; adapters import no DB module |
| Every event has a `source_url` | the contract's required field |
| Adapters never import each other | the package layout |
| Unchanged calendars are skipped | `test_cache.py` |
| The public page leaks no plain address | `test_site.py` asserts no `user@host.tld` in the output |
| A closed drawer is `visibility:hidden`, not just `max-height:0` | `test_site.py` — otherwise its links stay in the tab order |
| The scoring calibration matches a real verdict | the tests at the bottom of `test_scoring.py`. If a weight change breaks them, the change is wrong. |

## The control surface

Adding a source or retuning relevance must never require a code change.

| To change | Edit | Then run |
|---|---|---|
| What counts as relevant | `scoring.toml` | `cre-radar rescore` |
| Add / remove / disable a source | `sources.toml` | `./scripts/rebuild.sh` |
| The relevance floor | `CRE_MIN_SCORE` in `.env` | `cre-radar rescore` |
| Delivery targets and paths | `.env` | — |
| How a page is parsed | `heuristic.py` | `./scripts/rebuild.sh` |
| The public page | `site.py` | `cre-radar publish` |
| The email or the note | `digest/render.py` | `cre-radar digest --dry-run` |

**Two kinds of tuning, two different fixes.** Scoring re-judges rows already
stored, so `rescore` suffices. Extraction rules only affect *new* extractions —
junk already in the database stays until `rebuild.sh` re-runs it.

## Extending it

**A new source of an existing kind** — a TOML block in `sources.toml`
(`slug`, `org`, `url`, `mode`, `verification_level`). No code. Verify with
`cre-radar sources check --browser`, which reports **condensed characters**, not
HTTP status: a bot interstitial returns 200, and that distinction is the point.

**A new kind of stream** (an API, a partner feed) — a class in `adapters/`
implementing `SourceAdapter`, plus a branch in `build_adapter`. The runner, the
persister and the scorer need no change, because they only know the contract.

**A new output channel** — a module under `digest/` that takes rendered rows.
Keep layout in `render.py` and transport in the new module, so `--dry-run` keeps
working.

## Deployment

Only `public/index.html` deploys. `vercel.json` disables framework detection —
with it on, Vercel finds `pyproject.toml` and fails with "No python entrypoint
found" — and `.vercelignore` keeps everything but the generated page out. Deploy
from the repo root; deploying from `public/` creates a second Vercel project
named "public".

The collector, the database and the vault note never leave the machine that runs
the cron job.
