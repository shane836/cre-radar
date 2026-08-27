"""The no-model extractor, pinned against real pages captured on 2026-08-26.

Every assertion here encodes a bug that was actually observed, not imagined:
stale dates bleeding across events, navigation menus arriving as events, and
dates printed below the title instead of above it.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cre_radar.heuristic import extract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date(2026, 8, 26)


def page(slug: str) -> str:
    return (FIXTURES / f"{slug}.page.txt").read_text()


def events(slug: str, org: str):
    return extract(page(slug), org=org, today=TODAY)


def by_title(items, needle: str):
    return next(e for e in items if needle.lower() in e.title.lower())


def test_naiop_dates_and_times_are_exact():
    """Date above the title, time on the following line."""
    found = events("naiop-socal", "NAIOP SoCal")
    assert by_title(found, "Veterans Happy Hour").starts_at == "2026-09-10T17:00"
    assert by_title(found, "LA BBQ").starts_at == "2026-09-17T17:30"


def test_breaa_date_printed_below_the_title():
    """BREAA prints the date *after* the link. Reading only backwards mis-dated
    the LACMA event by two weeks."""
    found = events("breaa", "BREAA")
    assert by_title(found, "LACMA Concert Series").starts_at == "2026-09-11T18:00"


def test_breaa_navigation_is_not_mistaken_for_events():
    """The sidebar is ~30 undated links; they used to arrive as events."""
    titles = {e.title.lower() for e in events("breaa", "BREAA")}

    for nav in ("mission", "breaa board of directors", "corporate partnership",
                "anti-racism & bias resources", "la events"):
        assert nav not in titles


def test_aagla_chrome_is_rejected():
    titles = {e.title.lower() for e in events("aagla", "AAGLA")}

    for junk in ("terms of use", "privacy policy", "list view", "calendar"):
        assert junk not in titles


def test_every_event_has_a_date_and_a_url():
    for slug, org in (("naiop-socal", "NAIOP SoCal"), ("breaa", "BREAA"), ("aagla", "AAGLA")):
        for event in events(slug, org):
            assert event.starts_at, f"{slug}: {event.title} has no date"
            assert event.url.startswith("http"), f"{slug}: {event.title} has no url"


def test_a_stale_date_does_not_bleed_onto_a_later_link():
    """A date far above a link is not that link's date."""
    text = "\n".join([
        "September", "10", "[Real Event](https://x.test/e/1)", "5:00 PM",
        *["filler"] * 10,
        "[Footer Link](https://x.test/footer)",
    ])
    found = extract(text, today=TODAY)

    assert [e.title for e in found] == ["Real Event"]


def test_missing_year_resolves_to_the_next_occurrence():
    """Listings omit the year; January seen in August means next January."""
    text = "January\n15\n[Winter Forum](https://x.test/e/9)\n9:00 AM"
    assert extract(text, today=TODAY)[0].starts_at == "2027-01-15T09:00"


@pytest.mark.parametrize("clock,expected", [("9:00 AM", "T09:00"), ("5:30 PM", "T17:30"),
                                            ("12:00 PM", "T12:00"), ("12:30 AM", "T00:30")])
def test_twelve_hour_clock_conversion(clock, expected):
    text = f"March\n3\n[Panel Session](https://x.test/e/2)\n{clock}"
    assert extract(text, today=TODAY)[0].starts_at.endswith(expected)


def test_unlinked_title_is_recovered():
    """Bisnow links only the card image and renders the title as plain text.
    Requiring [title](url) found zero events on the whole calendar."""
    url = "https://www.bisnow.com/events/los-angeles/office/summit-9949"
    text = (
        f"[]({url})\n"
        "In Person\n"
        "Los Angeles | Office\n"
        "September 15, 2026 | 8:00 AM PDT\n"
        "Los Angeles Office & Workplace Summit\n"
        f"[Learn More]({url})"
    )
    found = extract(text, org="Bisnow", today=TODAY)

    assert len(found) == 1
    assert found[0].title == "Los Angeles Office & Workplace Summit"
    assert found[0].starts_at == "2026-09-15T08:00"


def test_unlinked_card_without_a_title_is_skipped():
    """An image link with nothing usable beneath it is not an event."""
    text = "[](https://x.test/card)\nIn Person\n[](https://x.test/next)"
    assert extract(text, today=TODAY) == []
