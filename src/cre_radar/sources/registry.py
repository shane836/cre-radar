"""The event-source registry, loaded from the editable `sources.toml`.

Sources are data, not code: adding one is a four-line TOML block, because the
extraction step is generic (see :mod:`cre_radar.extract`).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..config import sources_path

Mode = str  # "html" | "browser" | "rss" | "ical"


@dataclass(frozen=True)
class Source:
    slug: str
    org: str
    url: str
    mode: Mode = "html"
    enabled: bool = True
    # IANA zone the source's wall-clock times are in. Required: a naive local
    # string interpreted as UTC shifts evening events onto the next day.
    timezone: str = "America/Los_Angeles"
    # Precedence when two sources describe the same event. An org's own site
    # outranks an aggregator's write-up of the same panel.
    verification_level: str = "community"
    # Some calendars render every card as plain text with no anchor (USC), or
    # link only a map pin (UCLA Anderson). Those need the linkless reader.
    linkless: bool = False


def load(path: Path | None = None) -> list[Source]:
    """Read the registry. Returns only enabled sources, in file order."""
    target = path or sources_path()
    data = tomllib.loads(target.read_text())
    sources = [Source(**entry) for entry in data.get("source", [])]
    return [source for source in sources if source.enabled]
