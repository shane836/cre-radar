"""iCal and RSS adapter — the zero-extraction path.

Nothing is parsed out of prose: the feed already is structured data. When a
source publishes one, prefer it over `PageAdapter` on reliability. Same contract,
so the runner and persister cannot tell the difference.
"""
from __future__ import annotations

from datetime import UTC, datetime

import feedparser

from ..config import fetch_limit
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
from ..identity import fingerprint
from ..sources.registry import Source
from .timeparse import to_utc

ADAPTER_VERSION = "1.0.0"


class FeedAdapter:
    """Parse an RSS/Atom or iCal feed straight into the contract."""

    def __init__(self, source: Source):
        self.source = source
        self.tier = source.mode              # "rss" | "ical"
        self.id = f"{source.mode}:{source.slug}"
        self.verification_level = source.verification_level

    def fetch(self, cursor: str | None = None) -> FetchResult:
        now = datetime.now(UTC)
        if self.source.mode == "ical":
            # feedparser handles RSS/Atom only — it returns zero entries for a
            # VCALENDAR without erroring, which reads as "quiet source" instead
            # of "unsupported". Fail loudly until a real ics parser is added.
            return FetchResult(errors=[SourceError(
                stage="parse", retryable=False, source=self.id,
                message="ical is not supported yet — use mode = \"rss\" if the "
                        "site offers one (The Events Calendar exposes both)",
                occurred_at=now,
            )], fetched_at=now)
        try:
            feed = feedparser.parse(self.source.url)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(errors=[SourceError(
                stage="fetch", message=f"{type(exc).__name__}: {exc}",
                retryable=True, source=self.id, occurred_at=now,
            )], fetched_at=now)

        events, errors = [], []
        for entry in feed.entries[: fetch_limit()]:
            link, title = entry.get("link"), entry.get("title")
            if not link or not title:
                errors.append(SourceError(
                    stage="parse", message="entry missing link or title",
                    retryable=False, source=self.id, occurred_at=now,
                ))
                continue
            events.append(RawEvent(
                identity=ExternalIdentity(
                    source=self.id, external_id=entry.get("id") or link, source_url=link
                ),
                title=title,
                start_time_utc=to_utc(
                    entry.get("start") or entry.get("published"), self.source.timezone
                ),
                timezone=self.source.timezone,
                venue=VenueCandidate(
                    name=entry.get("location") or self.source.org,
                    timezone=self.source.timezone,
                ),
                description=(entry.get("summary") or "")[:2000] or None,
                verification_level=self.verification_level,
                org=self.source.org,
                fetched_at=now,
            ))
        return FetchResult(events=events, errors=errors, fetched_at=now)

    def normalize(self, raw: RawEvent, provenance: Provenance) -> NormalizedEvent:
        """Pure. Identical to `PageAdapter`'s — the fetch differs, not this."""
        return NormalizedEvent(
            canonical_fingerprint=fingerprint(
                title=raw.title, venue_name=raw.venue.name,
                start_time_utc=raw.start_time_utc, timezone=raw.timezone,
            ),
            identity=raw.identity,
            title=raw.title,
            description=raw.description,
            start_time_utc=raw.start_time_utc,
            end_time_utc=raw.end_time_utc,
            timezone=raw.timezone,
            category=raw.primary_category,
            pricing=raw.pricing or PriceInfo(),
            venue=raw.venue,
            verification_level=raw.verification_level,
            org=raw.org,
            provenance=provenance,
            raw_payload=raw.raw_payload,
        )
