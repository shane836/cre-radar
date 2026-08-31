"""The public page — a browsable version of the same data the digest emails.

Deliberately one self-contained file: no build step, no framework, no external
requests. `publish` writes `public/index.html` and Vercel serves it as a static
site, which means the page cannot break in a way the generator did not.

It answers a different question from the digest. The email asks "what haven't I
told you yet"; the page asks "what is coming up" — so events stay listed until
they happen, whether or not they were emailed.
"""
from __future__ import annotations

import base64
import html
import json
import sqlite3
from datetime import datetime
from functools import cache

from .digest.render import _local, _meta_parts, group_by_week
from .sources import registry

# Same palette as the email, so the two read as one product.
_PILL = {
    "panel":      ("#e8f0fe", "#1a56b8"),
    "networking": ("#e6f6ea", "#1c7a3d"),
    "webinar":    ("#e0f5f2", "#0f6f63"),
    "conference": ("#efe7fd", "#5b32b0"),
}
_ORDER = ("panel", "networking", "webinar", "conference")

# --- Who built this ---------------------------------------------------------
# Public-facing identity, deliberately kept here rather than in `.env`: it is
# not a secret and not environment-specific, and `.env` is gitignored, so a
# value there would vanish for anyone else who ran the project. Any of the
# link constants may be left empty — the renderer omits the link rather than
# emitting a dead one.

SITE_URL = "https://cre-radar.vercel.app"
# The firm's mark, recoloured for each theme from `mep-mark.png` in the website
# repo. The stacked lockup is not usable here: at navbar height its wordmark
# renders about five pixels tall.
LOGO_LIGHT = "assets/mep-mark-light.png"
LOGO_DARK = "assets/mep-mark-dark.png"
BUILDER = "Shane Mason"
BUILDER_ROLE = "principal"
FIRM = "Mason Equity Partners"
FIRM_URL = "https://masonequitypartners.com"

# The *profile* URL. Not /feed/, which is whatever the viewer's own logged-in
# home page happens to be rather than Shane's. Empty = the link is omitted.
LINKEDIN_URL = "https://www.linkedin.com/in/masonshane/"
# A forwarding alias, not the primary work address — if it is ever harvested it
# can be switched off without changing where real mail goes.
CONTACT_EMAIL = "radar@masonequitypartners.com"

# --- Mailing list ------------------------------------------------------------
# One beehiiv audience, tagged by source. `Website - Spec Contact & Insights`
# in the vault is explicit about this: never run a second list. `utm_medium`
# is the placement, matching the site's own footer / cta-band / contact /
# invest tags, so cre-radar signups are separable in beehiiv without being a
# separate audience.

SUBSCRIBE_URL = "https://masonequitypartners.beehiiv.com/subscribe"
SUBSCRIBE_UTM = "utm_source=cre-radar.vercel.app&utm_medium=cre-radar"

# beehiiv's own embed (Settings -> Subscribe Forms -> Embed), e.g.
# "https://embeds.beehiiv.com/<uuid>". Set this and the block becomes an inline
# form; leave it empty and it stays a button to the hosted subscribe page.
#
# Why an embed and not a form posting straight to beehiiv: their `/create`
# endpoint requires a per-session `visit_token` that a static page cannot mint,
# and `/subscribe?email=` does not prefill, so a hand-rolled field would make
# the visitor type the address twice. Verified against the live page, Aug 2026.
BEEHIIV_EMBED_URL = ""


def _esc(value: str | None) -> str:
    return html.escape(value or "")


def _chip(moment: datetime | None) -> str:
    if moment is None:
        return '<div class="chip"><span class="dow">DATE</span><span class="day">TBD</span></div>'
    return (
        f'<div class="chip"><span class="dow">{moment.strftime("%a").upper()}</span>'
        f'<span class="day">{moment.day}</span>'
        f'<span class="mon">{moment.strftime("%b").upper()}</span></div>'
    )


def _mail_token(address: str) -> str:
    """Base64 of the *reversed* address.

    The page never carries the address in a harvestable form. A scraper
    regexing for `name@host.tld` finds nothing, and reversing before encoding
    means the token does not decode to a recognisable address either, so a
    scraper that speculatively base64-decodes every attribute still misses.
    Cheap, and cheap is the point: bulk spam harvesting is a volume business,
    so anything that costs more than a regex is usually skipped.
    """
    return base64.b64encode(address[::-1].encode()).decode()


def _mail_spelled(address: str) -> str:
    """Human-readable, regex-hostile fallback shown when JS never runs."""
    local, _, domain = address.partition("@")
    return f"{local} at {domain.replace('.', ' dot ')}"


# Inline SVG rather than an icon font or a sprite URL: the page's
# zero-external-requests property is the whole reason the mark is base64'd too.
# Both are filled paths on `currentColor`, so they pick up the pill's hover
# colour without a second rule.
_ICON_LINKEDIN = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M20.45 20.45h-3.56v-5.57c0-1.33-.03-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 '
    '2.94v5.67H9.35V9h3.41v1.56h.05c.47-.9 1.63-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 '
    '5.45v6.29zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 '
    '20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 '
    '1.77 24h20.45c.98 0 1.78-.77 1.78-1.72V1.72C24 .77 23.2 0 22.22 0z"/></svg>'
)
_ICON_MAIL = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M3 5h18a1 1 0 0 1 1 1v.4l-9.48 6.09a1 1 0 0 1-1.04 0L2 6.4V6a1 1 0 0 1 '
    '1-1zm19 3.58V18a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V8.58l8.94 5.74a3 3 0 0 0 3.12 '
    '0L22 8.58z"/></svg>'
)


def _jsonld() -> str:
    """schema.org attribution, so `who built this` is machine-answerable too.

    Carries the name, role, firm and profile — never the email. Putting the
    address here would hand it back to exactly the scrapers `_mail_token`
    exists to defeat.
    """
    person: dict[str, object] = {
        "@type": "Person",
        "name": BUILDER,
        "jobTitle": BUILDER_ROLE.capitalize(),
        "affiliation": {"@type": "Organization", "name": FIRM, "url": FIRM_URL},
    }
    if LINKEDIN_URL:
        person["sameAs"] = [LINKEDIN_URL]
    payload = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "LA Real Estate Events",
        "url": SITE_URL,
        "author": person,
    }
    # `</script>` inside JSON would close the block early.
    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


def _drawer(name: str, heading: str, body: str) -> str:
    """One collapsible panel beneath the nav.

    Rendered *open*. The inline script in `<head>` stamps `js` on the root
    element and the `.js` rules collapse it, so a reader without JS gets the
    content inline rather than a button that does nothing. Stamping before
    first paint is what keeps that from flashing open on load.
    """
    return (
        f'<div class="drawer" id="{name}Drawer">'
        f'<div class="drawin"><h2 id="{name}">{heading}</h2>{body}</div></div>'
    )


@cache
def _data_uri(relative: str) -> str:
    """Inline an asset as base64.

    Read at render time rather than pasted into this file as a literal: the
    source stays readable, and re-exporting the mark is a file swap. The page
    still ships self-contained, which is the property that matters.
    """
    from .config import REPO_ROOT

    raw = (REPO_ROOT / relative).read_bytes()
    return f"data:image/png;base64,{base64.b64encode(raw).decode()}"


def _logo() -> str:
    """The firm's mark, linking out to the firm.

    `<picture>` rather than a CSS background so the mark is a real image with
    an accessible name, and so the theme swap needs no JavaScript. `width` and
    `height` are set to reserve the space before the data URI decodes.
    """
    return (
        f'<a class="logo" href="{_esc(FIRM_URL)}" target="_blank" rel="noopener" '
        f'aria-label="{_esc(FIRM)}"><picture>'
        f'<source srcset="{_data_uri(LOGO_DARK)}" '
        'media="(prefers-color-scheme: dark)">'
        f'<img src="{_data_uri(LOGO_LIGHT)}" width="131" height="84" '
        f'alt="{_esc(FIRM)}"></picture></a>'
    )


def _nav() -> str:
    """Sticky bar. The two panels hang directly beneath it, outside the sticky
    element — a drawer tall enough to matter would otherwise pin itself over
    the listing it is supposed to sit above."""
    return (
        '<nav class="nav"><div class="navin">'
        f"{_logo()}"
        '<a class="brand" href="#top">LA Real Estate Events</a>'
        '<button class="navlink" data-drawer="aboutDrawer" '
        'aria-controls="aboutDrawer" aria-expanded="false">About</button>'
        '<button class="navcta" data-drawer="subscribeDrawer" '
        'aria-controls="subscribeDrawer" aria-expanded="false">'
        "Get Events in your Inbox</button>"
        "</div></nav>"
    )


def _subscribe() -> str:
    """The mailing-list block. Copy is the site's, so the two read as one voice.

    Degrades on purpose: with no embed configured this is a button and the page
    still makes zero external requests, which is the property the rest of this
    file works to preserve. The embed trades that for inline completion.
    """
    if BEEHIIV_EMBED_URL:
        action = (
            f'<iframe class="embed" src="{_esc(BEEHIIV_EMBED_URL)}" '
            'title="Subscribe to the newsletter" loading="lazy" '
            'scrolling="no" frameborder="0"></iframe>'
        )
    else:
        href = f"{SUBSCRIBE_URL}?{SUBSCRIBE_UTM}"
        action = (
            f'<p class="links"><a class="cta" href="{_esc(href)}" '
            'target="_blank" rel="noopener">Sign me up &rarr;</a></p>'
        )

    return _drawer(
        "subscribe",
        "Learn how we think",
        "<p>For more events in your inbox, plus market observations and notes "
        "from the field, subscribe here.</p>"
        f"{action}"
        '<p class="fine">No spam. Unsubscribe anytime.</p>',
    )


def _about(source_count: int) -> str:
    """Who made this, why it exists, and how to reach him."""
    # Icon-only, so each needs an `aria-label`: an anchor whose only content is
    # a decorative SVG has no accessible name at all. The mail label stays the
    # generic "Email" in the source — putting the address there would hand it
    # straight back to the harvesters `_mail_token` exists to defeat — and the
    # script swaps in the real one once it has decoded it.
    links = ""
    if LINKEDIN_URL:
        links += (
            f'<a class="icon" href="{_esc(LINKEDIN_URL)}" target="_blank" '
            f'rel="noopener" aria-label="LinkedIn">{_ICON_LINKEDIN}</a>'
        )
    if CONTACT_EMAIL:
        # The spelled fallback moves inside <noscript>: with the script running
        # the icon is the whole link, and without it the reader still gets a
        # readable address instead of an envelope that does nothing.
        links += (
            f'<a class="icon mail" id="mail" data-a="{_mail_token(CONTACT_EMAIL)}" '
            f'rel="nofollow" aria-label="Email">{_ICON_MAIL}'
            f"<noscript>{_esc(_mail_spelled(CONTACT_EMAIL))}</noscript></a>"
        )

    # The colon only makes sense when there is something below it to point at.
    reach = "the contact below:" if links else "me."

    return _drawer(
        "about",
        "About",
        f"<p>Every morning this sweeps {source_count} Southern California commercial real "
        "estate event calendars for relevant events.</p>"
        f'<p class="by">Built by <strong>{_esc(BUILDER)}</strong>, {_esc(BUILDER_ROLE)} '
        f'at <a href="{_esc(FIRM_URL)}" target="_blank" rel="noopener">'
        f"{_esc(FIRM)}</a>.</p>"
        f"<p>Send bug reports and event suggestions to {reach}</p>"
        + (f'<p class="links">{links}</p>' if links else ""),
    )


def _card(row: sqlite3.Row) -> str:
    moment = _local(row)
    category = row["category"] if row["category"] in _PILL else "panel"
    meta = " · ".join(_meta_parts(row, moment))
    return (
        f'<article class="event" data-category="{category}">'
        f"{_chip(moment)}"
        f'<div class="body">'
        f'<a class="title" href="{_esc(row["url"])}" target="_blank" rel="noopener">'
        f'{_esc(row["title"])}</a>'
        f'<p class="meta">{_esc(meta)}</p></div>'
        f'<span class="pill {category}">{category.capitalize()}</span>'
        "</article>"
    )


def render(
    rows: list[sqlite3.Row], *, generated: datetime, source_count: int | None = None
) -> str:
    """One self-contained HTML document. No external assets, no network calls."""
    # Counted from the registry rather than written into the copy, because a
    # number typed into prose goes stale the first time a source is added.
    if source_count is None:
        source_count = len(registry.load())
    counts = {name: sum(1 for r in rows if r["category"] == name) for name in _ORDER}
    filters = "".join(
        f'<button class="filter" data-filter="{name}">{name.capitalize()}'
        f'<span class="count">{counts[name]}</span></button>'
        for name in _ORDER if counts[name]
    )

    groups = "".join(
        f'<section class="week"><h2>{_esc(label)}</h2>'
        + "".join(_card(row) for row in group)
        + "</section>"
        for label, group in group_by_week(rows)
    )

    empty = (
        '<p class="empty">Nothing upcoming cleared the relevance floor.</p>'
        if not rows else ""
    )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LA Real Estate Events</title>
<meta name="description" content="Upcoming Los Angeles commercial real estate and
real estate investment events, updated daily.">
<script>document.documentElement.className='js'</script>
<script type="application/ld+json">{_jsonld()}</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f6f7f9; --card:#fff; --line:#e6e6eb; --ink:#1b1b20; --dim:#7a7a85;
  --head:#f2f3f6; --accent:#1a56b8; --on-accent:#fff;
}}
@media (prefers-color-scheme:dark){{
  :root{{--bg:#141417;--card:#1c1c21;--line:#2c2c33;--ink:#f0f0f2;--dim:#9a9aa4;
        --head:#232329;--accent:#7aa5f0;--on-accent:#141417}}
}}
body{{background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
  padding:0 0 60px}}
.wrap{{max-width:640px;margin:0 auto;padding:22px 14px 0}}
h1{{font-size:24px;letter-spacing:-.01em;margin-bottom:4px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:18px}}
.filters{{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:18px}}
.filter{{background:var(--card);border:1px solid var(--line);color:var(--ink);
  border-radius:999px;padding:5px 13px;font-size:13px;cursor:pointer;
  display:flex;align-items:center;gap:6px;font-family:inherit}}
.filter:hover{{border-color:var(--accent)}}
.filter[aria-pressed=true]{{background:var(--accent);border-color:var(--accent);color:#fff}}
.count{{opacity:.6;font-size:11px}}
.week{{background:var(--card);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;margin-bottom:14px}}
.week h2{{background:var(--head);padding:8px 16px;font-size:11px;font-weight:700;
  letter-spacing:.07em;color:var(--dim)}}
.week.hidden,.event.hidden{{display:none}}
.event{{display:flex;gap:14px;align-items:flex-start;padding:13px 16px;
  border-top:1px solid var(--line)}}
.week h2 + .event{{border-top:0}}
.chip{{flex:0 0 52px;border:1px solid var(--line);border-radius:8px;padding:5px 0;
  display:flex;flex-direction:column;align-items:center;line-height:1.15}}
.dow,.mon{{font-size:10px;letter-spacing:.06em;color:var(--dim)}}
.day{{font-size:20px;font-weight:700}}
.body{{flex:1;min-width:0}}
.title{{font-size:15px;font-weight:600;color:var(--ink);text-decoration:none;
  display:block}}
.title:hover{{color:var(--accent);text-decoration:underline}}
.meta{{font-size:12.5px;color:var(--dim);margin-top:3px}}
.pill{{flex:0 0 auto;font-size:11px;font-weight:600;padding:3px 10px;
  border-radius:11px;white-space:nowrap}}
{"".join(f'.pill.{n}{{background:{bg};color:{fg}}}' for n, (bg, fg) in _PILL.items())}
.empty,.foot{{color:var(--dim);font-size:12px;text-align:center;padding:20px 0}}
.nav{{position:sticky;top:0;z-index:40;background:var(--card);
  border-bottom:1px solid var(--line)}}
.navin{{max-width:640px;margin:0 auto;padding:0 14px;height:52px;
  display:flex;align-items:center;gap:8px}}
.logo{{display:flex;align-items:center;flex:0 0 auto}}
.logo img{{height:28px;width:auto;display:block}}
.brand{{font-weight:700;font-size:15px;margin-right:auto;letter-spacing:-.01em;
  color:var(--ink);text-decoration:none;padding-left:11px;margin-left:11px;
  border-left:1px solid var(--line);white-space:nowrap}}
.brand:hover{{color:var(--accent)}}
@media (max-width:560px){{
  .brand{{font-size:0;padding:0;margin:0 auto 0 0;border:0}}
  /* The CTA label is long enough to push the bar past a narrow phone once the
     brand has collapsed. Trimming it here beats wrapping a sticky navbar. */
  .navcta{{font-size:12.5px;padding:7px 11px}}
  .navlink{{padding:7px 9px}}
}}
.navlink{{font-size:13.5px;color:var(--ink);text-decoration:none;padding:7px 12px;
  border-radius:999px;font-family:inherit;background:none;border:0;cursor:pointer}}
.navlink:hover,.navlink[aria-expanded=true]{{background:var(--head)}}
.navcta{{font-size:13.5px;font-weight:600;background:var(--accent);
  color:var(--on-accent);text-decoration:none;padding:7px 14px;border-radius:999px;
  font-family:inherit;border:0;cursor:pointer;white-space:nowrap}}
.navcta:hover{{opacity:.9}}
.drawer{{background:var(--head);border-bottom:1px solid var(--line)}}
/* Collapsed only once the script has confirmed it can open them again.
   `visibility` is what keeps a closed drawer out of the tab order — height
   alone leaves its links focusable but invisible. */
.js .drawer{{max-height:0;overflow:hidden;visibility:hidden;border-bottom:0;
  transition:max-height .22s ease,visibility .22s}}
.js .drawer.open{{max-height:640px;visibility:visible;
  border-bottom:1px solid var(--line)}}
.drawin{{max-width:640px;margin:0 auto;padding:18px 14px}}
.drawin h2{{font-size:11px;font-weight:700;letter-spacing:.07em;color:var(--dim);
  text-transform:uppercase;margin-bottom:11px}}
.drawin p{{font-size:13.5px;margin-bottom:10px}}
.drawin p:last-child{{margin-bottom:0}}
.drawin a{{color:var(--accent)}}
.drawin .links a{{color:var(--ink);background:var(--card)}}
.links{{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}}
.links a{{display:inline-flex;align-items:center;background:var(--head);
  border:1px solid var(--line);border-radius:999px;padding:6px 14px;
  font-size:13px;text-decoration:none;color:var(--ink)}}
.links a[href]:hover{{border-color:var(--accent);color:var(--accent)}}
.links a{{gap:7px}}
.links a.icon{{padding:7px 12px}}
.links a svg{{width:16px;height:16px;display:block;fill:currentColor}}
/* No href until the script runs, so it must not pretend to be clickable.
   Kept at full --ink: --dim on --head is 3.8:1 in light mode, and this is the
   state a reader with JS disabled is stuck with. Only the cursor differs. */
.mail:not([href]){{cursor:text}}
.join .cta{{background:var(--accent);border-color:var(--accent);
  color:var(--on-accent);font-weight:600}}
.join .cta:hover{{opacity:.9;color:var(--on-accent)}}
.fine{{font-size:12px;color:var(--dim);margin-top:11px}}
.embed{{width:100%;border:0;margin-top:13px;min-height:64px;
  color-scheme:light dark}}
@media (max-width:480px){{
  .event{{flex-wrap:wrap}}
  .pill{{order:3;margin-left:66px}}
}}
</style></head><body>
{_nav()}
{_subscribe()}
{_about(source_count)}
<div class="wrap" id="top">
<h1>&#128197; Upcoming LA Real Estate Events</h1>
<p class="sub">{len(rows)} events &middot; updated {generated.strftime('%-d %b %Y, %H:%M')} PT</p>
<div class="filters">
<button class="filter" data-filter="all" aria-pressed="true">All<span class="count">{len(rows)}</span></button>
{filters}
</div>
{empty}{groups}
<p class="foot">cre-radar &middot; scored against scoring.toml &middot; no AI, just rules</p>
</div>
<script>
// Category filter. Hides events, then hides any week left with nothing in it.
const buttons = document.querySelectorAll('.filter');
buttons.forEach(button => button.addEventListener('click', () => {{
  const want = button.dataset.filter;
  buttons.forEach(b => b.setAttribute('aria-pressed', b === button));
  document.querySelectorAll('.event').forEach(el =>
    el.classList.toggle('hidden', want !== 'all' && el.dataset.category !== want));
  document.querySelectorAll('.week').forEach(week =>
    week.classList.toggle('hidden',
      !week.querySelector('.event:not(.hidden)')));
}}));

// One drawer at a time. Clicking the open one closes it.
const drawers = document.querySelectorAll('.drawer');
const toggles = document.querySelectorAll('[data-drawer]');
function closeAll() {{
  drawers.forEach(d => d.classList.remove('open'));
  toggles.forEach(t => t.setAttribute('aria-expanded', 'false'));
}}
toggles.forEach(toggle => toggle.addEventListener('click', () => {{
  const panel = document.getElementById(toggle.dataset.drawer);
  const wasOpen = panel.classList.contains('open');
  closeAll();
  if (!wasOpen) {{
    panel.classList.add('open');
    toggle.setAttribute('aria-expanded', 'true');
    // The drawers sit below the sticky bar rather than inside it, so an open
    // one is off-screen if the reader has scrolled. Bring it into view.
    window.scrollTo({{top: 0, behavior: 'smooth'}});
  }}
}}));
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeAll(); }});

// A deep link to #about or #subscribe should arrive with that panel open.
const wanted = location.hash.replace('#', '');
if (wanted === 'about' || wanted === 'subscribe') {{
  document.querySelector(`[data-drawer="${{wanted}}Drawer"]`)?.click();
}}

// Rebuild the contact address. It ships reversed-then-base64'd, so it is not in
// the source in any form a harvester's regex matches. Until this runs the link
// has no href and its <noscript> spells the address out — degraded, never broken.
// Sets the label rather than the text: the text is the envelope icon.
const mail = document.getElementById('mail');
if (mail) {{
  const address = atob(mail.dataset.a).split('').reverse().join('');
  mail.href = 'mailto:' + address;
  mail.title = address;
  mail.setAttribute('aria-label', address);
}}
</script>
</body></html>"""
