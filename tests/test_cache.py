"""Rubric D — work avoidance. An unchanged calendar must not be re-extracted."""
from __future__ import annotations

import pytest

from cre_radar import db as db_mod
from cre_radar import events, heuristic
from cre_radar.models import EventIn
from cre_radar.sources.registry import Source

PAGE = "<body>September<br>14<br><a href='/e/1'>Capital Markets Forum</a> 6:00 PM</body>"
SOURCE = Source(slug="test", org="Test Org", url="https://x.test/events", mode="html")


@pytest.fixture()
def spy(monkeypatch):
    """Count extraction calls; serve a fixed page. No network."""
    calls = []

    def fake_extract(text, **kwargs):
        calls.append(kwargs.get("org"))
        return [EventIn(title="Capital Markets Forum", url="https://x.test/e/1",
                        starts_at="2026-09-14T18:00", venue="JW Marriott")]

    monkeypatch.setattr("cre_radar.adapters.page.fetch_static", lambda url: PAGE)
    monkeypatch.setattr(heuristic, "extract", fake_extract)
    monkeypatch.setattr("cre_radar.adapters.page.heuristic.extract", fake_extract)
    return calls


def test_d1_unchanged_page_is_not_re_extracted(conn, spy):
    events.harvest(conn, SOURCE)
    result = events.harvest(conn, SOURCE)

    assert len(spy) == 1, "second run must not re-extract"
    assert result.unchanged is True
    assert result.ok is True


def test_d2_prose_change_without_a_new_link_is_not_a_change(conn, spy, monkeypatch):
    """Several sources print today's date. Full-text hashing would never hit."""
    events.harvest(conn, SOURCE)
    monkeypatch.setattr(
        "cre_radar.adapters.page.fetch_static",
        lambda url: PAGE + "<p>Updated Wednesday, August 26, 2026</p>",
    )

    assert events.harvest(conn, SOURCE).unchanged is True
    assert len(spy) == 1


def test_d3_a_new_event_link_re_extracts(conn, spy, monkeypatch):
    events.harvest(conn, SOURCE)
    monkeypatch.setattr(
        "cre_radar.adapters.page.fetch_static",
        lambda url: PAGE.replace("</body>", "<a href='/e/2'>Panel</a></body>"),
    )

    assert events.harvest(conn, SOURCE).unchanged is False
    assert len(spy) == 2


def test_d4_force_re_extracts(conn, spy):
    events.harvest(conn, SOURCE)

    assert events.harvest(conn, SOURCE, force=True).unchanged is False
    assert len(spy) == 2


def test_d5_failed_extraction_does_not_poison_the_cache(conn, monkeypatch):
    """A source that errored must retry next run, not be treated as cached."""
    monkeypatch.setattr("cre_radar.adapters.page.fetch_static", lambda url: PAGE)

    def boom(*args, **kwargs):
        raise RuntimeError("extractor blew up")

    monkeypatch.setattr("cre_radar.adapters.page.heuristic.extract", boom)
    result = events.harvest(conn, SOURCE)

    assert result.ok is False
    assert db_mod.last_page_hash(conn, "test") is None


def test_first_run_persists_what_it_extracted(conn, spy):
    result = events.harvest(conn, SOURCE)

    assert result.found == 1
    assert result.inserted == 1


def test_d6_linkless_title_changes_invalidate_cache(conn, spy, monkeypatch):
    source = Source(slug="test", org="Test Org", url=SOURCE.url, linkless=True)
    events.harvest(conn, source)
    monkeypatch.setattr("cre_radar.adapters.page.fetch_static", lambda url:
                        PAGE.replace("Capital Markets Forum", "Storage Investment Forum"))
    assert events.harvest(conn, source).unchanged is False
    assert len(spy) == 2


def test_d7_detail_only_changes_are_refreshed_weekly(conn, spy):
    events.harvest(conn, SOURCE)
    conn.execute("UPDATE source_state SET extracted_at = datetime('now', '-8 days')")
    conn.commit()
    assert events.harvest(conn, SOURCE).unchanged is False
    assert len(spy) == 2


@pytest.mark.parametrize("html", [
    "<body>Just a moment... Performing security verification</body>",
    "<body><h1>Upcoming events</h1><p>No listings available</p></body>",
])
def test_d8_empty_extraction_is_reported_and_not_cached(conn, monkeypatch, html):
    monkeypatch.setattr("cre_radar.adapters.page.fetch_static", lambda url: html)
    result = events.harvest(conn, SOURCE)
    assert result.ok is False
    assert result.errors[0].stage == "parse"
    assert db_mod.last_page_hash(conn, SOURCE.slug) is None
