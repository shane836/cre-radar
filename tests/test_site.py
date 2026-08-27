"""The public page carries the only contact details on the internet that point
at Shane from this project, so the tests here are mostly about what must *not*
appear: a plaintext address, a dead link, or a second mailing list."""
from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime

import pytest

from cre_radar import site


@pytest.fixture()
def page(monkeypatch):
    """Render with both optional links populated, so the filled-in path is
    covered even while the real constants are still empty."""
    monkeypatch.setattr(site, "LINKEDIN_URL", "https://www.linkedin.com/in/example/")
    monkeypatch.setattr(site, "CONTACT_EMAIL", "radar@example.com")
    return site.render(
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC), source_count=20
    )


# --- The firm's mark ---------------------------------------------------------

def test_logo_links_to_the_firm_and_opens_safely(page):
    assert 'class="logo" href="https://masonequitypartners.com"' in page
    assert 'rel="noopener"' in page


def test_logo_has_an_accessible_name(page):
    """An image link whose only content is a picture needs a name, or a screen
    reader announces the URL."""
    assert 'alt="Mason Equity Partners"' in page
    assert 'aria-label="Mason Equity Partners"' in page


def test_both_themes_get_their_own_mark(page):
    """The mark is one flat colour. Ship only the navy one and it disappears
    into a dark background."""
    assert 'media="(prefers-color-scheme: dark)"' in page
    uris = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", page)
    assert len(uris) == 2
    assert uris[0] != uris[1]


def test_logo_is_inlined_not_linked(page):
    """A <img src="assets/..."> would 404: only `public/` is deployed. Inlining
    also keeps the page's zero-external-requests property."""
    assert "assets/mep-mark" not in page
    assert 'src="data:image/png;base64,' in page


def test_logo_reserves_its_space(page):
    """Without intrinsic dimensions the nav reflows as the data URI decodes."""
    assert 'width="131" height="84"' in page


# --- Nav and drawers --------------------------------------------------------

def test_nav_carries_both_entry_points(page):
    assert 'data-drawer="aboutDrawer"' in page
    assert 'data-drawer="subscribeDrawer"' in page
    assert ">About</button>" in page
    assert ">Subscribe</button>" in page


def test_toggles_declare_what_they_control(page):
    """Screen readers get nothing from a bare <button> that reveals a panel."""
    for name in ("about", "subscribe"):
        assert f'aria-controls="{name}Drawer"' in page
    assert page.count('aria-expanded="false"') == 2


def test_drawers_are_rendered_open_for_readers_without_js(page):
    """The panels ship expanded and the script collapses them. Ship them
    collapsed instead and a reader with JS disabled gets two dead buttons and
    no way to reach the contact details at all."""
    assert page.count('<div class="drawer"') == 2
    assert 'class="drawer open"' not in page          # no hardcoded state
    assert ".js .drawer{max-height:0" in page          # collapsed only under .js
    assert "document.documentElement.className='js'" in page


def test_closed_drawer_is_taken_out_of_the_tab_order(page):
    """`max-height:0` alone hides a drawer visually while leaving its links
    focusable, which strands a keyboard user on an invisible LinkedIn link."""
    collapsed = page.split(".js .drawer{")[1].split("}")[0]
    assert "visibility:hidden" in collapsed


def test_about_says_who_built_it_and_where_he_works(page):
    assert "Shane Mason" in page
    assert "principal" in page
    assert "Mason Equity Partners" in page
    assert "masonequitypartners.com" in page


def test_source_count_comes_from_the_registry_not_the_copy(page):
    """A number typed into prose goes stale the first time a source is added."""
    assert "20 Los Angeles" in page


# --- The address must never be scrapeable -----------------------------------

def test_email_never_appears_in_the_page_source(page):
    """The whole point of the encoding. If this fails, harvesters win."""
    assert "radar@example.com" not in page
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", page) is None


def test_token_round_trips_the_way_the_browser_decodes_it(page):
    """Mirrors the JS exactly: atob, split, reverse, join."""
    token = re.search(r'data-a="([^"]+)"', page).group(1)
    assert base64.b64decode(token).decode()[::-1] == "radar@example.com"


def test_no_js_fallback_is_readable_but_not_a_regex_match(page):
    assert "radar at example dot com" in page


def test_jsonld_carries_attribution_but_never_the_address(page):
    raw = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL
    ).group(1)
    data = json.loads(raw)

    assert data["author"]["name"] == "Shane Mason"
    assert data["author"]["affiliation"]["name"] == "Mason Equity Partners"
    assert data["author"]["sameAs"] == ["https://www.linkedin.com/in/example/"]
    assert "example.com" not in raw.replace("masonequitypartners.com", "")


# --- Unset constants must omit, never emit a dead link -----------------------

def test_empty_constants_render_no_link_at_all(monkeypatch):
    """A constant that is not filled in yet must omit its link, not ship
    `href=""`. Set explicitly rather than leaning on the real defaults, so this
    keeps testing the omission path after the constants get real values."""
    monkeypatch.setattr(site, "LINKEDIN_URL", "")
    monkeypatch.setattr(site, "CONTACT_EMAIL", "")
    page = site.render(
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC), source_count=20
    )

    assert 'href=""' not in page
    assert "data-a=" not in page
    assert "linkedin" not in page.lower()
    assert json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
        .group(1)
    )["author"].get("sameAs") is None


# --- One list, tagged by source ---------------------------------------------

def test_subscribe_points_at_the_one_beehiiv_audience_and_is_tagged(page):
    """`Website - Spec Contact & Insights` in the vault: never a second list."""
    assert "masonequitypartners.beehiiv.com/subscribe" in page
    assert "utm_medium=cre-radar" in page


def test_drawer_bodies_are_not_left_in_the_page_flow(page):
    """They live under the nav now. A stray copy at the bottom would show the
    same text twice for anyone without JS."""
    assert page.count("Learn how we think") == 1
    assert page.count("Built by") == 1


def test_subscribe_uses_the_sites_own_copy(page):
    assert "Learn how we think" in page
    assert "notes from the field" in page
    assert "No spam. Unsubscribe anytime." in page


def test_button_until_an_embed_is_configured(page):
    """No embed set means no iframe, which means the page still loads without
    making a single external request."""
    assert "<iframe" not in page
    assert "Sign me up" in page


def test_embed_replaces_the_button_when_configured(monkeypatch):
    monkeypatch.setattr(site, "BEEHIIV_EMBED_URL", "https://embeds.beehiiv.com/abc")
    page = site.render(
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC), source_count=20
    )

    assert '<iframe class="embed" src="https://embeds.beehiiv.com/abc"' in page
    assert 'title="Subscribe to the newsletter"' in page   # the a11y name
    assert "Sign me up" not in page
