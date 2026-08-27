"""The single writer. Every row that reaches the events table goes through here.

Two rules from `sf-events-aggregator/docs/IDENTITY.md` do the work:

**Cross-source dedupe by canonical fingerprint.** Bisnow, Connect CRE and NAIOP
all writing up the same panel produce one row, not three — without any shared
state between adapters, because the fingerprint is derived from the event's own
title, venue and local minute.

**Verification-level winner rule.** When two sources collide, the higher level
wins the visible fields and the loser is appended to ``secondary_sources`` rather
than discarded. The hosting org's own listing beats an aggregator's write-up of
it, which is usually the difference between a correct registration link and a
paywalled news post.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .contracts import NormalizedEvent, rank
from .db import normalize_url


@dataclass
class PersistOutcome:
    """What happened to one event, so the runner can report it honestly."""

    inserted: bool = False
    won_merge: bool = False    # replaced a lower-verification row
    lost_merge: bool = False   # recorded as a secondary source on an existing row
    updated: bool = False      # same source, refreshed fields


def _secondary(event: NormalizedEvent) -> dict:
    return {
        "source": event.identity.source,
        "external_id": event.identity.external_id,
        "source_url": event.identity.source_url,
        "verification_level": event.verification_level,
        "observed_at": event.provenance.normalized_at.isoformat(),
    }


def _load_secondaries(raw: str | None) -> list[dict]:
    try:
        return json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []


def _append_secondary(existing: str | None, entry: dict) -> str:
    """Append, de-duplicating on (source, external_id) so re-runs don't grow it."""
    entries = _load_secondaries(existing)
    key = (entry["source"], entry["external_id"])
    if not any((e.get("source"), e.get("external_id")) == key for e in entries):
        entries.append(entry)
    return json.dumps(entries)


def persist_event(conn: sqlite3.Connection, event: NormalizedEvent) -> PersistOutcome:
    """Insert, update, or merge one normalized event. Returns what it did."""
    url = normalize_url(event.identity.source_url)
    starts = event.start_time_utc.isoformat() if event.start_time_utc else None
    ends = event.end_time_utc.isoformat() if event.end_time_utc else None

    row = conn.execute(
        "SELECT * FROM events WHERE canonical_fingerprint = ?",
        (event.canonical_fingerprint,),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO events (canonical_fingerprint, source, external_id, url, org,
                                title, starts_at, ends_at, timezone, venue, city, price,
                                category, description, verification_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.canonical_fingerprint, event.identity.source,
                event.identity.external_id, url, event.org, event.title, starts, ends,
                event.timezone, event.venue.name, event.venue.city,
                event.pricing.display, event.category, event.description,
                event.verification_level,
            ),
        )
        conn.commit()
        return PersistOutcome(inserted=True)

    same_source = (
        row["source"] == event.identity.source
        and row["external_id"] == event.identity.external_id
    )

    if same_source:
        # Refresh what can legitimately change; never clobber a value with a null.
        conn.execute(
            """
            UPDATE events SET
                title = ?,
                starts_at = COALESCE(?, starts_at),
                ends_at = COALESCE(?, ends_at),
                venue = COALESCE(?, venue),
                city = COALESCE(?, city),
                price = COALESCE(?, price),
                description = COALESCE(?, description),
                category = ?
            WHERE canonical_fingerprint = ?
            """,
            (
                event.title, starts, ends, event.venue.name, event.venue.city,
                event.pricing.display, event.description, event.category,
                event.canonical_fingerprint,
            ),
        )
        conn.commit()
        return PersistOutcome(updated=True)

    if rank(event.verification_level) > rank(row["verification_level"]):
        # The newcomer outranks the incumbent: it takes the visible fields, and
        # the incumbent is preserved as attribution.
        demoted = {
            "source": row["source"],
            "external_id": row["external_id"],
            "source_url": row["url"],
            "verification_level": row["verification_level"],
            "observed_at": row["discovered_at"],
        }
        conn.execute(
            """
            UPDATE events SET
                source = ?, external_id = ?, url = ?, title = ?, org = COALESCE(?, org),
                starts_at = COALESCE(?, starts_at), ends_at = COALESCE(?, ends_at),
                venue = COALESCE(?, venue), city = COALESCE(?, city),
                price = COALESCE(?, price), description = COALESCE(?, description),
                category = ?, verification_level = ?, secondary_sources = ?
            WHERE canonical_fingerprint = ?
            """,
            (
                event.identity.source, event.identity.external_id, url, event.title,
                event.org, starts, ends, event.venue.name, event.venue.city,
                event.pricing.display, event.description, event.category,
                event.verification_level,
                _append_secondary(row["secondary_sources"], demoted),
                event.canonical_fingerprint,
            ),
        )
        conn.commit()
        return PersistOutcome(won_merge=True)

    # Incumbent keeps the row; the newcomer is recorded as attribution only.
    conn.execute(
        "UPDATE events SET secondary_sources = ? WHERE canonical_fingerprint = ?",
        (
            _append_secondary(row["secondary_sources"], _secondary(event)),
            event.canonical_fingerprint,
        ),
    )
    conn.commit()
    return PersistOutcome(lost_merge=True)


def is_unscored(conn: sqlite3.Connection, canonical_fingerprint: str) -> bool:
    """True when the row exists and has not yet been judged."""
    row = conn.execute(
        "SELECT score FROM events WHERE canonical_fingerprint = ?",
        (canonical_fingerprint,),
    ).fetchone()
    return row is not None and row["score"] is None
