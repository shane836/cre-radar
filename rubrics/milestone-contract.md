# Milestone — Unified Ingestion Contract

**Purpose:** Adopt `sf-events-aggregator`'s three-layer identity model and adapter
contract so every event stream — LLM page extraction, iCal, RSS, a future API —
lands through one interface with cross-source dedupe.

**Every check below is a deterministic test, a grep, or an exit code. No judgment.**
Per `docs/pge-rubric-guide-for-claude.md`: a mediocre architecture with a
failure-mode-targeted rubric beats a sophisticated one with a vague rubric. Each
dimension below is a hypothesis that one specific thing can go wrong.

**Iteration cap:** 3 per dimension. Past 3, mark `HUMAN-REVIEW-NEEDED` and stop.

---

## A. Contract conformance

| # | Failure mode | Check | Pass |
|---|---|---|---|
| A1 | An adapter writes to the DB directly | `grep -rn "conn.execute\|INSERT INTO" src/cre_radar/adapters/` | Empty — `persist.py` is the single writer |
| A2 | Adapters import each other | `grep -rn "from .llm_page\|from .feed" src/cre_radar/adapters/*.py` | Only in `__init__.py` / `build_adapter` |
| A3 | `normalize()` is impure | Call twice with identical `RawEvent` + `Provenance`; compare | Byte-identical `NormalizedEvent` |
| A4 | `normalize()` reads the clock | `grep -n "datetime.now\|time()" ` inside each `normalize` body | No match — time comes from `Provenance` |
| A5 | An event escapes without `source_url` | Adapter emits N events from a fixture; assert every `identity.source_url` is non-empty | All non-empty |
| A6 | `fetch()` raises instead of returning errors | Point an adapter at an unreachable host | Returns `FetchResult` with a `SourceError`, no exception |
| A7 | Both adapters satisfy the same Protocol | `isinstance(adapter, SourceAdapter)` for LLM and feed adapters | Both True |

## B. Identity invariants (pure functions, no DB)

| # | Failure mode | Check | Pass |
|---|---|---|---|
| B1 | Same-day two-session collision | `fingerprint(X, V, 09:00)` vs `fingerprint(X, V, 18:00)` | Different |
| B2 | UTC date drift on evening events | `2026-09-14T18:00 America/Los_Angeles` → `format_local_date` | `2026-09-14`, not `2026-09-15` |
| B3 | Diacritics split a match | `fingerprint("Café Forum")` vs `fingerprint("Cafe Forum")` | Identical |
| B4 | Punctuation causes a false merge | `fingerprint("A.I.R. CRE")` vs `fingerprint("AIR CRE")` | Different |
| B5 | Cross-source same-event collapse | Two adapters, same title+venue+local-minute+tz | Identical fingerprints |
| B6 | Undated events all collide into one | Two different undated titles | Different fingerprints |
| B7 | Timezone ignored in the key | Same wall-clock in `America/Los_Angeles` vs `America/New_York` | Different fingerprints |

## C. Persister behaviour (DB-touching)

| # | Failure mode | Check | Pass |
|---|---|---|---|
| C1 | Duplicate rows across sources | Persist same event from `community` then `official` | One row |
| C2 | Winner rule ignored | Persist `community` then `official` | Row shows official's url + level; community in `secondary_sources` |
| C3 | Loser overwrites the winner | Persist `official` then `community` | Official's url/level stick; community in `secondary_sources` |
| C4 | `secondary_sources` grows on every re-run | Persist the loser three times | Exactly one entry |
| C5 | Re-running a source duplicates rows | Persist the same event from the same source twice | One row, `updated=True` on the second |
| C6 | An update nulls out a populated field | Persist with `venue`, then again without it | `venue` retained |
| C7 | Single writer | `grep -rn "INSERT INTO events\|UPDATE events" src/` | Only `persist.py` |

## D. Cost control

| # | Failure mode | Check | Pass |
|---|---|---|---|
| D1 | Unchanged page still calls the model | Harvest twice, count extraction calls | Exactly 1 |
| D2 | A page printing today's date defeats the cache | Change prose without changing links | `unchanged=True`, 0 extra calls |
| D3 | A new event is missed by the cache | Add one link | Re-extracts |
| D4 | `--force` doesn't force | Harvest, then harvest with `force=True` | 2 calls |
| D5 | Failed extraction poisons the cache | Extraction raises, then succeeds | Second run re-extracts (no stored hash) |

## E. Build / lint / tests

| # | Check | Pass |
|---|---|---|
| E1 | `uv run ruff check .` | Exit 0 |
| E2 | `uv run pytest -q` | All green |
| E3 | No orphaned social references | `grep -rni "linkedin\|twitter\|\bx_\|PostIn" src/ tests/` | Empty |
