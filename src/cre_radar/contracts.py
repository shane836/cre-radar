"""Source adapter contract — the frozen interface between adapters and the persister.

Ported from `sf-events-aggregator/lib/sources/types.ts`. All cross-module data
flows through these types.

Invariants this contract enforces:

* ``fetch()`` is the only IO entrypoint on an adapter.
* ``normalize()`` is pure and synchronous — no network, no DB, no clock read
  other than the ``Provenance`` handed in by the caller.
* Adapters never import each other and never write to the database.
* Identity is derived from title + venue + local time, never from a surrogate id.
* ``source_url`` is REQUIRED on every event. An event you cannot click through to
  is not an event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

EventCategory = Literal["panel", "networking", "webinar", "conference"]

# How the adapter got the data. Drives expectations about reliability, not trust.
SourceTier = Literal["api", "ical", "rss", "llm"]

# How much the data is to be trusted when two sources describe the same event.
# Ordering matters: see `rank` below.
VerificationLevel = Literal[
    "official",         # the hosting organization's own site (NAIOP, ULI, CSSA)
    "trusted_partner",  # the org's own listing on a ticketing platform (Eventbrite, Luma)
    "community",        # editorial aggregators (Bisnow, Connect CRE)
    "unverified",       # anything else
]

_LEVEL_RANK: dict[str, int] = {
    "official": 3, "trusted_partner": 2, "community": 1, "unverified": 0,
}


def rank(level: VerificationLevel) -> int:
    """Precedence for the winner rule. Higher wins a fingerprint conflict."""
    return _LEVEL_RANK[level]


@dataclass(frozen=True)
class ExternalIdentity:
    """Layer 1 identity: stable per source. Answers "have I seen this row before"."""

    source: str          # 'llm:naiop-socal' | 'ical:usc-lusk'
    external_id: str     # stable per source; the source URL when nothing better exists
    source_url: str      # click-through. REQUIRED on every event.
    source_version: str | None = None


@dataclass(frozen=True)
class VenueCandidate:
    """Where it happens. ``name`` feeds the fingerprint, so it must be present.

    Use ``"Online"`` for webinars — it makes two orgs' virtual events on the same
    topic at the same minute collapse, which is correct.
    """

    name: str
    city: str | None = None
    address: str | None = None
    timezone: str | None = None


@dataclass(frozen=True)
class PriceInfo:
    price_min: float | None = None
    price_max: float | None = None
    is_free: bool | None = None
    display: str | None = None   # free text as printed: "$95 members / $150 guests"


@dataclass(frozen=True)
class RawEvent:
    """What an adapter's ``fetch()`` produces. Pre-identity, pre-normalization."""

    identity: ExternalIdentity
    title: str
    start_time_utc: datetime | None    # None when the source gives no usable date
    timezone: str                      # IANA. Required for a stable fingerprint.
    venue: VenueCandidate
    fetched_at: datetime
    end_time_utc: datetime | None = None
    description: str | None = None
    primary_category: EventCategory = "panel"
    pricing: PriceInfo | None = None
    verification_level: VerificationLevel = "unverified"
    org: str | None = None
    raw_payload: Any = None


ErrorStage = Literal["fetch", "parse", "normalize", "persist"]


@dataclass(frozen=True)
class SourceError:
    """A structured failure. Never a bare string — the runner reports on `stage`."""

    stage: ErrorStage
    message: str
    retryable: bool
    source: str | None = None
    external_id: str | None = None
    http_status: int | None = None
    occurred_at: datetime | None = None


@dataclass
class FetchResult:
    """One adapter pass. Partial success is first class: events AND errors."""

    events: list[RawEvent] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)
    fetched_at: datetime | None = None
    partial: bool = False           # adapter knows it returned incomplete results
    next_cursor: str | None = None
    unchanged: bool = False         # link fingerprint matched; nothing re-extracted


@dataclass(frozen=True)
class Provenance:
    """Passed in by the caller so ``normalize()`` stays pure — it reads no clock."""

    adapter_id: str
    adapter_version: str
    pipeline_version: str
    normalized_at: datetime


@dataclass(frozen=True)
class SecondarySource:
    """A losing source in a fingerprint merge. Kept so attribution is not lost."""

    source: str
    external_id: str
    source_url: str
    verification_level: VerificationLevel
    observed_at: datetime


@dataclass(frozen=True)
class NormalizedEvent:
    """Persistence-ready. Deliberately decoupled from the DB row shape so the
    contract can evolve faster than the schema."""

    canonical_fingerprint: str
    identity: ExternalIdentity
    title: str
    description: str | None
    start_time_utc: datetime | None
    end_time_utc: datetime | None
    timezone: str
    category: EventCategory
    pricing: PriceInfo
    venue: VenueCandidate
    verification_level: VerificationLevel
    org: str | None
    provenance: Provenance
    raw_payload: Any = None


@runtime_checkable
class SourceAdapter(Protocol):
    """Every stream — LLM extraction, iCal, RSS, a future API — implements this."""

    id: str
    tier: SourceTier
    verification_level: VerificationLevel

    def fetch(self, cursor: str | None = None) -> FetchResult:
        """The only IO method. Must not raise: failures come back as SourceError."""
        ...

    def normalize(self, raw: RawEvent, provenance: Provenance) -> NormalizedEvent:
        """MUST be pure and synchronous. Same input + provenance → same output."""
        ...
