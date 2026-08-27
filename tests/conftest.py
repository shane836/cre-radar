"""Shared fixtures: an in-memory DB and a builder for contract objects."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cre_radar import db as db_mod
from cre_radar.contracts import (
    ExternalIdentity,
    NormalizedEvent,
    PriceInfo,
    Provenance,
    VenueCandidate,
)
from cre_radar.identity import fingerprint

LA = "America/Los_Angeles"
WHEN = datetime(2026, 9, 15, 1, 0, tzinfo=UTC)          # 18:00 PT, 14 Sept
STAMP = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture()
def conn():
    connection = db_mod.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture()
def provenance():
    return Provenance(
        adapter_id="test", adapter_version="1.0.0",
        pipeline_version="1.0.0", normalized_at=STAMP,
    )


def make_event(
    *, source: str, level: str, title: str = "Capital Markets Forum",
    venue: str = "JW Marriott", url: str | None = None, when=WHEN,
    city: str | None = "Los Angeles", price: str | None = None,
    description: str | None = None, provenance: Provenance | None = None,
) -> NormalizedEvent:
    """Build a NormalizedEvent as an adapter would. Same title+venue+time across
    sources means the same fingerprint — which is exactly what dedupe relies on."""
    return NormalizedEvent(
        canonical_fingerprint=fingerprint(
            title=title, venue_name=venue, start_time_utc=when, timezone=LA
        ),
        identity=ExternalIdentity(
            source=source,
            external_id=url or f"https://{source}.test/e/1",
            source_url=url or f"https://{source}.test/e/1",
        ),
        title=title,
        description=description,
        start_time_utc=when,
        end_time_utc=None,
        timezone=LA,
        category="panel",
        pricing=PriceInfo(display=price),
        venue=VenueCandidate(name=venue, city=city, timezone=LA),
        verification_level=level,
        org="Test Org",
        provenance=provenance or Provenance(
            adapter_id=source, adapter_version="1.0.0",
            pipeline_version="1.0.0", normalized_at=STAMP,
        ),
    )
