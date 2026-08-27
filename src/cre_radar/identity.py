"""Identity helpers. Pure, synchronous, no IO.

Ported from `sf-events-aggregator/lib/identity.ts` and `docs/IDENTITY.md`. Every
adapter funnels through these — do not reimplement normalization or
fingerprinting anywhere else.

Three layers of identity:

===============  ==========================================  ==================
Layer            Key                                          Used by
===============  ==========================================  ==================
Source           ``(source, external_id)``                    adapter: seen before?
Canonical event  ``sha256(title|venue|local_minute)[:32]``    persister: cross-source dedupe
===============  ==========================================  ==================

sf-events also carries a venue surrogate; cre-radar has far fewer venues and no
venue table, so the normalized venue name serves directly.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

_WHITESPACE = re.compile(r"\s+")
_COMBINING = re.compile(r"[̀-ͯ]")


def normalize_title(title: str) -> str:
    """Soft title normalization: lowercase, strip diacritics, collapse whitespace.

    Punctuation is deliberately NOT stripped. "K.Flay" and "K Flay" stay distinct.
    The governing rule from docs/IDENTITY.md: **false splits are recoverable,
    false merges are not.** An under-merge shows you the same event twice; an
    over-merge silently deletes one.
    """
    decomposed = unicodedata.normalize("NFKD", title.lower())
    stripped = _COMBINING.sub("", decomposed)
    return _WHITESPACE.sub(" ", stripped).strip()


def normalize_venue_name(name: str) -> str:
    """Venue names share the title rules."""
    return normalize_title(name)


def format_local_minute(moment: datetime, timezone: str) -> str:
    """``YYYY-MM-DDTHH:MM`` in the given IANA zone.

    Never derive a local date by slicing a UTC ISO string: an 18:00 PT event on
    14 Sept is 15 Sept in UTC, so slicing moves it a day and breaks both the
    fingerprint and calendar grouping.
    """
    return moment.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%dT%H:%M")


def format_local_date(moment: datetime, timezone: str) -> str:
    """Local calendar date, ``YYYY-MM-DD``. Use this for day grouping."""
    return format_local_minute(moment, timezone)[:10]


def fingerprint(
    *, title: str, venue_name: str, start_time_utc: datetime | None, timezone: str
) -> str:
    """Canonical event fingerprint — ``sha256(title|venue|local_minute)[:32]``.

    Two adapters that saw the same event produce the same fingerprint with no
    shared state, which is what makes cross-source dedupe possible: Bisnow,
    Connect CRE and NAIOP all listing one panel collapse to a single row.

    Minute granularity is deliberate — a morning and an evening session of the
    same program at the same venue are different events.

    An event with no usable date fingerprints on title + venue alone. Those
    under-merge rather than over-merge, per the rule in :func:`normalize_title`.
    """
    local_minute = (
        format_local_minute(start_time_utc, timezone) if start_time_utc else "no-date"
    )
    key = "|".join(
        (normalize_title(title), normalize_venue_name(venue_name), local_minute)
    )
    return hashlib.sha256(key.encode()).hexdigest()[:32]
