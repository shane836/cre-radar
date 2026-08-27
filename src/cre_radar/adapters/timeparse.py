"""Turning a source's date string into an aware UTC datetime.

Shared by every adapter so the interpretation is identical everywhere. The rule
that matters: a wall-clock string from a source is **local to that source's
timezone**, never UTC. Getting this backwards shifts evening events onto the
next day — see `identity.format_local_minute`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Accepted shapes, in the order the extraction prompt is told to emit them.
_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def to_utc(value: str | None, source_timezone: str) -> datetime | None:
    """Parse a local wall-clock string and return it as aware UTC.

    Returns None for anything unparseable rather than guessing — a wrong date is
    worse than a missing one, because a wrong date silently sorts the event into
    the wrong day of the digest.
    """
    if not value:
        return None
    text = value.strip().replace("Z", "")
    if "+" in text[10:]:
        text = text[: 10 + text[10:].index("+")]

    for fmt in _FORMATS:
        try:
            # Deliberately naive: the string IS local wall-clock, and the
            # very next line attaches the source timezone. noqa: the rule cannot
            # see one line ahead.
            naive = datetime.strptime(text, fmt)  # noqa: DTZ007
        except ValueError:
            continue
        return naive.replace(tzinfo=ZoneInfo(source_timezone)).astimezone(UTC)
    return None
