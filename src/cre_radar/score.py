"""Score every unjudged event against `scoring.toml`.

Kept separate from collection so retuning the rules and re-scoring never requires
re-fetching anything, and a source outage never leaves a half-judged database.
`rescore` exists for exactly that: edit the rules, re-run, see the new digest.
"""
from __future__ import annotations

import sqlite3

from . import db
from .scoring import load_rules, score_row


def run(conn: sqlite3.Connection) -> int:
    """Score everything not yet judged. Returns how many were scored."""
    rules = load_rules()
    rows = db.unscored(conn, "events")
    for row in rows:
        db.apply_verdict(conn, "events", row["id"], score_row(row, rules))
    return len(rows)


def rescore(conn: sqlite3.Connection) -> int:
    """Re-score every event, including already-judged ones.

    Use after editing `scoring.toml`. Events already delivered stay delivered —
    this changes their score, not whether you have seen them.
    """
    load_rules.cache_clear()
    rules = load_rules()
    rows = conn.execute("SELECT * FROM events").fetchall()
    for row in rows:
        db.apply_verdict(conn, "events", row["id"], score_row(row, rules))
    return len(rows)
