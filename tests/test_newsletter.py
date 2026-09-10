"""The public issue. Everything here is about the difference between an email to
Shane and an email to strangers: it is bounded by a window, it has to carry an
opt-out, and it must never go out empty."""
from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta

import pytest

from cre_radar import config, db
from cre_radar.digest import newsletter, render
from cre_radar.models import Verdict
from cre_radar.persist import persist_event

from .conftest import make_event

DAY = date(2026, 8, 26)


def _rows(*offsets_in_days: int):
    """Score events sitting a given number of days from now, and read them back
    through the same query the newsletter uses."""
    conn = db.connect(":memory:")
    now = datetime.now(UTC)
    for index, offset in enumerate(offsets_in_days):
        persist_event(conn, make_event(
            source="llm:naiop-socal", level="official",
            title=f"Event {index}", url=f"https://example.test/e/{index}",
            when=now + timedelta(days=offset),
        ))
    for row in db.unscored(conn, "events"):
        db.apply_verdict(conn, "events", row["id"],
                         Verdict(score=80, reason="Worth it.", topics=["capital markets"]))
    return conn


# --- the window -------------------------------------------------------------

def test_horizon_excludes_events_past_the_window():
    """Subscribers get a fortnight, not the whole site. An unbounded issue would
    be the entire calendar in one email."""
    conn = _rows(3, 30)

    titles = [row["title"] for row in db.newsletter_events(conn, 55, 14)]

    assert titles == ["Event 0"]


def test_horizon_excludes_events_that_have_happened():
    conn = _rows(-5, 3)

    titles = [row["title"] for row in db.newsletter_events(conn, 55, 14)]

    assert titles == ["Event 1"]


def test_horizon_ignores_surfaced_at():
    """`surfaced_at` records what went to *Shane's* digest. A subscriber has
    seen none of it, so a delivered event must still appear."""
    conn = _rows(3)
    db.mark_surfaced(conn, "events", [row["id"] for row in db.newsletter_events(conn, 55, 14)])

    assert len(db.newsletter_events(conn, 55, 14)) == 1


def test_horizon_respects_the_score_floor():
    conn = _rows(3)
    conn.execute("UPDATE events SET score = 10")

    assert db.newsletter_events(conn, 55, 14) == []


# --- the cadence ------------------------------------------------------------

def test_the_issue_goes_out_on_thursdays(monkeypatch):
    """The box promises weekly and the cadence is the promise. `run` calls the
    command every day; this gate is the only thing making it weekly."""
    monkeypatch.delenv("NEWSLETTER_DAY", raising=False)

    assert config.newsletter_day() == 3
    assert calendar.day_name[config.newsletter_day()] == "Thursday"


def test_the_send_day_is_configurable_by_name(monkeypatch):
    monkeypatch.setenv("NEWSLETTER_DAY", "Monday")

    assert config.newsletter_day() == 0


def test_an_unrecognised_send_day_falls_back_to_thursday(monkeypatch):
    """A typo in .env must not silently stop the newsletter for good."""
    monkeypatch.setenv("NEWSLETTER_DAY", "thurdsay")

    assert config.newsletter_day() == 3


def test_the_default_window_is_a_fortnight(monkeypatch):
    monkeypatch.delenv("NEWSLETTER_HORIZON_DAYS", raising=False)

    assert config.newsletter_horizon_days() == 14


# --- the body ---------------------------------------------------------------

def _issue(**kwargs) -> str:
    rows = db.newsletter_events(_rows(3), 55, 14)
    return render.html_newsletter(rows, day=DAY, horizon_days=14, **kwargs)


def test_issue_carries_an_unsubscribe_link():
    """Bulk mail without an opt-out is not sendable, and MailerLite rejects the
    campaign outright."""
    assert render.UNSUBSCRIBE_TOKEN in _issue()
    assert f'href="{render.UNSUBSCRIBE_TOKEN}"' in _issue()


def test_the_unsubscribe_token_survives_the_ascii_escaper():
    """`_esc` rewrites every non-ASCII character as a numeric reference. The
    token is ASCII and carries no HTML-special characters, so it must pass
    through untouched — a `{` or `$` that got escaped would reach MailerLite as
    literal text and the link would go nowhere."""
    assert render._esc(render.UNSUBSCRIBE_TOKEN) == render.UNSUBSCRIBE_TOKEN


def test_issue_is_email_safe_and_self_contained():
    body = _issue()

    assert "<table" in body
    assert "<style" not in body and "display:flex" not in body
    assert "src=" not in body                      # no remote images
    assert body.isascii()                          # no charset guessing


def test_issue_states_its_own_window():
    """"Nothing this fortnight" has to read as a fact, not as a broken email."""
    empty = render.html_newsletter([], day=DAY, horizon_days=14)

    assert "the next 14 days" in empty
    assert "No events cleared the bar in the next 14 days." in empty


def test_footer_omits_what_is_not_configured():
    """Same rule the page's optional links follow: an empty constant drops the
    element rather than shipping a blank one. A placeholder in a compliance
    footer is worse than no line, because it looks filled in."""
    bare = _issue()

    assert "Sent by" not in bare
    assert "See the full listing" not in bare


def test_footer_names_the_sender_when_it_is_configured():
    filled = _issue(
        site_url="https://radar.example.com",
        firm="Mason Equity Partners",
        firm_url="https://example.com",
        postal_address="1 Example St, Los Angeles, CA 90001",
    )

    assert 'href="https://example.com"' in filled
    assert "Sent by" in filled
    assert "1 Example St, Los Angeles, CA 90001" in filled


def test_shanes_daily_digest_carries_no_unsubscribe_link():
    """The daily digest goes to one known address through a different transport.
    An opt-out token in it would render as literal braces."""
    rows = db.newsletter_events(_rows(3), 55, 14)

    assert render.UNSUBSCRIBE_TOKEN not in render.html_email(rows, day=DAY)


# --- the signup report ------------------------------------------------------

def test_signup_report_says_so_when_nobody_joined():
    """A quiet week must not look like a broken pipeline."""
    block = render.html_signup_report([], day=DAY, total=2)

    assert "NEW SUBSCRIBERS &middot; 0" in block
    assert "Nobody new this week." in block
    assert "2 active on the list." in block


def test_signup_report_lists_and_escapes_addresses():
    block = render.html_signup_report(["a@example.com", "b<c>@example.com"], day=DAY)

    assert "a@example.com" in block
    assert "b&lt;c&gt;@example.com" in block


def test_new_since_uses_the_window():
    now = datetime.now().astimezone()
    people = [
        {"email": "fresh@example.com", "subscribed_at": (now - timedelta(days=2)).isoformat()},
        {"email": "old@example.com", "subscribed_at": (now - timedelta(days=40)).isoformat()},
        {"email": "undated@example.com"},
    ]

    assert newsletter.new_since(people, 7) == ["fresh@example.com"]


def test_new_since_survives_an_unparseable_timestamp():
    """A transport must not take the run down over one malformed field."""
    assert newsletter.new_since([{"email": "x@example.com", "created_at": "soon"}], 7) == []


# --- the transport ----------------------------------------------------------

def test_send_refuses_a_body_with_no_opt_out(monkeypatch):
    """Caught here rather than by MailerLite, which would reject the campaign
    only after creating it and leave a draft behind."""
    monkeypatch.setenv("MAILERLITE_API_KEY", "test-key")
    monkeypatch.setenv("MAILERLITE_GROUP_ID", "123")
    monkeypatch.setenv("NEWSLETTER_FROM", "radar@example.com")

    with pytest.raises(newsletter.NewsletterError, match="no .* link"):
        newsletter.send_campaign("<p>hi</p>", subject="s", name="n")


def test_unconfigured_is_a_skip_not_a_failure(monkeypatch):
    """The same contract `email.py` follows: the daily run has to survive an
    unconfigured newsletter, and did for the whole life of the project."""
    monkeypatch.delenv("MAILERLITE_API_KEY", raising=False)

    assert newsletter.configured() is False
    assert newsletter.send_campaign("<p>hi</p>", subject="s", name="n") is None
    assert newsletter.subscribers() == []
