"""Rubric A — contract conformance. The invariants that let every stream share
one persister and one scorer."""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from cre_radar.adapters import FeedAdapter, PageAdapter, build_adapter
from cre_radar.contracts import SourceAdapter
from cre_radar.sources.registry import Source

SRC = Path(__file__).resolve().parents[1] / "src" / "cre_radar"
SOURCE = Source(slug="t", org="Test Org", url="https://t.invalid/events")


def test_a1_adapters_never_write_to_the_database():
    """persist.py is the single writer; an adapter that inserts breaks dedupe."""
    offenders = [
        path.name
        for path in (SRC / "adapters").glob("*.py")
        if re.search(r"INSERT INTO|conn\.execute", path.read_text())
    ]
    assert offenders == []


def test_a4_normalize_never_reads_the_clock():
    """Time must arrive via Provenance, or normalize() is not pure."""
    for adapter in (PageAdapter, FeedAdapter):
        body = inspect.getsource(adapter.normalize)
        assert "datetime.now" not in body, adapter.__name__


def test_a6_fetch_returns_errors_instead_of_raising():
    """An unreachable host must be data, not an exception — the batch continues."""
    result = PageAdapter(SOURCE).fetch()

    assert result.events == []
    assert len(result.errors) == 1
    assert result.errors[0].stage == "fetch"
    assert result.errors[0].retryable is True


def test_a7_both_adapters_satisfy_the_protocol():
    llm = PageAdapter(SOURCE)
    feed = FeedAdapter(Source(slug="f", org="F", url="https://f.invalid/f.ics", mode="ical"))

    assert isinstance(llm, SourceAdapter)
    assert isinstance(feed, SourceAdapter)


def test_build_adapter_routes_on_mode():
    assert isinstance(build_adapter(SOURCE), PageAdapter)
    assert isinstance(
        build_adapter(Source(slug="f", org="F", url="https://f.invalid", mode="ical")),
        FeedAdapter,
    )


def test_a5_every_emitted_event_carries_a_source_url(monkeypatch):
    """An event you cannot click through to is not an event."""
    from cre_radar.models import EventIn

    monkeypatch.setattr(
        "cre_radar.adapters.page.fetch_static",
        lambda url: "<body><a href='/e/1'>Forum</a></body>",
    )
    monkeypatch.setattr(
        "cre_radar.adapters.page.heuristic.extract",
        lambda text, **kw: [
            EventIn(title="Forum", url="https://t.invalid/e/1", starts_at="2026-09-14T18:00"),
            EventIn(title="No link", url=""),
        ],
    )

    result = PageAdapter(SOURCE).fetch()

    assert all(e.identity.source_url for e in result.events)
    assert len(result.events) == 1
    assert result.errors[0].stage == "parse"          # the linkless one is reported
    assert result.errors[0].retryable is False
