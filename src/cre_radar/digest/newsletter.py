"""Send the public issue through MailerLite, and read the list back.

The second transport in this package, and the only one that talks to strangers.
`email.py` sends one message to one known address through Resend; this creates a
campaign against a list and hands MailerLite the send.

**MailerLite, not Resend or beehiiv.** The firm is already authenticating and
warming `news.masonequitypartners.com` there for the associates blast, and a
second provider would mean a second sending domain, a second set of SPF/DKIM
records and a second reputation to build. The two audiences stay apart as two
*groups* inside one account — same domain, same reputation, different
recipients. Never send this campaign to the account rather than to the group:
the associates list is in there.

**beehiiv cannot do this job.** Its Create Post endpoint is Max/Enterprise only
and the cre-radar publication is on the free plan, which is why the signup box
promised a weekly digest for weeks that nothing sent.

Unconfigured is not an error, the same contract `email.py` follows: with no
``MAILERLITE_API_KEY`` every function here no-ops and the daily run still exits
clean. A configured transport that *fails*, though, raises — a newsletter that
silently does not go out is the failure this module exists to end.
"""
from __future__ import annotations

import httpx

from ..config import (
    mailerlite_api_key,
    mailerlite_group_id,
    newsletter_from,
    newsletter_from_name,
)

API = "https://connect.mailerlite.com/api"

# One page is 100; the list is two people. The cap exists so a paging bug can
# never turn into an unbounded loop against someone else's API.
_PAGE = 100
_MAX_PAGES = 50

TIMEOUT = 30.0


class NewsletterError(RuntimeError):
    """MailerLite refused something. Carries its own body — the reason is in it."""


def configured() -> bool:
    """True when there is a key, a group and a sender to send from."""
    return bool(mailerlite_api_key() and mailerlite_group_id() and newsletter_from())


def _request(method: str, path: str, **kwargs: object) -> dict:
    key = mailerlite_api_key()
    if not key:
        raise NewsletterError("MAILERLITE_API_KEY is not set")

    response = httpx.request(
        method,
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
        **kwargs,  # type: ignore[arg-type]
    )
    if response.status_code >= 400:
        # The body carries the reason — a bad group id, an unverified sender
        # domain, HTML MailerLite rejected. Logging the status alone would turn
        # a five-minute fix into a guessing game.
        raise NewsletterError(
            f"{method} {path} returned {response.status_code}: {response.text[:800]}"
        )
    return response.json() if response.content else {}


def subscribers(status: str = "active") -> list[dict]:
    """Everyone in the cre-radar group, newest first.

    Scoped to the group, never the account: the associates list shares this
    MailerLite account and must not appear here.
    """
    group = mailerlite_group_id()
    if not group or not mailerlite_api_key():
        return []

    found: list[dict] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        params: dict[str, object] = {"limit": _PAGE, "filter[status]": status}
        if cursor:
            params["cursor"] = cursor
        payload = _request("GET", f"/groups/{group}/subscribers", params=params)
        found.extend(payload.get("data") or [])
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return found


def new_since(people: list[dict], days: int) -> list[str]:
    """Addresses that joined within ``days``. Input is whatever :func:`subscribers`
    returned, so the report costs no extra API call."""
    from datetime import datetime, timedelta

    cutoff = datetime.now().astimezone() - timedelta(days=days)
    fresh: list[str] = []
    for person in people:
        stamp = person.get("subscribed_at") or person.get("created_at")
        if not stamp:
            continue
        try:
            # MailerLite returns "2026-09-04 17:22:31", naive and in the
            # account's timezone. Compare in local time rather than pretending
            # to know better; a few hours either way cannot matter to a
            # seven-day window. (fromisoformat has handled a "Z" suffix since
            # 3.11, so no pre-substitution is needed.)
            joined = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if joined.tzinfo is None:
            joined = joined.astimezone()
        if joined >= cutoff:
            fresh.append(str(person.get("email", "")))
    return [address for address in fresh if address]


def send_campaign(html: str, *, subject: str, name: str) -> str | None:
    """Create the campaign and hand it to MailerLite to send now.

    Two calls, because MailerLite separates them: a campaign is created as a
    draft and only sends once it is scheduled. Returning after the create would
    leave a draft nobody looks at — exactly the silent non-send this replaces.
    """
    if not configured():
        return None

    from .render import UNSUBSCRIBE_TOKEN

    # Cheaper to catch here than to have MailerLite reject the campaign after
    # it exists, leaving a draft behind.
    if UNSUBSCRIBE_TOKEN not in html:
        raise NewsletterError(
            f"refusing to send: the body carries no {UNSUBSCRIBE_TOKEN} link"
        )

    created = _request("POST", "/campaigns", json={
        "name": name,
        "type": "regular",
        "emails": [{
            "subject": subject,
            "from": newsletter_from(),
            "from_name": newsletter_from_name() or "CRE Events Radar",
            "content": html,
        }],
        "groups": [mailerlite_group_id()],
    })

    campaign_id = str((created.get("data") or {}).get("id") or "")
    if not campaign_id:
        raise NewsletterError(f"campaign created but no id came back: {created}")

    _request("POST", f"/campaigns/{campaign_id}/schedule", json={"delivery": "instant"})
    return campaign_id
