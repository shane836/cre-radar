"""Pre-flight checks. Answers "will `cre-radar run` work, and if not, why".

Every check reports what to do about a failure, because the failures here are
all configuration rather than code: a missing key, an unwritable vault folder, a
browser that was never installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None
    required: bool = True


def _chromium() -> Check:
    """7 sources are JS-built or 403 a bare HTTP client."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            version = browser.version
            browser.close()
        return Check("Chromium", True, version)
    except Exception as exc:  # noqa: BLE001
        return Check("Chromium", False, f"{type(exc).__name__}",
                     "uv run playwright install chromium  "
                     "(without it the 7 browser-mode sources fail; the other 10 still run)",
                     required=False)


def _obsidian() -> Check:
    directory = config.obsidian_dir()
    if directory is None:
        return Check("Obsidian vault", False, "OBSIDIAN_DIGEST_DIR not set",
                     "Set it in .env, or run digest with --no-obsidian.", required=False)
    if not directory.parent.exists():
        return Check("Obsidian vault", False, f"parent missing: {directory.parent}",
                     "Check the path in .env — Dropbox may not have synced.",
                     required=False)
    return Check("Obsidian vault", True, str(directory))


def _resend() -> Check:
    key, sender, to = config.resend_api_key(), config.digest_from(), config.digest_to()
    if not key:
        return Check("Email (Resend)", False, "RESEND_API_KEY not set",
                     "Set it in .env, or run digest with --no-email.", required=False)
    if not sender or not to:
        return Check("Email (Resend)", False, "DIGEST_FROM or DIGEST_TO missing",
                     "Both are needed before anything sends.", required=False)
    return Check("Email (Resend)", True, f"{sender} -> {', '.join(to)}")


def _scoring() -> Check:
    """scoring.toml IS the scorer — without it nothing can be judged."""
    path = config.scoring_path()
    if not path.exists():
        return Check("scoring.toml", False, f"missing at {path}",
                     "The scorer has no rules; every event would score the same.")
    try:
        from .scoring import load_rules

        rules = load_rules()
    except Exception as exc:  # noqa: BLE001
        return Check("scoring.toml", False, f"{type(exc).__name__}: {exc}",
                     "Fix the TOML syntax.")
    return Check("scoring.toml", True,
                 f"{len(rules.positive)} positive, {len(rules.negative)} negative, "
                 f"{len(rules.org)} orgs")


def _sources() -> Check:
    from .sources.registry import load

    sources = load()
    if not sources:
        return Check("sources.toml", False, "no enabled sources",
                     "Every source is disabled — nothing will be collected.")
    browser = sum(1 for s in sources if s.mode == "browser")
    return Check("sources.toml", True,
                 f"{len(sources)} enabled ({len(sources) - browser} http, {browser} browser)")


def _database() -> Check:
    from . import db

    try:
        conn = db.connect()
        count = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        conn.close()
        return Check("Database", True, f"{Path(config.db_path()).name}, {count} events")
    except Exception as exc:  # noqa: BLE001
        return Check("Database", False, f"{type(exc).__name__}: {exc}",
                     "Check CRE_DB in .env points somewhere writable.")


def run() -> list[Check]:
    """Every check, cheapest first so failures surface fast."""
    return [
        _sources(), _scoring(), _database(),
        _obsidian(), _resend(), _chromium(),
    ]
