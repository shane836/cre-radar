"""The rule scorer. Every case here is one that scored wrong against real data."""
from __future__ import annotations

import pytest

from cre_radar.scoring import load_rules, score_event


@pytest.fixture()
def rules():
    load_rules.cache_clear()
    return load_rules()


def score(**kwargs) -> int:
    return score_event(**kwargs).score


def test_org_name_does_not_leak_into_geography(rules):
    """Adapters fall back to the org as a venue. "California Self Storage
    Association" scored a NorCal holiday party as in-market because of it."""
    verdict = score_event(
        title="2026 NorCal Holiday Networking Lunch",
        org="California Self Storage Association",
        venue="California Self Storage Association",
        city=None, category="networking", rules=rules,
    )

    assert "california +" not in verdict.reason
    assert verdict.score < 55


def test_out_of_market_chapter_is_rejected(rules):
    assert score(title="Bay Area Chapter Mixer", org="AIR CRE",
                 category="networking", rules=rules) < 55


def test_in_market_capital_event_clears_the_floor(rules):
    assert score(title="LA Capital Markets Outlook",
                 org="California Self Storage Association", city="Los Angeles",
                 category="panel", rules=rules) >= 55


@pytest.mark.parametrize("title", [
    "2026 CSSA California Lien Law Series With Carlos Kaslow",
    "2026 Enforcing the Non-Monetary Default Remedy",
])
def test_storage_compliance_training_is_rejected(title, rules):
    """CSSA runs two kinds of programming, and only one is for him. Its
    networking ranks highest of any org here; its legal-compliance series is
    operator education wearing an on-asset-class badge."""
    assert score(title=title, org="California Self Storage Association",
                 city="Los Angeles", category="panel", rules=rules) < 55


def test_a_social_event_at_a_good_org_is_kept(rules):
    """The LACMA concert, the golf tournament, the holiday party: the format is
    not the point, the room is. Penalising the format cost all three."""
    assert score(title="2nd Annual LACMA Concert Series",
                 org="Berkeley Real Estate Alumni Association", city="Los Angeles",
                 category="networking", rules=rules) >= 55


def test_coaching_pitch_is_rejected_even_in_market(rules):
    """These are technically real estate events in Los Angeles. They are still noise."""
    assert score(title="Get Started Investing in Real Estate: Free Workshop",
                 org="Eventbrite", city="Los Angeles", category="panel",
                 rules=rules) < 55


def test_social_event_scores_below_a_substantive_one(rules):
    social = score(title="2026 Oktoberfest", org="NAIOP SoCal", city="Los Angeles",
                   category="networking", rules=rules)
    substantive = score(title="Capital Markets Outlook Panel", org="NAIOP SoCal",
                        city="Los Angeles", category="panel", rules=rules)

    assert social < substantive


def test_scoring_is_reproducible(rules):
    """Same inputs, same verdict — a digest you can diff is one you can trust."""
    args = {"title": "Self Storage Underwriting Roundtable", "org": "AIR CRE",
            "city": "Los Angeles", "category": "panel", "rules": rules}
    assert score_event(**args) == score_event(**args)


def test_reason_names_the_rules_that_fired(rules):
    verdict = score_event(title="Self Storage Lien Law Update",
                          org="California Self Storage Association",
                          city="Los Angeles", category="panel", rules=rules)

    assert "self storage" in verdict.reason
    assert "lien law" in verdict.reason
    assert "self storage" in verdict.topics


# --- Calibration against Shane's own verdict, 2026-08-26 --------------------
#
# He reviewed a 40-event digest and said two things were worth his time: the
# Raising & Investing Summit, and the week of Dec 7. Everything else was
# "garbage". These tests encode that judgment so a later rule change cannot
# quietly walk it back.


def test_the_summit_he_named_scores_at_the_top(rules):
    """The one event he singled out. If this drops below the floor, the scorer
    has lost the thread entirely."""
    assert score(title="Raising & Investing Summit 2.0 - Virtual (LA)",
                 org="Eventbrite", city="Online", category="conference",
                 rules=rules) >= 85


@pytest.mark.parametrize("title,org", [
    ("2026 CBPA CA Commercial Real Estate Summit", "NAIOP SoCal"),
    ("2026 Los Angeles Golf Tournament", "NAIOP SoCal"),
    ("2026 LA Holiday Networking Event", "California Self Storage Association"),
])
def test_week_of_dec_7_survives(title, org, rules):
    """The other thing he endorsed. Note the golf tournament and the holiday
    party: the room is the value, not the format."""
    assert score(title=title, org=org, city="Los Angeles",
                 category="networking", rules=rules) >= 55


@pytest.mark.parametrize("title,org", [
    ("Property Management Training Program - Spanish", "Apartment Association of Greater Los Angeles"),
    ("Rent Increases, Evictions, and New Regulations", "Apartment Association of Greater Los Angeles"),
    ("Debt Collection & Handling Rental Arrears", "Apartment Association of Greater Los Angeles"),
    ("Managing Seismic Upgrades and Building Approvals", "BOMA Greater Los Angeles"),
    ("Sustainability Policy Briefing", "BOMA Greater Los Angeles"),
    ("Ethics 800: Ethics for the Real Estate Manager", "IREM Greater Los Angeles"),
    ("Leadership Pathways for Property Professionals", "BOMA Greater Los Angeles"),
])
def test_operator_education_is_rejected(title, org, rules):
    """23 of the 40 he rejected were these. He is a principal — he hires people
    for every one of these subjects."""
    assert score(title=title, org=org, city="Los Angeles", category="panel",
                 rules=rules) < 55


@pytest.mark.parametrize("title", [
    "Real Estate Investor Network of West LA",
    "Real Estate Social by SGV REI MEET UP",
    "Los Angeles Learn about Real Estate Investors Training",
    "Live Deal Workshop: Analyzing Multifamily Properties",
])
def test_amateur_investor_meetups_are_kept(title, rules):
    """A room full of people buying property is worth being in, whatever their
    scale. The line is the sales funnel, not the size of the buyer."""
    assert score(title=title, org="Eventbrite", city="Los Angeles",
                 category="networking", rules=rules) >= 55


@pytest.mark.parametrize("title", [
    "Financial Freedom Through Passive Income: Free Workshop",
    "No Money Down Real Estate: Get Started Investing Today",
    "Real Estate Wealth Building Mastermind",
])
def test_the_coaching_funnel_is_still_rejected(title, rules):
    """The distinction that survives: an investor meetup is a room; a coaching
    pitch is a product."""
    assert score(title=title, org="Eventbrite", city="Los Angeles",
                 category="networking", rules=rules) < 55


def test_a_principal_social_beats_an_operator_seminar(rules):
    """The ordering that the whole rewrite turns on."""
    social = score(title="2026 Oktoberfest", org="NAIOP SoCal", city="Los Angeles",
                   category="networking", rules=rules)
    seminar = score(title="Fire & Life Safety Virtual Seminar",
                    org="BOMA Greater Los Angeles", city="Los Angeles",
                    category="panel", rules=rules)

    assert social > seminar


@pytest.mark.parametrize("title", [
    "National Data Center Investment Conference And Expo",
    "Central Region Data Center Construction, Design & Development",
    "International Life Sciences & Biotech Conference",
    "West Coast Data Center Operations & Cooling Conference",
])
def test_bisnows_national_calendar_is_filtered(title, rules):
    """Bisnow's LA feed carries its national calendar too, and those events score
    well on capital vocabulary. Scope and asset class are what reject them."""
    assert score(title=title, org="Bisnow", city=None, category="conference",
                 rules=rules) < 55


@pytest.mark.parametrize("title", [
    "Los Angeles CRE State of the Market Conference",
    "Both Sides of the Financing Table",
    "Southern California Multifamily Annual Conference",
])
def test_bisnows_la_conferences_surface(title, rules):
    """The reason Bisnow is worth scraping at all."""
    assert score(title=title, org="Bisnow", city="Los Angeles",
                 category="conference", rules=rules) >= 70
