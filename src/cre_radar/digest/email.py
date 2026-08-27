"""Send the digest through Resend.

Unconfigured is not an error: with no ``RESEND_API_KEY`` the send is skipped and
reported, so the Obsidian note still lands and the run still exits clean.
"""
from __future__ import annotations

from datetime import date

from ..config import digest_from, digest_to, resend_api_key


def send(html_body: str, *, day: date) -> str | None:
    """Send the digest. Returns the Resend message id, or None if skipped."""
    key, sender, recipients = resend_api_key(), digest_from(), digest_to()
    if not (key and sender and recipients):
        return None

    import resend

    resend.api_key = key
    response = resend.Emails.send({
        "from": sender,
        "to": recipients,
        "subject": f"CRE Radar — {day.strftime('%d %b')}",
        "html": html_body,
    })
    return response.get("id") if isinstance(response, dict) else None
