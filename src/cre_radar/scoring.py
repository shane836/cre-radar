"""Deterministic relevance scoring. No model, no API, no network.

`scoring.toml` is the entire scorer. An event starts at ``base`` and accumulates
weights for its host, its subject matter, its geography, and its kind. Every rule
that fires is recorded, so the reason line in your digest is a list of what
actually matched rather than a generated sentence — which means when something
scores wrong you can see the rule to change.

Two properties this buys over a model:

* **Reproducible.** The same event always scores the same. A digest you can
  diff is a digest you can trust.
* **Tunable in one place.** Something irrelevant got through? Add the term to
  ``[negative]``. No retraining, no prompt, no re-run of anything upstream.
"""
from __future__ import annotations

import sqlite3
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import scoring_path
from .models import Verdict

# A single term can't dominate; without a cap a title repeating "self storage"
# three ways would swamp every other signal.
POSITIVE_CAP = 60
NEGATIVE_CAP = -110


@dataclass
class Rules:
    base: int = 45
    org: dict[str, int] = field(default_factory=dict)
    positive: dict[str, int] = field(default_factory=dict)
    negative: dict[str, int] = field(default_factory=dict)
    geography: dict[str, int] = field(default_factory=dict)
    category: dict[str, int] = field(default_factory=dict)
    socal_cities: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> Rules:
    """Read scoring.toml once per process."""
    target = Path(path) if path else scoring_path()
    data = tomllib.loads(target.read_text())
    geography = dict(data.get("geography", {}))
    cities = tuple(geography.pop("socal_cities", []) or [])
    return Rules(
        base=int(data.get("base", 45)),
        org={k.lower(): int(v) for k, v in data.get("org", {}).items()},
        positive={k.lower(): int(v) for k, v in data.get("positive", {}).items()},
        negative={k.lower(): int(v) for k, v in data.get("negative", {}).items()},
        geography={k: int(v) for k, v in geography.items()},
        category={k: int(v) for k, v in data.get("category", {}).items()},
        socal_cities=tuple(c.lower() for c in cities),
    )


def _geography(
    city: str | None, venue: str | None, org: str | None, rules: Rules
) -> tuple[int, str]:
    """Where it is. Unknown scores slightly negative — an event with no stated
    location is usually a listing artefact, not a real one.

    The org is excluded deliberately. Adapters fall back to the org name when a
    source gives no venue, and org names carry place names: "California Self
    Storage Association" was scoring a NorCal holiday party as in-market, and
    "IREM Greater Los Angeles" did the same for everything it hosts.
    """
    if venue and org and venue.strip().lower() == org.strip().lower():
        venue = None
    where = " ".join(part.lower() for part in (city, venue) if part).strip()
    if not where:
        return rules.geography.get("unknown", 0), "location unknown"
    if "online" in where or "virtual" in where or "zoom" in where or "webinar" in where:
        return rules.geography.get("online", 0), "online"
    for candidate in rules.socal_cities:
        if candidate in where:
            return rules.geography.get("socal", 0), f"in {candidate}"
    return rules.geography.get("elsewhere", 0), f"outside SoCal ({where[:40]})"


def _terms(haystack: str, table: dict[str, int], cap: int) -> tuple[int, list[str]]:
    """Sum every matching term, capped. Returns (points, what matched)."""
    total, hits = 0, []
    for term, weight in table.items():
        if term in haystack:
            total += weight
            hits.append(f"{term} {weight:+d}")
    if cap >= 0:
        total = min(total, cap)
    else:
        total = max(total, cap)
    return total, hits


def score_event(
    *, title: str, description: str | None = None, org: str | None = None,
    city: str | None = None, venue: str | None = None, category: str | None = None,
    rules: Rules | None = None,
) -> Verdict:
    """Score one event. Pure: same inputs always produce the same verdict."""
    rules = rules or load_rules()
    haystack = " ".join(p.lower() for p in (title, description or "") if p)

    points = rules.base
    reasons: list[str] = []

    if org and (weight := rules.org.get(org.lower())):
        points += weight
        reasons.append(f"{org} {weight:+d}")

    positive, hits = _terms(haystack, rules.positive, POSITIVE_CAP)
    points += positive
    reasons.extend(hits)

    negative, hits = _terms(haystack, rules.negative, NEGATIVE_CAP)
    points += negative
    reasons.extend(hits)

    geo_points, geo_label = _geography(city, venue, org, rules)
    points += geo_points
    reasons.append(f"{geo_label} {geo_points:+d}")

    if category and (weight := rules.category.get(category)):
        points += weight
        reasons.append(f"{category} {weight:+d}")

    topics = [term for term in rules.positive if term in haystack][:4]

    return Verdict(
        score=max(0, min(100, points)),
        reason="; ".join(reasons) if reasons else "no rules matched",
        topics=topics,
    )


def score_row(row: sqlite3.Row, rules: Rules | None = None) -> Verdict:
    """Score a database row."""
    return score_event(
        title=row["title"], description=row["description"], org=row["org"],
        city=row["city"], venue=row["venue"], category=row["category"], rules=rules,
    )
