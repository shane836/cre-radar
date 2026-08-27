"""Finding the city an event is in, from whatever text a listing gives us.

Every calendar states location differently — Bisnow writes "Los Angeles |
Office", Eventbrite writes "Long Beach · 3229 E Spring St" or "Downtown · Los
Angeles", others write a full postal address or nothing at all. Rather than a
parser per shape, this matches against a gazetteer of the places an LA reader
cares about, longest name first so "West Hollywood" wins over "Hollywood".

This list is for *extraction* — recognising a place when it appears. The
`socal_cities` list in `scoring.toml` is for *scoring* — deciding what counts as
in-market. They overlap, and they are deliberately separate: one is a fact about
the page, the other is a preference you tune.
"""
from __future__ import annotations

import re

# LA county and the adjacent markets, plus neighbourhood names that listings use
# as if they were cities.
LA_AREA = (
    "Downtown Los Angeles", "West Hollywood", "North Hollywood", "Universal City",
    "Beverly Hills", "Santa Monica", "Culver City", "Century City", "Marina del Rey",
    "Playa Vista", "El Segundo", "Manhattan Beach", "Hermosa Beach", "Redondo Beach",
    "Long Beach", "San Pedro", "Torrance", "Carson", "Inglewood", "Hawthorne",
    "Gardena", "Compton", "Pasadena", "South Pasadena", "Glendale", "Burbank",
    "Sherman Oaks", "Studio City", "Woodland Hills", "Encino", "Van Nuys",
    "Northridge", "Calabasas", "Agoura Hills", "Thousand Oaks", "Santa Clarita",
    "Alhambra", "Monterey Park", "Arcadia", "Monrovia", "El Monte", "Whittier",
    "Downey", "Norwalk", "Cerritos", "Commerce", "Vernon", "Industry",
    "Hollywood", "Koreatown", "Silver Lake", "Echo Park", "Highland Park",
    "Venice", "Brentwood", "Westwood", "Los Feliz", "Pomona", "Ontario",
    "Los Angeles", "Downtown",
)

# In-market but outside LA county.
SOCAL_OTHER = (
    "Newport Beach", "Costa Mesa", "Huntington Beach", "Orange County", "Irvine",
    "Anaheim", "Santa Ana", "Fullerton", "Riverside", "San Bernardino", "Corona",
    "Temecula", "Oceanside", "Carlsbad", "San Diego", "Ventura", "Oxnard",
    "Santa Barbara", "Palm Springs", "Bakersfield",
)

# Out of market, recognised so they can be scored down rather than left unknown.
ELSEWHERE = (
    "San Francisco", "Oakland", "San Jose", "Sacramento", "Palo Alto", "Berkeley",
    "New York", "Brooklyn", "Manhattan", "Chicago", "Dallas", "Houston", "Austin",
    "Atlanta", "Miami", "Boston", "Seattle", "Portland", "Denver", "Phoenix",
    "Las Vegas", "Washington", "Nashville", "Dubai", "London",
)

_ALL = tuple(sorted(LA_AREA + SOCAL_OTHER + ELSEWHERE, key=len, reverse=True))

# Longest-first alternation, whole-word, so "Hollywood" cannot shadow
# "West Hollywood" and "Ontario" cannot match inside "Ontarion".
_GAZETTEER = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in _ALL) + r")\b", re.IGNORECASE
)

_ONLINE = re.compile(
    r"\b(online|virtual|zoom|webinar|livestream|remote)\b", re.IGNORECASE
)

# "Los Angeles, CA 90036" / "Austin, TX" — catches places not in the gazetteer.
_CITY_STATE = re.compile(r"([A-Z][A-Za-z .'-]{2,30}),\s*([A-Z]{2})\b")

# Canonical spelling, so "downtown la" and "DTLA" group with "Los Angeles".
_CANONICAL = {
    "downtown": "Los Angeles",
    "downtown los angeles": "Los Angeles",
    "dtla": "Los Angeles",
}


def find_city(*texts: str | None) -> str | None:
    """The city named in any of ``texts``, or None.

    Online beats a physical name: a webinar that mentions Los Angeles in its
    blurb is still online, and that is what a reader needs to know.
    """
    haystack = " ".join(t for t in texts if t)
    if not haystack:
        return None

    if _ONLINE.search(haystack):
        return "Online"

    if match := _GAZETTEER.search(haystack):
        name = match.group(1)
        return _CANONICAL.get(name.lower(), name.title() if name.islower() else name)

    if match := _CITY_STATE.search(haystack):
        city, state = match.group(1).strip(), match.group(2)
        return city if state == "CA" else f"{city}, {state}"

    return None
