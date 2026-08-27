"""URL normalization and the surfacing lifecycle — where a bug would silently
corrupt the digest."""
from __future__ import annotations

import pytest

from cre_radar import db
from cre_radar.models import Verdict
from cre_radar.persist import persist_event

from .conftest import make_event


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/Events/", "https://example.com/Events"),
        ("https://example.com/e?utm_source=x&id=7", "https://example.com/e?id=7"),
        ("https://example.com/e#agenda", "https://example.com/e"),
        ("https://infohub.bomagla.org/d/x?sourceTypeId=Website", "https://infohub.bomagla.org/d/x"),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_normalize_url_collapses_cosmetic_variation(raw, expected):
    assert db.normalize_url(raw) == expected


def test_pending_excludes_low_scores_and_already_surfaced(conn):
    persist_event(conn, make_event(source="a", level="official", title="Keep"))
    persist_event(conn, make_event(source="b", level="official", title="Drop"))
    rows = conn.execute("SELECT id, title FROM events ORDER BY id").fetchall()
    for row in rows:
        db.apply_verdict(conn, "events", row["id"],
                         Verdict(score=90 if row["title"] == "Keep" else 30, reason="r"))

    pending = db.pending_events(conn, 55)
    assert [row["title"] for row in pending] == ["Keep"]

    db.mark_surfaced(conn, "events", [pending[0]["id"]])
    assert db.pending_events(conn, 55) == []


def test_scored_row_is_not_reported_as_unscored(conn):
    from cre_radar.persist import is_unscored

    event = make_event(source="a", level="official")
    persist_event(conn, event)
    assert is_unscored(conn, event.canonical_fingerprint) is True

    row = conn.execute("SELECT id FROM events").fetchone()
    db.apply_verdict(conn, "events", row["id"], Verdict(score=80, reason="r"))
    assert is_unscored(conn, event.canonical_fingerprint) is False
