"""The event extractor. No model, no API key — pattern matching and nothing else.

Event listings share a shape: a date, then a linked title, then a time range.
This exploits that and nothing else. Measured against pages verified by hand, it
recovers every real event on well-structured calendars (NAIOP, CSSA) and adds
noise on messier ones, which `scoring.toml` then sorts out. That noise is the
price of having no per-site parser in the repo.

Two failure modes are handled explicitly because they were observed, not
imagined:

* **Stale dates.** A date seen long before a link is not that link's date. Dates
  expire after `_DATE_TTL` lines, so a footer link does not inherit a date from
  the top of the page.
* **Navigation masquerading as events.** "Privacy Policy", "List View", "LA
  events" all match the link shape. They are rejected by title and by URL.
* **Title not linked.** Some calendars link only the card image and render the
  title as plain text beneath it (Bisnow). An empty-label link adopts the first
  substantial line that follows as its title.
* **Date after the title.** Some calendars print the date above the title
  (NAIOP, CSSA); others print it below (BREAA). A link's own following lines are
  checked first, and only then the running date context from above.
* **Empty context.** A title alone gives the scorer almost nothing to judge. The
  lines following a link usually carry the venue, the city and a sentence of
  description, so they are captured as `description` and mined for a location.
* **Undated nav lists.** BREAA's sidebar is thirty links with no dates near them,
  which is indistinguishable from thirty undated events. Since this extractor
  cannot tell them apart, it requires a date. A genuinely undated event is lost
  — a nav menu in the digest is worse than one missed undated event.
"""
from __future__ import annotations

import re
from datetime import date

from .models import EventIn
from .places import find_city

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_BARE_MONTH = re.compile(rf"^({MONTHS})[a-z]*\.?$", re.IGNORECASE)
_BARE_DAY = re.compile(r"^(\d{1,2})$")
_INLINE_DATE = re.compile(
    rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}})\b|\b(\d{{1,2}})\s+({MONTHS})[a-z]*\b", re.IGNORECASE)
_LINK = re.compile(r"^\[([^\]]{6,140})\]\((https?://[^)]+)\)$")
# A card whose only link is its image: `[](url)`, with the title as plain text
# on a following line. Bisnow's whole calendar is built this way.
_BARE_LINK = re.compile(r"^\[\]\((https?://[^)]+)\)$")

# An event title is a name, not a paragraph. Luma renders sponsor and venue
# blurbs in the same shape as events ("The KINN is a membership network,
# accelerator, and collaborative workspace for..."), and only length and
# sentence structure separate them.
_MAX_TITLE = 95
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")
_ONLINE = re.compile(r"\b(online|virtual|zoom|webinar|livestream)\b", re.IGNORECASE)
# "..., Los Angeles, CA 90036" or "Los Angeles, CA"
_CITY_STATE = re.compile(r"([A-Z][A-Za-z .'-]{2,30}),\s*(CA|California)\b")

# How many lines after a link count as that event's context.
_CONTEXT_LINES = 5

# Title keywords -> category, checked in order. First match wins, so the more
# specific kinds are listed before the generic ones. Category drives the pill in
# the digest, and without this every event would read "Other".
# Four kinds, because four is what a reader can scan without a legend. Anything
# unmatched is a `panel` — these calendars are overwhelmingly educational
# sessions, so that is the honest default rather than a shrug.
_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("webinar",    ("webinar", "virtual", "online", "zoom", "livestream",
                    "e-learning", "remote")),
    ("conference", ("conference", "summit", "convention", "expo", "converge",
                    "symposium", "annual meeting", "forum", "congress")),
    ("networking", ("happy hour", "mixer", "reception", "networking", "bbq", "party",
                    "social", "breakfast", "luncheon", "lunch ", "dinner", "meetup",
                    "kick off", "kickoff", "golf", "night", "tour", "site visit",
                    "open house", "gala", "awards", "holiday", "celebration",
                    "anniversary", "tournament", "fundraiser")),
)

DEFAULT_CATEGORY = "panel"

# A date more than this many lines above a link is not that link's date.
_DATE_TTL = 6

# Link text that is chrome, never an event.
_JUNK_TITLES = frozenset({
    "register", "details", "more info", "read more", "learn more", "list view",
    "calendar", "terms of use", "privacy policy", "contact us", "sign up", "log in",
    "login", "home", "about", "donate", "join us", "get involved", "subscribe",
    "switch to list view", "see more pics here", "map", "follow", "view all",
    "all events", "past events", "upcoming events", "membership software",
    # Calendar-export widgets. IREM's page carries sixteen "Google Calendar"
    # links — one per event — and every one matched the link shape.
    "google calendar", "add to calendar", "ical", "ical export", "outlook calendar",
    "outlook", "apple calendar", "yahoo calendar", "download", "export",
    # The CMS vendors that sign their own footers.
    "growthzone", "wild apricot", "memberclicks", "wordpress", "squarespace",
    # Section headings that link to themselves. SSA's page title is one.
    "events & education", "events and education", "events", "education",
    "news and events", "event calendar", "our events",
    # Social share bars sit in the same shape as an event listing.
    "facebook", "twitter", "linkedin", "instagram", "youtube", "x", "threads",
    "share", "get connected", "newsletter", "mailing list",
})

# Titles that are calls to action, not events. Matched as a prefix so
# "Register for ASM 603 here." is caught along with a bare "Register here!".
_JUNK_PREFIXES = ("register ", "register for", "click here", "buy tickets",
                  "get tickets", "rsvp", "sign up for", "apply now", "learn more about")

# URL fragments that mark navigation rather than an event page.
_JUNK_URL = re.compile(
    r"/(privacy|terms|contact|login|signin|register$|about|sitemap|feed|rss"
    r"|membership|sponsor|donate|newsletter)\b", re.IGNORECASE)

# A map pin is where an event is, not the event. UCLA Anderson links its venue
# to Google Maps, and that link sits exactly where a title link would.
_MAP_URL = re.compile(r"(google\.[a-z.]+/maps|maps\.app\.goo\.gl|openstreetmap)",
                      re.IGNORECASE)

# Lines that are card furniture rather than a title, in linkless calendars.
_CARD_NOISE = frozenset({
    "alumni led", "in person", "virtual", "online", "online only", "hybrid",
    "view details", "organizer:", "free", "sold out", "members only",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})


def _looks_like_event(title: str, url: str) -> bool:
    lowered = title.strip().lower().rstrip("!.")
    if lowered in _JUNK_TITLES or len(lowered) < 6:
        return False
    if lowered.endswith(" events") or lowered.startswith("switch to"):
        return False
    if any(lowered.startswith(prefix) for prefix in _JUNK_PREFIXES):
        return False
    if len(title) > _MAX_TITLE or ". " in title:
        return False
    if _MAP_URL.search(url):
        return False
    return not _JUNK_URL.search(url)


def _resolve_year(month: int, day: int, today: date) -> int:
    """Listings omit the year. Pick the nearest future occurrence."""
    candidate = date(today.year, month, min(day, 28))
    return today.year if candidate >= date(today.year, today.month, 1) else today.year + 1


def _title_below(lines: list[str], index: int) -> str | None:
    """The title for an empty-label link, taken from the lines beneath it.

    Skips the card's own metadata — "In Person", "Los Angeles | Office",
    "August 26, 2026 | 8:00 AM PDT" — and takes the first line that reads like a
    name. Stops at the next link, which is the next card.
    """
    for line in lines[index + 1 : index + 1 + _CONTEXT_LINES]:
        if _LINK.match(line) or _BARE_LINK.match(line):
            return None
        if _BARE_MONTH.match(line) or _BARE_DAY.match(line):
            continue
        # Metadata lines: a date, a time, a breadcrumb, or a delivery format.
        if _INLINE_DATE.search(line) or _TIME.search(line) or "|" in line:
            continue
        if line.strip().lower() in ("in person", "virtual", "online", "hybrid"):
            continue
        if len(line) >= 12 and _looks_like_title(line):
            return line.strip()
    return None


def _looks_like_title(line: str) -> bool:
    """Reject prose and chrome; a title is a name, not a sentence."""
    lowered = line.strip().lower().rstrip("!.")
    return (
        lowered not in _JUNK_TITLES
        and not any(lowered.startswith(p) for p in _JUNK_PREFIXES)
        and len(line) <= _MAX_TITLE
        and ". " not in line
    )


def _context(lines: list[str], index: int) -> str:
    """Prose following a link — venue, address, a sentence of description.

    Stops at the next link, because that is the next event. Dates and bare
    times are skipped: they are already captured as structure.
    """
    collected: list[str] = []
    for line in lines[index + 1 : index + 1 + _CONTEXT_LINES]:
        if _LINK.match(line) or _BARE_MONTH.match(line) or _BARE_DAY.match(line):
            break
        if len(line) < 3 or _TIME.fullmatch(line.strip()):
            continue
        collected.append(line)
        if sum(len(c) for c in collected) > 400:
            break
    return " ".join(collected)[:400]


def _category(title: str, context: str) -> str:
    """Best-effort event kind from the title. Deterministic, no model."""
    haystack = f"{title} {context}".lower()
    for name, terms in _CATEGORIES:
        if any(term in haystack for term in terms):
            return name
    return DEFAULT_CATEGORY


def _city(context: str, title: str) -> str | None:
    """Best-effort location from the captured context.

    Geography is the most useful scoring signal there is — an event in Sacramento
    is not an event for someone in Los Angeles — and it is what the digest shows
    the reader. Every listing states it differently, so this defers to the
    gazetteer in :mod:`cre_radar.places` rather than parsing per shape.
    """
    return find_city(context, title)


_CITY_LINE = re.compile(r"^[A-Z][A-Za-z .'-]{2,30},\s*[A-Z]{2}$")


def _linkless_events(
    lines: list[str], *, org: str | None, page_url: str, today: date
) -> list[EventIn]:
    """Extract from calendars whose cards carry no anchor at all.

    USC's alumni calendar renders every card as plain text — even "View Details"
    is not a link — and UCLA Anderson links only the venue's map pin. Requiring
    `[title](url)` finds nothing on either.

    The shape both share is a date block followed by a title line, so that is
    what this reads. Every event gets the listing page as its URL, which the
    contract allows when a source gives an event no link of its own.
    """
    events: list[EventIn] = []
    seen: set[str] = set()
    month = day = None

    for index, line in enumerate(lines):
        if match := _BARE_MONTH.match(line):
            month, day = _MONTH_NUM[match.group(1)[:3].lower()], None
            continue
        if (match := _BARE_DAY.match(line)) and month:
            day = int(match.group(1))
            continue
        # "September 2" on its own line — UCLA Anderson's shape, where the day
        # never appears alone and a weekday name sits between month and date.
        if match := _INLINE_DATE.fullmatch(line.strip()):
            name = (match.group(1) or match.group(4))[:3].lower()
            month, day = _MONTH_NUM[name], int(match.group(2) or match.group(3))
            continue
        if not (month and day):
            continue

        lowered = line.strip().lower().rstrip(":")
        if lowered in _CARD_NOISE or _TIME.fullmatch(line.strip()):
            continue
        if _LINK.match(line) or _BARE_LINK.match(line) or _INLINE_DATE.search(line):
            continue
        if len(line) < 12 or not _looks_like_title(line):
            continue
        # Card metadata that reads like a title: the venue city on its own line,
        # and the sponsoring organisation's own name.
        if _CITY_LINE.match(line.strip()):
            continue
        if org and line.strip().lower() == org.strip().lower():
            continue

        title = line.strip()
        if title.lower() in seen:
            continue
        seen.add(title.lower())

        window = " ".join(lines[index : index + _CONTEXT_LINES])
        clock = _TIME.search(window)
        year = _resolve_year(month, day, today)
        starts_at = f"{year:04d}-{month:02d}-{day:02d}"
        if clock:
            hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "p" else 0)
            starts_at += f"T{hour:02d}:{clock.group(2)}"

        context = " ".join(lines[index + 1 : index + 1 + _CONTEXT_LINES])
        events.append(EventIn(
            title=title, url=page_url, starts_at=starts_at, org=org,
            description=context[:400] or None, city=find_city(context, title),
            category=_category(title, context),
        ))
        month = day = None      # one event per date block
    return events


def extract(
    text: str, *, org: str | None = None, today: date | None = None,
    require_date: bool = True, linkless: bool = False, page_url: str = "",
) -> list[EventIn]:
    """Pull events from one condensed page. Pure: no network, no clock beyond `today`.

    ``require_date`` drops candidates with no nearby date. Leave it on: without a
    date there is no way to separate an event from a navigation link.
    """
    today = today or date.today()
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if linkless:
        return _linkless_events(lines, org=org, page_url=page_url, today=today)

    events: list[EventIn] = []
    seen: set[str] = set()
    month = day = None
    explicit_year = None
    age = 0

    for index, line in enumerate(lines):
        if match := _BARE_MONTH.match(line):
            month, day, age = _MONTH_NUM[match.group(1)[:3].lower()], None, 0
            continue
        if (match := _BARE_DAY.match(line)) and month:
            day, age = int(match.group(1)), 0
            continue
        if (match := _INLINE_DATE.search(line)) and not _LINK.match(line):
            name = (match.group(1) or match.group(4))[:3].lower()
            month = _MONTH_NUM[name]
            day = int(match.group(2) or match.group(3))
            age = 0
            if year := _YEAR.search(line):
                explicit_year = int(year.group(1))

        link = _LINK.match(line)
        bare = None if link else _BARE_LINK.match(line)
        if not link and not bare:
            age += 1
            continue

        if link:
            title, url = link.group(1).strip(), link.group(2)
        else:
            title, url = _title_below(lines, index), bare.group(1)
            if not title:
                age += 1
                continue
        if not _looks_like_event(title, url) or url in seen:
            age += 1
            continue

        # A date on the link's own following lines belongs to this link. Only
        # fall back to the running context from above when there isn't one.
        window = " ".join(lines[index + 1 : index + 4])
        ahead = _INLINE_DATE.search(window)
        if ahead:
            name = (ahead.group(1) or ahead.group(4))[:3].lower()
            use_month, use_day = _MONTH_NUM[name], int(ahead.group(2) or ahead.group(3))
            year_match = _YEAR.search(window)
            use_year = int(year_match.group(1)) if year_match else _resolve_year(
                use_month, use_day, today)
        elif month and day and age <= _DATE_TTL:
            use_month, use_day = month, day
            use_year = explicit_year or _resolve_year(month, day, today)
        else:
            use_month = use_day = use_year = None

        starts_at = None
        if use_month:
            clock = _TIME.search(" ".join(lines[index : index + 4]))
            starts_at = f"{use_year:04d}-{use_month:02d}-{use_day:02d}"
            if clock:
                hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "p" else 0)
                starts_at += f"T{hour:02d}:{clock.group(2)}"

        age += 1
        if require_date and not starts_at:
            continue

        context = _context(lines, index)
        seen.add(url)
        events.append(EventIn(
            title=title, url=url, starts_at=starts_at, org=org,
            description=context or None, city=_city(context, title),
            category=_category(title, context),
        ))

    return events
