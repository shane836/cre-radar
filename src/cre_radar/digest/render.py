"""Turn scored events into the two shapes the digest ships in: HTML and Markdown.

Both group by week and lead with a date chip, because the question you actually
ask a digest is "what is coming up, and when" — not "what scored highest". Score
decides what appears; the calendar decides the order.

The HTML is email-safe: tables rather than flexbox, every style inline, no
external assets. Mail clients drop <style> blocks and ignore modern layout.
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import date, datetime, timedelta

from .. import HEADING
from ..identity import format_local_minute

DEFAULT_TZ = "America/Los_Angeles"

# Category -> (background, text). Light fills with dark text stay readable in
# both mail-client themes, which a saturated pill does not.
_PILL = {
    "panel":      ("#e8f0fe", "#1a56b8"),
    "networking": ("#e6f6ea", "#1c7a3d"),
    "webinar":    ("#e0f5f2", "#0f6f63"),
    "conference": ("#efe7fd", "#5b32b0"),
}
_FALLBACK_PILL = "panel"


# --- shared helpers ---------------------------------------------------------

def _local(row: sqlite3.Row) -> datetime | None:
    raw = row["starts_at"]
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return datetime.fromisoformat(
        format_local_minute(moment, row["timezone"] or DEFAULT_TZ)
    )


def _time_label(moment: datetime | None) -> str:
    """`6 pm` / `10:30 am`. Midnight means the source gave a date only."""
    if moment is None or (moment.hour == 0 and moment.minute == 0):
        return ""
    hour = moment.hour % 12 or 12
    suffix = "am" if moment.hour < 12 else "pm"
    return f"{hour}:{moment.minute:02d} {suffix}" if moment.minute else f"{hour} {suffix}"


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_label(day: date) -> str:
    return f"WEEK OF {_week_start(day).strftime('%b %-d').upper()}"


def group_by_week(rows: list[sqlite3.Row]) -> list[tuple[str, list[sqlite3.Row]]]:
    """Order events by date and bucket them into weeks. Undated go last."""
    dated = sorted(
        (r for r in rows if _local(r)), key=lambda r: _local(r)  # type: ignore[arg-type]
    )
    undated = [r for r in rows if not _local(r)]

    groups: list[tuple[str, list[sqlite3.Row]]] = []
    for row in dated:
        label = _week_label(_local(row).date())  # type: ignore[union-attr]
        if groups and groups[-1][0] == label:
            groups[-1][1].append(row)
        else:
            groups.append((label, [row]))
    if undated:
        groups.append(("DATE TBD", undated))
    return groups


def _meta_parts(row: sqlite3.Row, moment: datetime | None) -> list[str]:
    """The pieces of `6 pm · NAIOP SoCal · Los Angeles`, skipping what's missing."""
    city, org = row["city"], row["org"]

    # Time, host, city — in that order, and nothing else. The venue name and
    # street address are noise at a glance; the city is what decides whether you
    # can get there. Anything the adapter could not resolve simply drops out.
    if city and org and city.strip().lower() == org.strip().lower():
        city = None

    return [p for p in (_time_label(moment), org, city) if p]


def _meta(row: sqlite3.Row, moment: datetime | None) -> str:
    """Markdown form — a literal middot is fine in a UTF-8 vault note."""
    return " · ".join(_meta_parts(row, moment))


def _topics(row: sqlite3.Row) -> list[str]:
    try:
        return json.loads(row["topics"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


# --- HTML (email) -----------------------------------------------------------

def _esc(value: str | None) -> str:
    """Escape for HTML *and* force pure ASCII.

    An email body is a fragment — there is no <head> to declare a charset in, and
    clients that guess wrong render "—" as "â€"". Converting every non-ASCII
    character to a numeric reference sidesteps the guess entirely.
    """
    escaped = html.escape(value or "")
    return escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _chip(moment: datetime | None) -> str:
    """The stacked WED / 26 / AUG date box."""
    if moment is None:
        return (
            '<td width="58" valign="top" style="padding:0 14px 0 0">'
            '<div style="border:1px solid #e3e3e8;border-radius:8px;padding:6px 0;'
            'text-align:center;font-family:Helvetica,Arial,sans-serif">'
            '<div style="font-size:10px;color:#8a8a94;letter-spacing:.05em">DATE</div>'
            '<div style="font-size:15px;font-weight:700;color:#26262c;line-height:1.2">TBD</div>'
            "</div></td>"
        )
    return (
        '<td width="58" valign="top" style="padding:0 14px 0 0">'
        '<div style="border:1px solid #e3e3e8;border-radius:8px;padding:5px 0;'
        'text-align:center;font-family:Helvetica,Arial,sans-serif">'
        f'<div style="font-size:10px;color:#8a8a94;letter-spacing:.06em">'
        f'{moment.strftime("%a").upper()}</div>'
        f'<div style="font-size:20px;font-weight:700;color:#26262c;line-height:1.15">'
        f'{moment.day}</div>'
        f'<div style="font-size:10px;color:#8a8a94;letter-spacing:.06em">'
        f'{moment.strftime("%b").upper()}</div>'
        "</div></td>"
    )


def _pill_html(category: str | None) -> str:
    key = category if category in _PILL else _FALLBACK_PILL
    background, colour = _PILL[key]
    return (
        f'<td align="right" valign="top" style="padding:0 0 0 10px">'
        f'<span style="background:{background};color:{colour};font-size:11px;'
        f'font-weight:600;padding:3px 10px;border-radius:11px;white-space:nowrap;'
        f'font-family:Helvetica,Arial,sans-serif">{_esc(key.capitalize())}</span></td>'
    )


FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def _cards(rows: list[sqlite3.Row], *, empty: str) -> str:
    """The week-grouped event cards, shared by the digest and the newsletter.

    Both channels show the same events in the same shape; only the wrapper
    around them differs. Keeping one builder means a fix to the card markup —
    the part mail clients are fussy about — cannot land in one and miss the
    other.
    """
    parts: list[str] = []

    if not rows:
        parts.append(
            '<div style="background:#fff;border:1px solid #e6e6eb;border-radius:12px;'
            'padding:24px;text-align:center;color:#8a8a94;font-size:14px">'
            f"{_esc(empty)}</div>"
        )

    for label, group in group_by_week(rows):
        parts.append(
            '<div style="background:#fff;border:1px solid #e6e6eb;border-radius:12px;'
            'overflow:hidden;margin:0 0 14px">'
            f'<div style="background:#f2f3f6;padding:8px 16px;font-size:11px;'
            f'font-weight:700;letter-spacing:.07em;color:#6b6b76">{_esc(label)}</div>'
        )
        for index, row in enumerate(group):
            moment = _local(row)
            border = "" if index == 0 else "border-top:1px solid #eeeef2;"
            parts.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
                f'style="{border}"><tr><td style="padding:13px 16px">'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                f"{_chip(moment)}"
                f'<td valign="top">'
                f'<a href="{_esc(row["url"])}" style="font-size:15px;font-weight:600;'
                f'color:#1b1b20;text-decoration:none;line-height:1.35">'
                f'{_esc(row["title"])}</a>'
                f'<div style="font-size:12.5px;color:#7a7a85;margin-top:3px">'
                f"{' &middot; '.join(_esc(p) for p in _meta_parts(row, moment))}"
                "</div></td>"
                f"{_pill_html(row['category'])}"
                "</tr></table></td></tr></table>"
            )
        parts.append("</div>")

    return "".join(parts)


def html_email(rows: list[sqlite3.Row], *, day: date) -> str:
    """Single-column, table-based, inline-styled. No external assets."""
    return "".join([
        f'<div style="{FONT};background:#f6f7f9;padding:24px 12px;margin:0">',
        '<div style="max-width:600px;margin:0 auto">',
        ('<div style="font-size:23px;font-weight:700;color:#1b1b20;margin:0 0 4px">'
         f"&#128197;&nbsp; {_esc(HEADING)}</div>"),
        ('<div style="font-size:13px;color:#7a7a85;margin:0 0 18px">'
         f'{day.strftime("%A, %-d %B %Y")} &middot; {len(rows)} events</div>'),
        _cards(rows, empty="Nothing new cleared the relevance floor."),
        ('<div style="font-size:11px;color:#a0a0aa;text-align:center;padding:6px 0 0">'
         "cre-radar &middot; scored against scoring.toml</div></div></div>"),
    ])


# --- HTML (public newsletter) -----------------------------------------------

# MailerLite's merge tag for the per-recipient opt-out link. It has to reach
# MailerLite verbatim: it is ASCII with no HTML-special characters, so `_esc`
# would pass it through unharmed, but it is never escaped anyway because it is
# built into the href rather than interpolated from data. A campaign whose HTML
# is missing this is rejected at send time, which is the correct failure — an
# unsubscribe link is not optional in bulk mail.
UNSUBSCRIBE_TOKEN = "{$unsubscribe}"


def html_newsletter(
    rows: list[sqlite3.Row],
    *,
    day: date,
    horizon_days: int,
    site_url: str = "",
    firm: str = "",
    firm_url: str = "",
    postal_address: str = "",
) -> str:
    """The public issue. Same cards as the digest; a different frame around them.

    Three things separate this from :func:`html_email`, and all three are
    consequences of it going to strangers rather than to Shane:

    * it says what it is and who sends it, because the reader did not build it;
    * it carries an unsubscribe link and a postal address, which bulk mail is
      required to have and a one-recipient digest is not;
    * it states its own window, so "nothing this fortnight" reads as a fact
      rather than as a broken email.
    """
    heading = f"{_esc(HEADING)}"
    empty = f"No events cleared the bar in the next {horizon_days} days."

    footer: list[str] = [
        ('<div style="font-size:11px;color:#8a8a94;text-align:center;'
         'padding:14px 8px 0;line-height:1.6">')
    ]
    if site_url:
        footer.append(
            f'<a href="{_esc(site_url)}" style="color:#7a7a85">'
            "See the full listing</a><br>"
        )
    if firm:
        sender = (
            f'<a href="{_esc(firm_url)}" style="color:#7a7a85">{_esc(firm)}</a>'
            if firm_url else _esc(firm)
        )
        footer.append(f"Sent by {sender}.<br>")
    if postal_address:
        footer.append(f"{_esc(postal_address)}<br>")
    footer.append(
        f'<a href="{UNSUBSCRIBE_TOKEN}" style="color:#8a8a94;'
        'text-decoration:underline">Unsubscribe</a></div>'
    )

    return "".join([
        f'<div style="{FONT};background:#f6f7f9;padding:24px 12px;margin:0">',
        '<div style="max-width:600px;margin:0 auto">',
        ('<div style="font-size:23px;font-weight:700;color:#1b1b20;margin:0 0 4px">'
         f"&#128197;&nbsp; {heading}</div>"),
        ('<div style="font-size:13px;color:#7a7a85;margin:0 0 18px">'
         f'{day.strftime("%A, %-d %B %Y")} &middot; the next {horizon_days} days '
         f"&middot; {len(rows)} events</div>"),
        _cards(rows, empty=empty),
        "".join(footer),
        "</div></div>",
    ])


def html_signup_report(signups: list[str], *, day: date, total: int | None = None) -> str:
    """The block appended to Shane's copy: who joined since the last issue.

    Separate from the issue itself so the subscriber version can never
    accidentally carry it — the two are concatenated by the caller, and only
    for the copy addressed to Shane.
    """
    parts = [
        ('<div style="max-width:600px;margin:18px auto 0;'
         f'{FONT};background:#fff;border:1px solid #e6e6eb;border-radius:12px;'
         'overflow:hidden">'),
        ('<div style="background:#f2f3f6;padding:8px 16px;font-size:11px;'
         'font-weight:700;letter-spacing:.07em;color:#6b6b76">'
         f"NEW SUBSCRIBERS &middot; {len(signups)}</div>"),
        '<div style="padding:13px 16px;font-size:13px;color:#1b1b20;line-height:1.7">',
    ]
    if signups:
        parts.append("<br>".join(_esc(address) for address in signups))
    else:
        parts.append('<span style="color:#8a8a94">Nobody new this week.</span>')
    if total is not None:
        parts.append(
            '<div style="margin-top:8px;font-size:12px;color:#7a7a85">'
            f"{total} active on the list.</div>"
        )
    parts.append("</div></div>")
    return "".join(parts)


# --- Markdown (Obsidian) ----------------------------------------------------

def markdown(rows: list[sqlite3.Row], *, day: date) -> str:
    """A dated vault note. Same grouping; frontmatter matches the vault's convention."""
    lines = [
        "---",
        "type: reference",
        "workstream: programming",
        'hub: "[[Programming_Automation]]"',
        f"date: {day.isoformat()}",
        "---",
        "",
        f"# 📅 {HEADING}",
        "",
        f"*{day.strftime('%A, %-d %B %Y')} · {len(rows)} events*",
        "",
    ]

    if not rows:
        lines.append("_Nothing new cleared the relevance floor._")

    for label, group in group_by_week(rows):
        lines += [f"## {label}", ""]
        for row in group:
            moment = _local(row)
            stamp = moment.strftime("%a %-d %b") if moment else "Date TBD"
            lines.append(f"**{stamp}** — [{row['title']}]({row['url']})")
            meta = _meta(row, moment)
            if meta:
                lines.append(f"{meta}")
            category = row["category"] if row["category"] in _PILL else _FALLBACK_PILL
            tags = " ".join(f"#{t.replace(' ', '-')}" for t in _topics(row))
            lines.append(f"`{category}` · score {row['score']} {tags}".rstrip())
            lines.append("")

    return "\n".join(lines)
