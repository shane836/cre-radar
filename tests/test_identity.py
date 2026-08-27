"""Rubric B — identity invariants. Pure functions, no DB, no network.

Each test names the rubric dimension it enforces. The governing rule throughout:
false splits are recoverable, false merges are not.
"""
from __future__ import annotations

from datetime import UTC, datetime

from cre_radar.identity import fingerprint, format_local_date, normalize_title

LA = "America/Los_Angeles"
NY = "America/New_York"


def fp(title: str, venue: str = "JW Marriott", moment=None, tz: str = LA) -> str:
    return fingerprint(title=title, venue_name=venue, start_time_utc=moment, timezone=tz)


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_b1_two_sessions_same_day_do_not_collide():
    morning = fp("Capital Markets Forum", moment=utc(2026, 9, 14, 16))   # 09:00 PT
    evening = fp("Capital Markets Forum", moment=utc(2026, 9, 15, 1))    # 18:00 PT
    assert morning != evening


def test_b2_evening_event_keeps_its_local_date():
    """18:00 PT on 14 Sept is 15 Sept in UTC. Slicing the ISO string loses a day."""
    moment = utc(2026, 9, 15, 1)
    assert format_local_date(moment, LA) == "2026-09-14"
    assert moment.isoformat()[:10] == "2026-09-15"   # the trap this guards against


def test_b3_diacritics_do_not_split_a_match():
    assert fp("Café Forum") == fp("Cafe Forum")
    assert normalize_title("Café") == normalize_title("Cafe")


def test_b4_punctuation_does_not_cause_a_false_merge():
    """Under-merging shows a dupe. Over-merging deletes an event. Prefer the dupe."""
    assert fp("A.I.R. CRE Summit") != fp("AIR CRE Summit")


def test_b5_two_sources_seeing_one_event_agree():
    moment = utc(2026, 9, 15, 1)
    bisnow = fp("Capital Markets Forum", "JW Marriott", moment)
    naiop = fp("Capital Markets Forum", "JW Marriott", moment)
    assert bisnow == naiop


def test_b6_undated_events_do_not_all_collapse():
    assert fp("Storage Roundtable", moment=None) != fp("Retail Outlook", moment=None)


def test_b7_timezone_is_part_of_the_key():
    moment = utc(2026, 9, 15, 1)
    assert fp("Forum", moment=moment, tz=LA) != fp("Forum", moment=moment, tz=NY)


def test_venue_normalization_matches_title_rules():
    moment = utc(2026, 9, 15, 1)
    assert fp("Forum", "JW  Marriott", moment) == fp("Forum", "jw marriott", moment)
