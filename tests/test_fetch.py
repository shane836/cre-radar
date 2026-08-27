"""`condense` is what makes generic extraction possible — links must survive."""
from __future__ import annotations

from cre_radar.fetch import MAX_CHARS, condense

HTML = """
<html><head><style>.a{color:red}</style></head><body>
  <nav><a href="/about">About</a></nav>
  <h1>Upcoming Events</h1>
  <div><a href="/events/capital-markets-2026">Capital Markets Forum</a> — Sept 14, DTLA</div>
  <div><a href="https://other.test/x">Offsite</a></div>
  <a href="#top">Back to top</a>
  <script>track()</script>
</body></html>
"""


def test_relative_links_become_absolute():
    text, truncated = condense(HTML, "https://naiopsocal.org/events/")
    assert "[Capital Markets Forum](https://naiopsocal.org/events/capital-markets-2026)" in text
    assert truncated is False


def test_absolute_links_are_preserved():
    text, _ = condense(HTML, "https://naiopsocal.org/events/")
    assert "[Offsite](https://other.test/x)" in text


def test_chrome_and_anchors_without_targets_are_dropped():
    text, _ = condense(HTML, "https://naiopsocal.org/events/")
    assert "track()" not in text
    assert "color:red" not in text
    assert "About" not in text          # inside <nav>
    assert "[Back to top]" not in text  # fragment-only anchor keeps its label, loses the link
    assert "Back to top" in text


def test_oversized_page_reports_truncation():
    text, truncated = condense("<body>" + ("x " * MAX_CHARS) + "</body>", "https://x.test/")
    assert truncated is True
    assert len(text) == MAX_CHARS


def test_link_fingerprint_ignores_a_changing_date():
    """SSA prints today's date in its page text; the calendar is still the same."""
    from cre_radar.fetch import link_fingerprint

    monday = condense(HTML.replace("<h1>Upcoming Events</h1>", "<h1>Monday, 25 Aug</h1>"),
                      "https://naiopsocal.org/events/")[0]
    tuesday = condense(HTML.replace("<h1>Upcoming Events</h1>", "<h1>Tuesday, 26 Aug</h1>"),
                       "https://naiopsocal.org/events/")[0]

    assert monday != tuesday
    assert link_fingerprint(monday) == link_fingerprint(tuesday)


def test_link_fingerprint_changes_when_an_event_is_added():
    from cre_radar.fetch import link_fingerprint

    before = condense(HTML, "https://naiopsocal.org/events/")[0]
    after = condense(
        HTML.replace("</body>", "<a href='/events/new-panel'>New Panel</a></body>"),
        "https://naiopsocal.org/events/",
    )[0]

    assert link_fingerprint(before) != link_fingerprint(after)
