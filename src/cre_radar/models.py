"""The typed contracts every source and both pipelines share."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EventCategory = Literal["panel", "networking", "webinar", "conference"]


class EventIn(BaseModel):
    """An event as extracted from a source page, before storage."""

    title: str
    url: str
    starts_at: str | None = Field(
        default=None, description="ISO 8601 date or datetime, e.g. 2026-09-14 or 2026-09-14T18:00"
    )
    ends_at: str | None = None
    venue: str | None = None
    city: str | None = None
    org: str | None = None
    price: str | None = None
    category: EventCategory = "panel"
    description: str | None = None


class ExtractedEvents(BaseModel):
    """Wrapper the extraction call returns — a list plus what it could not parse."""

    events: list[EventIn] = Field(default_factory=list)


class Verdict(BaseModel):
    """The scorer's judgment on one item, against the interest profile."""

    score: int = Field(ge=0, le=100, description="0 = ignore, 100 = drop everything and read")
    reason: str = Field(description="One sentence. Why it does or does not matter to Shane.")
    topics: list[str] = Field(default_factory=list, description="2-4 short topic tags")


class IndexedVerdict(Verdict):
    """A verdict that knows which item in the batch it belongs to."""

    index: int = Field(description="0-based position of the item in the batch")


class ScoredBatch(BaseModel):
    """Verdicts for a batch, aligned to the input list by ``index``."""

    verdicts: list[IndexedVerdict] = Field(default_factory=list)
