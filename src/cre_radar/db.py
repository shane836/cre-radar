"""SQLite storage: schema, the URL-normalization dedupe chokepoint, and reads.

Every write goes through :func:`upsert_event`, which normalizes the URL first and lean on a ``UNIQUE`` constraint for dedupe. Re-running
a source is therefore always safe — a seen item updates in place instead of
producing a duplicate row.
"""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import db_path
from .models import Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Layer 2 identity. Cross-source dedupe hangs off this unique index.
    canonical_fingerprint TEXT UNIQUE NOT NULL,
    -- Layer 1 identity, for the winning source.
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    org TEXT,
    title TEXT NOT NULL,
    starts_at TEXT,              -- ISO 8601, UTC
    ends_at TEXT,
    timezone TEXT NOT NULL,
    venue TEXT,
    city TEXT,
    price TEXT,
    category TEXT,
    description TEXT,
    verification_level TEXT NOT NULL,
    secondary_sources TEXT NOT NULL DEFAULT '[]',
    score INTEGER,
    reason TEXT,
    topics TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    surfaced_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_starts ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_events_surfaced ON events(surfaced_at);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source, external_id);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ok INTEGER NOT NULL,
    found INTEGER DEFAULT 0,
    inserted INTEGER DEFAULT 0,
    error TEXT,
    ran_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_ran ON runs(ran_at);

CREATE TABLE IF NOT EXISTS source_state (
    slug TEXT PRIMARY KEY,
    page_hash TEXT NOT NULL,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Query params that only ever identify a click or campaign, never the item.
_TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "igshid", "mc_eid", "mc_cid", "ref", "ref_src", "ref_url",
    "source", "sourceTypeId", "s", "t", "trk", "trackingId", "originalSubdomain",
})


def normalize_url(url: str) -> str:
    """Collapse cosmetic URL variations so the same item dedupes to one row.

    Lowercases scheme and host, strips ``utm_*`` plus the known tracking params
    above, drops the fragment, and removes a trailing slash from non-root paths.
    Every insert path goes through here — it is the single dedupe chokepoint.
    """
    parts = urlsplit(url.strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k not in _TRACKING_PARAMS
    ]
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), ""))


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open a connection with the schema ensured and dict-like rows."""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _is_new(conn: sqlite3.Connection, table: str, url: str) -> bool:
    """True when the row for ``url`` has never been scored — i.e. it is fresh.

    Dedupe is by URL, so "new" means "arrived in this run and not yet judged",
    which is exactly the set the scorer needs to look at.
    """
    row = conn.execute(f"SELECT score FROM {table} WHERE url = ?", (url,)).fetchone()
    return row is not None and row["score"] is None


def unscored(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    """Rows that have arrived but not yet been judged by the scorer."""
    return conn.execute(
        f"SELECT * FROM {table} WHERE score IS NULL ORDER BY id"
    ).fetchall()


def apply_verdict(conn: sqlite3.Connection, table: str, row_id: int, verdict: Verdict) -> None:
    """Write one scorer verdict back onto its row."""
    conn.execute(
        f"UPDATE {table} SET score = ?, reason = ?, topics = ? WHERE id = ?",
        (verdict.score, verdict.reason, json.dumps(verdict.topics), row_id),
    )
    conn.commit()


def pending_events(conn: sqlite3.Connection, floor: int) -> list[sqlite3.Row]:
    """Scored, never-surfaced, not-yet-past events at or above the score floor."""
    return conn.execute(
        """
        SELECT * FROM events
        WHERE surfaced_at IS NULL AND score >= ?
          AND (starts_at IS NULL OR starts_at >= date('now', '-1 day'))
        ORDER BY starts_at IS NULL, starts_at, score DESC
        """,
        (floor,),
    ).fetchall()


def upcoming_events(conn: sqlite3.Connection, floor: int) -> list[sqlite3.Row]:
    """Every future event at or above the floor, surfaced or not.

    Different question from :func:`pending_events`: the digest asks "what haven't
    I told you yet", the site asks "what is coming up". An event stays on the
    site until it happens, whether or not it was emailed.
    """
    return conn.execute(
        """
        SELECT * FROM events
        WHERE score >= ?
          AND starts_at IS NOT NULL
          AND starts_at >= datetime('now', '-12 hours')
        ORDER BY starts_at
        """,
        (floor,),
    ).fetchall()


def mark_surfaced(conn: sqlite3.Connection, table: str, ids: list[int]) -> None:
    """Stamp rows as delivered so tomorrow's digest does not repeat them."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE {table} SET surfaced_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()


def record_run(
    conn: sqlite3.Connection, source: str, *,
    ok: bool, found: int = 0, inserted: int = 0, error: str | None = None,
) -> None:
    """Log one source's outcome so `cre-radar status` can show what is rotting."""
    conn.execute(
        "INSERT INTO runs (source, ok, found, inserted, error) VALUES (?, ?, ?, ?, ?)",
        (source, int(ok), found, inserted, error),
    )
    conn.commit()


def last_page_hash(conn: sqlite3.Connection, slug: str) -> str | None:
    """Hash of the page the last *successful* extraction ran on, if any."""
    row = conn.execute(
        "SELECT page_hash FROM source_state WHERE slug = ?", (slug,)
    ).fetchone()
    return row["page_hash"] if row else None


def remember_page_hash(conn: sqlite3.Connection, slug: str, page_hash: str) -> None:
    """Record what we just extracted, so an unchanged page can be skipped."""
    conn.execute(
        """
        INSERT INTO source_state (slug, page_hash) VALUES (?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            page_hash = excluded.page_hash,
            extracted_at = CURRENT_TIMESTAMP
        """,
        (slug, page_hash),
    )
    conn.commit()
