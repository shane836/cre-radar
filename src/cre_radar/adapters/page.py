"""The page adapter — fetch, condense, and turn text into events.

Extraction is :mod:`cre_radar.heuristic` — pattern matching over the condensed
page. No model, no API key, no network beyond the fetch itself, so a cron run
costs nothing and behaves identically every time.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .. import heuristic
from ..contracts import (
    ExternalIdentity,
    FetchResult,
    NormalizedEvent,
    PriceInfo,
    Provenance,
    RawEvent,
    SourceError,
    VenueCandidate,
)
from ..fetch import condense, fetch_rendered, fetch_static, link_fingerprint
from ..identity import fingerprint
from ..sources.registry import Source
from .timeparse import to_utc

ADAPTER_VERSION = "2.0.0"
PIPELINE_VERSION = "2.0.0"


class PageAdapter:
    """Fetch a page and produce contract events from it."""

    tier = "page"

    def __init__(self, source: Source, *, known_fingerprint: str | None = None):
        self.source = source
        self.id = f"page:{source.slug}"
        self.verification_level = source.verification_level
        self._known_fingerprint = known_fingerprint
        self.page_fingerprint: str | None = None
        self.page_text: str | None = None

    def fetch(self, cursor: str | None = None) -> FetchResult:
        now = datetime.now(UTC)
        try:
            html = (
                fetch_rendered(self.source.url)
                if self.source.mode == "browser"
                else fetch_static(self.source.url)
            )
        except Exception as exc:  # noqa: BLE001 — transport failures are data
            return FetchResult(errors=[SourceError(
                stage="fetch", message=f"{type(exc).__name__}: {exc}",
                retryable=True, source=self.id, occurred_at=now,
            )], fetched_at=now)

        text, truncated = condense(html, self.source.url)
        self.page_text = text
        self.page_fingerprint = link_fingerprint(text)
        if self._known_fingerprint and self.page_fingerprint == self._known_fingerprint:
            return FetchResult(fetched_at=now, unchanged=True)

        extracted = heuristic.extract(
            text, org=self.source.org,
            linkless=self.source.linkless, page_url=self.source.url,
        )

        events, errors = [], []
        for item in extracted:
            if not item.url:
                errors.append(SourceError(
                    stage="parse", message=f"event {item.title!r} has no source_url",
                    retryable=False, source=self.id, occurred_at=now,
                ))
                continue
            events.append(RawEvent(
                identity=ExternalIdentity(
                    source=self.id, external_id=item.url, source_url=item.url
                ),
                title=item.title,
                start_time_utc=to_utc(item.starts_at, self.source.timezone),
                end_time_utc=to_utc(item.ends_at, self.source.timezone),
                timezone=self.source.timezone,
                venue=VenueCandidate(
                    name=item.venue or item.city or self.source.org,
                    city=item.city, timezone=self.source.timezone,
                ),
                description=item.description,
                primary_category=item.category,
                pricing=PriceInfo(display=item.price),
                verification_level=self.verification_level,
                org=item.org or self.source.org,
                fetched_at=now,
            ))

        return FetchResult(events=events, errors=errors, fetched_at=now, partial=truncated)

    def normalize(self, raw: RawEvent, provenance: Provenance) -> NormalizedEvent:
        """Pure. Same RawEvent + same Provenance always yields the same output."""
        return NormalizedEvent(
            canonical_fingerprint=fingerprint(
                title=raw.title, venue_name=raw.venue.name,
                start_time_utc=raw.start_time_utc, timezone=raw.timezone,
            ),
            identity=raw.identity, title=raw.title, description=raw.description,
            start_time_utc=raw.start_time_utc, end_time_utc=raw.end_time_utc,
            timezone=raw.timezone, category=raw.primary_category,
            pricing=raw.pricing or PriceInfo(), venue=raw.venue,
            verification_level=raw.verification_level, org=raw.org,
            provenance=provenance, raw_payload=raw.raw_payload,
        )


def build_adapter(source: Source, *, known_fingerprint: str | None = None):
    """Pick the adapter for a source's mode. The registry stays declarative."""
    from .feed import FeedAdapter

    if source.mode in ("rss", "ical"):
        return FeedAdapter(source)
    return PageAdapter(source, known_fingerprint=known_fingerprint)
