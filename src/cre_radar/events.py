"""The harvest runner — drives every adapter through the contract.

The loop is deliberately dull, because all the interesting behaviour lives behind
the contract: adapters decide how to fetch, `identity` decides what counts as the
same event, `persist` decides who wins a collision. This file only orchestrates
and reports.

One hard rule: **sources fail independently.** An adapter's `fetch()` returns
errors rather than raising, and the runner still wraps it, because a source that
violates the contract must not take the batch down either.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import db, persist
from .adapters import build_adapter
from .adapters.page import ADAPTER_VERSION, PIPELINE_VERSION
from .config import fetch_limit
from .contracts import Provenance, SourceError
from .sources.registry import Source, load


@dataclass
class SourceResult:
    """Outcome of one source, including how its events resolved against others."""

    slug: str
    found: int = 0
    inserted: int = 0
    merged_won: int = 0
    merged_lost: int = 0
    updated: int = 0
    ok: bool = True
    error: str | None = None
    partial: bool = False
    unchanged: bool = False
    errors: list[SourceError] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.unchanged:
            return "unchanged, skipped"
        bits = [f"{self.found:>3} found", f"+{self.inserted} new"]
        if self.updated:
            bits.append(f"{self.updated} updated")
        if self.merged_won:
            bits.append(f"{self.merged_won} won merge")
        if self.merged_lost:
            bits.append(f"{self.merged_lost} dup")
        if self.partial:
            bits.append("PARTIAL")
        if self.errors:
            bits.append(f"{len(self.errors)} item errors")
        return ", ".join(bits)


def harvest(
    conn: sqlite3.Connection, source: Source, *, force: bool = False
) -> SourceResult:
    """Fetch, normalize, and persist one source.

    Extraction is the expensive half — a browser render for several sources — so
    the adapter compares the page's link fingerprint against the last successful
    extraction and skips the whole thing when the calendar is unchanged.
    ``force=True`` re-extracts regardless: use it after editing `heuristic.py`,
    or on a weekly cron to catch detail-only edits.
    """
    known = None if force else db.last_page_hash(conn, source.slug)
    try:
        adapter = build_adapter(source, known_fingerprint=known)
        result = adapter.fetch()

        if result.unchanged:
            db.record_run(conn, source.slug, ok=True)
            return SourceResult(slug=source.slug, unchanged=True)

        # A source that produced nothing *and* reported an error failed, whatever
        # stage it failed at. Reporting that as a clean "+0 new" is the silent
        # failure the run log exists to prevent: a bot interstitial and a quiet
        # week look identical from the digest.
        if result.errors and not result.events:
            first = result.errors[0].message
            db.record_run(conn, source.slug, ok=False, error=first)
            return SourceResult(
                slug=source.slug, ok=False, error=first, errors=result.errors
            )

        provenance = Provenance(
            adapter_id=adapter.id,
            adapter_version=ADAPTER_VERSION,
            pipeline_version=PIPELINE_VERSION,
            normalized_at=datetime.now(UTC),
        )

        outcome = SourceResult(slug=source.slug, partial=result.partial,
                               errors=result.errors)
        for raw in result.events[: fetch_limit()]:
            written = persist.persist_event(conn, adapter.normalize(raw, provenance))
            outcome.found += 1
            outcome.inserted += written.inserted
            outcome.merged_won += written.won_merge
            outcome.merged_lost += written.lost_merge
            outcome.updated += written.updated

        page_hash = getattr(adapter, "page_fingerprint", None)
        if page_hash:
            db.remember_page_hash(conn, source.slug, page_hash)

    except Exception as exc:  # noqa: BLE001 — a contract violation must not abort the batch
        message = f"{type(exc).__name__}: {exc}"
        db.record_run(conn, source.slug, ok=False, error=message)
        return SourceResult(slug=source.slug, ok=False, error=message)

    db.record_run(
        conn, source.slug, ok=True, found=outcome.found, inserted=outcome.inserted,
        error="; ".join(e.message for e in result.errors) or None,
    )
    return outcome


def run(
    conn: sqlite3.Connection, only: list[str] | None = None, *, force: bool = False
) -> list[SourceResult]:
    """Harvest every enabled source (or just the slugs in ``only``)."""
    sources = load()
    if only:
        wanted = set(only)
        sources = [source for source in sources if source.slug in wanted]
    return [harvest(conn, source, force=force) for source in sources]


def render_for_scoring(row: sqlite3.Row) -> str:
    """Flatten an event row into the text the scorer judges."""
    parts = [f"{row['title']} — {row['org'] or row['source']}"]
    if row["starts_at"]:
        parts.append(f"When: {row['starts_at']} ({row['timezone']})")
    where = ", ".join(p for p in (row["venue"], row["city"]) if p)
    if where:
        parts.append(f"Where: {where}")
    if row["price"]:
        parts.append(f"Price: {row['price']}")
    if row["description"]:
        parts.append(row["description"][:1200])
    return "\n".join(parts)
