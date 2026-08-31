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

from .. import APP_NAME
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


def html_email(rows: list[sqlite3.Row], *, day: date) -> str:
    """Single-column, table-based, inline-styled. No external assets."""
    font = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
    parts = [
        f'<div style="{font};background:#f6f7f9;padding:24px 12px;margin:0">',
        '<div style="max-width:600px;margin:0 auto">',
        ('<div style="font-size:23px;font-weight:700;color:#1b1b20;margin:0 0 4px">'
         f"&#128197;&nbsp; Upcoming {_esc(APP_NAME)}</div>"),
        ('<div style="font-size:13px;color:#7a7a85;margin:0 0 18px">'
         f'{day.strftime("%A, %-d %B %Y")} &middot; {len(rows)} events</div>'),
    ]

    if not rows:
        parts.append(
            '<div style="background:#fff;border:1px solid #e6e6eb;border-radius:12px;'
            'padding:24px;text-align:center;color:#8a8a94;font-size:14px">'
            "Nothing new cleared the relevance floor.</div>"
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

    parts.append(
        '<div style="font-size:11px;color:#a0a0aa;text-align:center;padding:6px 0 0">'
        "cre-radar &middot; scored against scoring.toml</div></div></div>"
    )
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
        f"# 📅 Upcoming {APP_NAME}",
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
