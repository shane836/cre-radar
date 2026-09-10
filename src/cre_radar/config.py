"""Every runtime setting comes from the environment / `.env`.

No secrets and no absolute paths are hardcoded. `.env` is loaded once on import;
each getter has a working default so an unconfigured channel no-ops cleanly
rather than crashing the run.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def db_path() -> str:
    """SQLite file holding events, posts, and run history."""
    return os.environ.get("CRE_DB", "cre_radar.db")


def min_score() -> int:
    """Relevance floor (0-100). Items below this are stored but never surfaced."""
    return int(os.environ.get("CRE_MIN_SCORE", "55"))


def fetch_limit() -> int:
    """Max items one source yields per run. Shared ceiling across all channels."""
    return int(os.environ.get("CRE_FETCH_LIMIT", "40"))


def scoring_path() -> Path:
    """The rule file that IS the scorer. Edit it to retune what surfaces."""
    return Path(os.environ.get("CRE_SCORING", str(REPO_ROOT / "scoring.toml")))


def work_dir() -> Path:
    """Where condensed pages and extracted events are handed between stages."""
    return Path(os.environ.get("CRE_WORK", str(REPO_ROOT / "work")))


def sources_path() -> Path:
    """The editable event-source registry."""
    return Path(os.environ.get("CRE_SOURCES", str(REPO_ROOT / "sources.toml")))


# --- Obsidian ---------------------------------------------------------------

def obsidian_dir() -> Path | None:
    """Vault folder the digest note is written to, or None if unconfigured."""
    raw = os.environ.get("OBSIDIAN_DIGEST_DIR", "").strip()
    return Path(raw) if raw else None


# --- Email ------------------------------------------------------------------

def resend_api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


def digest_from() -> str:
    return os.environ.get("DIGEST_FROM", "").strip()


def digest_to() -> list[str]:
    return _csv("DIGEST_TO")


# --- Newsletter (MailerLite) ------------------------------------------------
#
# A different channel from the digest above, with a different transport and a
# different audience. The digest is one email to Shane through Resend; the
# newsletter is a campaign to the public list through MailerLite.
#
# MailerLite rather than Resend or beehiiv because the firm is already warming
# `news.masonequitypartners.com` there for the associates blast, and a second
# provider would mean a second sending domain and a second warm-up. The two
# lists stay separate inside the account as separate groups — same domain, same
# reputation, different recipients.

# Monday-based, matching `datetime.date.weekday()`.
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def mailerlite_api_key() -> str:
    """MailerLite API token. Empty means the newsletter step skips entirely."""
    return os.environ.get("MAILERLITE_API_KEY", "").strip()


def mailerlite_group_id() -> str:
    """The cre-radar group inside the account.

    A group, not the whole account: the associates blast lives in the same
    MailerLite account and must never receive the events newsletter.
    """
    return os.environ.get("MAILERLITE_GROUP_ID", "").strip()


def newsletter_from() -> str:
    """Sender address. Must be on a domain MailerLite has authenticated."""
    return os.environ.get("NEWSLETTER_FROM", "").strip()


def newsletter_from_name() -> str:
    return os.environ.get("NEWSLETTER_FROM_NAME", "").strip()


def newsletter_day() -> int:
    """Weekday the issue goes out, as `date.weekday()` numbers it. Thursday."""
    raw = os.environ.get("NEWSLETTER_DAY", "thursday").strip().lower()
    return _WEEKDAYS.get(raw, 3)


def newsletter_horizon_days() -> int:
    """How far ahead the issue looks. Subscribers get a bounded window, not
    everything on the site."""
    return int(os.environ.get("NEWSLETTER_HORIZON_DAYS", "14"))


def newsletter_postal_address() -> str:
    """The physical address bulk mail is required to carry.

    Empty omits the line, following the same rule the page's optional links
    follow — a placeholder in a compliance footer is worse than no line at all,
    because it looks filled in.
    """
    return os.environ.get("NEWSLETTER_POSTAL_ADDRESS", "").strip()
