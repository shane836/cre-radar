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
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
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
    assert "Get Events in your Inbox</button>" in page


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


def test_about_is_only_the_two_lines_that_matter(page):
    """Who built it and how to tell him it is wrong. What the tool does is the
    listing underneath, which says it better than a sentence of prose."""
    assert "Every morning this sweeps" not in page
    assert "event calendars" not in page
    drawer = page.split('id="aboutDrawer"')[1].split("</div></div>")[0]
    # `<p[ >]`, not `<p` — the mail icon's SVG is full of `<path`.
    assert len(re.findall(r"<p[ >]", drawer)) == 3   # by-line, bug reports, links


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


def test_no_js_fallback_lives_in_noscript(page):
    """The link's visible content is an icon once the script runs. The spelled
    address has to stay reachable for the reader who never gets that far."""
    assert "<noscript>radar at example dot com</noscript>" in page


def test_icon_only_links_carry_an_accessible_name(page):
    """An anchor whose only child is a decorative SVG announces as nothing."""
    assert 'aria-label="LinkedIn"' in page
    assert 'aria-label="Email"' in page
    assert page.count('aria-hidden="true"') >= 2


def test_the_mail_label_is_generic_until_the_script_decodes_it(page):
    """`aria-label` is page source like any other attribute. The real address
    goes in only at runtime, or the encoding was pointless."""
    label = re.search(r'id="mail"[^>]*aria-label="([^"]+)"', page).group(1)
    assert label == "Email"
    assert "aria-label', address" in page   # ...and the script replaces it


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
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    )

    assert 'href=""' not in page
    assert "data-a=" not in page
    assert "linkedin" not in page.lower()
    assert json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
        .group(1)
    )["author"].get("sameAs") is None


# --- One list, tagged by source ---------------------------------------------

def test_signup_posts_to_this_site_not_a_third_party(page):
    """Same-origin, so the page still makes zero external requests on load and
    the address goes to the radar alias rather than a form service."""
    assert '<form class="join" id="joinForm" method="post" action="/api/subscribe"' in page
    assert 'name="email" type="email"' in page
    assert ">Subscribe</button>" in page


def test_the_field_has_a_real_label(page):
    """A placeholder disappears the moment anyone types and is not required to
    be announced at all."""
    assert '<label class="sr" for="joinEmail">Email address</label>' in page
    assert 'id="joinEmail"' in page


def test_the_honeypot_is_hidden_from_people_but_present_for_bots(page):
    assert 'name="company"' in page
    assert 'tabindex="-1"' in page
    assert '<div class="hp" aria-hidden="true">' in page
    # display:none would let a bot skip it, which defeats the point.
    trap = page.split(".hp{")[1].split("}")[0]
    assert "left:-9999px" in trap and "display:none" not in trap


def test_the_result_message_is_a_live_region_that_ships_empty(page):
    """Injecting the element with the message gives a screen reader nothing to
    announce — the region has to already be there."""
    assert '<p class="note" id="joinNote" role="status"></p>' in page


def test_the_hosted_page_is_the_failure_route_only(monkeypatch):
    """The publication's own signup page is where a broken function sends
    people — not the first thing the page asks them to click."""
    monkeypatch.setattr(site, "SUBSCRIBE_URL", "https://cre-radar.beehiiv.com/subscribe")
    page = site.render(
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    )

    assert page.count("cre-radar.beehiiv.com") == 1
    assert "Subscribe here instead" in page
    assert "utm_medium=cre-radar" in page
    # Entities are not decoded inside <script>: `&amp;` here ships literally
    # and beehiiv reads the tag as `amp;utm_medium`.
    assert "&amp;utm_medium" not in page


def test_an_unset_hosted_page_omits_the_fallback_link(page):
    """Same rule as every other link constant: omit it, never ship a dead one.
    The failure message still shows; only the link goes."""
    assert "Subscribe here instead" not in page
    assert "beehiiv.com" not in page
    assert "Could not reach the server." in page   # the message survives


def test_the_button_never_hardcodes_white_on_accent(page):
    """`--accent` inverts in dark mode: white on the light blue is 2.5:1."""
    filled = page.split(".join .cta{")[1].split("}")[0]
    assert "color:var(--on-accent)" in filled
    assert "#fff" not in filled


def test_drawer_bodies_are_not_left_in_the_page_flow(page):
    """They live under the nav now. A stray copy at the bottom would show the
    same text twice for anyone without JS."""
    assert page.count("Learn how we think") == 1
    assert page.count("Built by") == 1


def test_subscribe_uses_the_sites_own_copy_and_states_the_cadence(page):
    """The cadence is the first thing anyone weighing a signup wants to know,
    and the button cannot say it on its own."""
    assert "Learn how we think" in page
    assert "<strong>weekly</strong> digest" in page
    assert "notes from the field" in page
    assert "No spam. Unsubscribe anytime." in page


def test_own_form_until_an_embed_is_configured(page):
    """No embed set means no iframe, which means the page still loads without
    making a single external request."""
    assert "<iframe" not in page
    assert 'id="joinForm"' in page


def test_embed_replaces_the_form_when_configured(monkeypatch):
    monkeypatch.setattr(site, "BEEHIIV_EMBED_URL", "https://embeds.beehiiv.com/abc")
    page = site.render(
        [], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    )

    assert '<iframe class="embed" src="https://embeds.beehiiv.com/abc"' in page
    assert 'title="Subscribe to the newsletter"' in page   # the a11y name
    assert 'id="joinForm"' not in page


# --- The product name is one constant ---------------------------------------

def test_the_public_name_is_on_the_page_in_every_slot(page):
    assert "<title>SoCal CRE Events</title>" in page
    assert '<a class="brand" href="#top">SoCal CRE Events</a>' in page
    assert "Upcoming SoCal CRE Events</h1>" in page
    assert json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
        .group(1)
    )["name"] == "SoCal CRE Events"


def test_renaming_the_product_does_not_rename_the_command(monkeypatch):
    """`APP_NAME` is the only place the public name is written. `cre-radar` is
    the repo, the CLI and the cron entry, and must survive a rename."""
    monkeypatch.setattr(site, "APP_NAME", "Renamed Thing")
    page = site.render([], generated=datetime(2026, 8, 26, 12, 0, tzinfo=UTC))

    assert "SoCal CRE Events" not in page
    assert page.count("Renamed Thing") == 4      # title, brand, h1, json-ld
    assert "cre-radar" in page                   # the footer credit stands
