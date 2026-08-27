"""Rubric C — the persister. Cross-source dedupe and the winner rule.

This is where three sites writing up one panel become one row, and where the
hosting org's registration link beats an aggregator's news post.
"""
from __future__ import annotations

import json

from cre_radar.persist import persist_event

from .conftest import make_event


def rows(conn):
    return conn.execute("SELECT * FROM events").fetchall()


def test_c1_same_event_from_two_sources_is_one_row(conn):
    persist_event(conn, make_event(source="llm:bisnow-la", level="community"))
    persist_event(conn, make_event(source="llm:naiop-socal", level="official"))

    assert len(rows(conn)) == 1


def test_c2_higher_verification_takes_the_row(conn):
    persist_event(conn, make_event(
        source="llm:bisnow-la", level="community", url="https://bisnow.test/post"))
    outcome = persist_event(conn, make_event(
        source="llm:naiop-socal", level="official", url="https://naiop.test/register"))

    row = rows(conn)[0]
    assert outcome.won_merge is True
    assert row["verification_level"] == "official"
    assert row["url"] == "https://naiop.test/register"

    secondaries = json.loads(row["secondary_sources"])
    assert [s["source"] for s in secondaries] == ["llm:bisnow-la"]


def test_c3_lower_verification_does_not_overwrite(conn):
    persist_event(conn, make_event(
        source="llm:naiop-socal", level="official", url="https://naiop.test/register"))
    outcome = persist_event(conn, make_event(
        source="llm:bisnow-la", level="community", url="https://bisnow.test/post"))

    row = rows(conn)[0]
    assert outcome.lost_merge is True
    assert row["verification_level"] == "official"
    assert row["url"] == "https://naiop.test/register"
    assert json.loads(row["secondary_sources"])[0]["source"] == "llm:bisnow-la"


def test_c4_secondary_sources_do_not_grow_on_re_runs(conn):
    persist_event(conn, make_event(source="llm:naiop-socal", level="official"))
    for _ in range(3):
        persist_event(conn, make_event(
            source="llm:bisnow-la", level="community", url="https://bisnow.test/post"))

    assert len(json.loads(rows(conn)[0]["secondary_sources"])) == 1


def test_c5_re_running_one_source_updates_rather_than_duplicates(conn):
    first = persist_event(conn, make_event(source="llm:cssa", level="official"))
    second = persist_event(conn, make_event(source="llm:cssa", level="official"))

    assert first.inserted is True
    assert second.updated is True
    assert len(rows(conn)) == 1


def test_c6_an_update_never_nulls_a_populated_field(conn):
    persist_event(conn, make_event(
        source="llm:cssa", level="official", price="$95 members", description="Panel."))
    persist_event(conn, make_event(
        source="llm:cssa", level="official", price=None, description=None))

    row = rows(conn)[0]
    assert row["price"] == "$95 members"
    assert row["description"] == "Panel."


def test_a3_normalize_is_pure(provenance):
    """Rubric A3: same input + same provenance → byte-identical output."""
    from cre_radar.adapters.page import PageAdapter
    from cre_radar.contracts import ExternalIdentity, RawEvent, VenueCandidate
    from cre_radar.sources.registry import Source

    from .conftest import LA, STAMP, WHEN

    source = Source(slug="t", org="Test", url="https://t.test/events")
    adapter = PageAdapter(source)
    raw = RawEvent(
        identity=ExternalIdentity(source="llm:t", external_id="1",
                                  source_url="https://t.test/e/1"),
        title="Forum", start_time_utc=WHEN, timezone=LA,
        venue=VenueCandidate(name="JW Marriott"), fetched_at=STAMP,
    )

    assert adapter.normalize(raw, provenance) == adapter.normalize(raw, provenance)
