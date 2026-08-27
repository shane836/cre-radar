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
