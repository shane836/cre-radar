"""The digest is the product. Times must render local, and missing fields must
never crash the note."""
from __future__ import annotations

from datetime import UTC, date, datetime

from cre_radar import db
from cre_radar.digest import render
from cre_radar.models import Verdict
from cre_radar.persist import persist_event

from .conftest import make_event


def _pending():
    conn = db.connect(":memory:")
    persist_event(conn, make_event(
        source="llm:naiop-socal", level="official", price="$95 members",
        when=datetime(2026, 9, 15, 1, 0, tzinfo=UTC)))          # 18:00 PT, 14 Sept
    persist_event(conn, make_event(
        source="llm:cssa", level="official", title="Storage Roundtable",
        venue="Online", when=None, city="Online"))
    for row in db.unscored(conn, "events"):
        db.apply_verdict(conn, "events", row["id"],
                         Verdict(score=80, reason="Worth it.", topics=["self storage"]))
    return db.pending_events(conn, 55)


def test_times_render_in_local_not_utc():
    """Stored UTC is 2026-09-15T01:00; the reader must see the 14th at 6 pm."""
    note = render.markdown(_pending(), day=date(2026, 8, 26))

    assert "Mon 14 Sep" in note
    assert "6 pm" in note
    assert "15 Sep" not in note


def test_markdown_has_vault_frontmatter_and_links():
    note = render.markdown(_pending(), day=date(2026, 8, 26))

    assert note.startswith("---\ntype: reference")
    assert "[[Programming_Automation]]" in note
    assert "[Capital Markets Forum](https://llm:naiop-socal.test/e/1)" in note
    assert "WEEK OF SEP 14" in note


def test_undated_event_is_grouped_last():
    note = render.markdown(_pending(), day=date(2026, 8, 26))

    assert "DATE TBD" in note
    assert note.index("WEEK OF") < note.index("DATE TBD")


def test_html_is_email_safe_and_self_contained():
    """Mail clients drop <style> blocks and ignore flexbox; every style must be
    inline, every layout a table, and no asset may be external."""
    body = render.html_email(_pending(), day=date(2026, 8, 26))

    assert "Capital Markets Forum" in body
    assert "WEEK OF SEP 14" in body
    assert "<table" in body
    assert "<style" not in body and "display:flex" not in body
    assert "http://" not in body.replace("https://", "")
    assert "src=" not in body                      # no remote images


def test_html_pill_reflects_the_category():
    body = render.html_email(_pending(), day=date(2026, 8, 26))

    assert "Panel" in body                          # make_event uses category="panel"


def test_empty_digest_still_renders():
    assert "Nothing new" in render.markdown([], day=date(2026, 8, 26))


def test_venue_is_not_repeated_as_city():
    """Sources without a venue fall back to the city; the reader must not see
    "Online, Online"."""
    conn = db.connect(":memory:")
    persist_event(conn, make_event(
        source="llm:cssa", level="official", title="Webinar",
        venue="Online", city="Online", when=datetime(2026, 9, 15, 1, tzinfo=UTC)))
    for row in db.unscored(conn, "events"):
        db.apply_verdict(conn, "events", row["id"], Verdict(score=80, reason="r"))

    note = render.markdown(db.pending_events(conn, 55), day=date(2026, 8, 26))
    assert "Online, Online" not in note
    assert "Online" in note


def test_link_fingerprint_ignores_tracking_params():
    """BOMA appends ?sourceTypeId= to every link. If that reached the
    fingerprint, the source would re-extract on every single run."""
    from cre_radar.fetch import condense, link_fingerprint

    base = "<body><a href='https://x.test/e/1{q}'>Capital Markets Forum</a></body>"
    clean = condense(base.format(q=""), "https://x.test/")[0]
    tracked = condense(base.format(q="?sourceTypeId=Website"), "https://x.test/")[0]

    assert clean != tracked
    assert link_fingerprint(clean) == link_fingerprint(tracked)
