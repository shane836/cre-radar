"""Getting a page, and turning it into something the extractor can read.

Two fetch modes — plain HTTP, and a real headless browser for the sites that
either need JavaScript or reject non-browser clients (Bisnow, ICSC, ULI, Connect
CRE, Eventbrite, Luma all do one or the other).

:func:`condense` is the piece that makes generic extraction work: it throws away
chrome and markup but *keeps anchor hrefs inline*, so every extracted event
carries a real ticket/registration URL rather than a guess at one.
"""
from __future__ import annotations

import hashlib
import re

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Chrome that never contains an event and only adds noise to the extraction.
#
# `form` is deliberately NOT here: ASP.NET WebForms wraps the entire page body in
# a single <form runat="server">, so stripping it deletes the whole page. That bug
# silently reduced selfstorage.org from 243KB to just its <title>.
_STRIP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header")

# Generous — no real event calendar condenses to anywhere near this. Pages
# larger than this are truncated, and the caller reports it rather than silently
# losing events off the end.
MAX_CHARS = 200_000


# Anchors as `condense` writes them: [label](url)
_LINK = re.compile(r"\]\((https?://[^)]+)\)")


def link_fingerprint(condensed: str) -> str:
    """A hash of the page's *link set*, used to detect a genuinely changed calendar.

    Hashing the full text does not work: several of these pages print today's date
    or a session id, so the text differs every morning while the calendar is
    identical, and the cache would never hit. The set of linked URLs is stable
    across those, and a new or removed event always changes it.

    The trade-off is that an edit which changes only an existing event's details —
    time moved, venue swapped — leaves the URL set untouched and will not trigger
    re-extraction. `collect --force` overrides, and a weekly forced run picks up
    that class of change.
    """
    from .db import normalize_url

    # Normalize first: BOMA's calendar appends ?sourceTypeId=... to every link,
    # which changed the raw fingerprint on every run and defeated the cache.
    urls = sorted({normalize_url(u) for u in _LINK.findall(condensed)})
    return hashlib.sha256("\n".join(urls).encode()).hexdigest()


class Truncated(UserWarning):
    """Raised into the run log when a page exceeded MAX_CHARS."""


def fetch_static(url: str, *, timeout: float = 30.0) -> str:
    """Plain HTTP GET with a browser User-Agent. Raises on 4xx/5xx."""
    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
    )
    response.raise_for_status()
    return response.text


def fetch_rendered(url: str, *, timeout: float = 45.0, wait_ms: int = 2500) -> str:
    """Render the page in headless Chromium and return the resulting DOM.

    Needed for JS-built calendars and for hosts that 403 a bare HTTP client.
    Requires ``uv run playwright install chromium``.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA, locale="en-US")
            page.goto(url, timeout=int(timeout * 1000), wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            return page.content()
        finally:
            browser.close()


def condense(html: str, base_url: str) -> tuple[str, bool]:
    """Reduce a page to link-annotated plain text. Returns (text, was_truncated).

    Anchors survive as ``[label](absolute-url)`` because the URL is the one field
    that cannot be reconstructed from the text — every event needs a working link
    back to its source.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            anchor.replace_with(label)
            continue
        anchor.replace_with(f"[{label}]({httpx.URL(base_url).join(href)})")

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS], True
    return text, False
