from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import salary


def parsed(text):
    r = salary.parse_salary(text)
    return r["salary_min"], r["salary_max"], r["salary_period"]


# Every shape below was taken from salary text the boards actually returned.
@pytest.mark.parametrize("text,expected", [
    # Plain ranges, the overwhelming majority.
    ("£45,000 - £55,000 per annum", (45000, 55000, "year")),
    ("£57946 - £80664 per annum +", (57946, 80664, "year")),
    ("£500 - £600 per day", (500, 600, "day")),
    ("£45 - £60 per hour", (45, 60, "hour")),
    # Trailing prose after the range must not move the bounds.
    ("£60,000 - £70,000 per annum, OTE, inc benefits, negotiable", (60000, 70000, "year")),
    ("£45,000 - £55,000 per annum, inc benefits, pro-rata", (45000, 55000, "year")),
    # "From X to Y" is still a range.
    ("From £55,000 to £85,000 per annum Plus benefits", (55000, 85000, "year")),
    ("From £65,000 to £85,000 per annum", (65000, 85000, "year")),
    # Single values.
    ("£45,000 per annum", (45000, 45000, "year")),
    ("£450 per day", (450, 450, "day")),
    # Aggregators suffix the unit instead of spelling the period out.
    ("£87,000 - £111,000/yr", (87000, 111000, "year")),
    ("£14 - £16/h", (14, 16, "hour")),
    ("£90,000/yr", (90000, 90000, "year")),
    ("£395/d", (395, 395, "day")),
    ("£3,500/mo", (3500, 3500, "month")),
])
def test_observed_formats(text, expected):
    assert parsed(text) == expected


def test_k_shorthand_on_only_the_first_value_is_inherited_by_the_second():
    # "£70k - 85k" means 70,000-85,000, not 70,000-85.
    assert parsed("£70k - 85k per year + Share options") == (70000, 85000, "year")
    assert parsed("£65k - 75k per year + benefits") == (65000, 75000, "year")
    assert parsed("£50k - £85k per year") == (50000, 85000, "year")


def test_contractor_rate_abbreviations():
    # Contractor adverts rarely spell out "per day".
    assert parsed("£395p/d Inside IR35") == (395, 395, "day")
    assert parsed("£550 p/d outside IR35") == (550, 550, "day")
    assert parsed("£55ph") == (55, 55, "hour")
    assert parsed("£90,000 p.a.") == (90000, 90000, "year")


def test_a_range_with_no_currency_symbol_or_spacing():
    assert parsed("71,250-118,000 Annual") == (71250, 118000, "year")


def test_only_the_first_range_is_taken_when_the_text_lists_more_numbers():
    # The trailing "Up to £55000+" is benefits prose, not a wider band.
    assert parsed("£47000 - £55000 per annum + Up to £55000+ Excellent Benefits") == (47000, 55000, "year")


def test_a_ceiling_has_no_minimum():
    assert parsed("Up to £80,000 DOE") == (None, 80000, "year")
    assert parsed("Up to £65,000 per annum") == (None, 65000, "year")


def test_a_floor_has_no_maximum():
    assert parsed("From £55,000, dependent on experience") == (55000, None, "year")


def test_percentages_are_not_mistaken_for_pay():
    assert parsed("£45,000 - £55,000 per annum + 10% employer pension") == (45000, 55000, "year")


def test_bare_numbers_are_read_as_annual_when_large_enough_to_be_unambiguous():
    assert parsed("55000") == (55000, 55000, "year")
    assert parsed("48842.00") == (48842, 48842, "year")


def test_a_small_bare_number_leaves_the_period_unstated_rather_than_guessing():
    # 450 could be a day rate or an hourly one; the text does not say.
    assert parsed("450") == (450, 450, "")


def test_text_without_a_number_yields_nothing():
    for text in ["Competitive", "Competitive salary", "Salary negotiable", "Negotiable", "Training Course"]:
        assert parsed(text) == (None, None, ""), text


def test_missing_and_empty_input():
    assert parsed(None) == (None, None, "")
    assert parsed("") == (None, None, "")
    assert parsed("   ") == (None, None, "")


def test_an_inverted_range_is_normalised():
    assert parsed("£70,000 - £50,000 per annum") == (50000, 70000, "year")


def test_apply_to_sets_the_fields_on_a_lead_in_place():
    class Lead:
        salary = "£60,000 - £70,000 per annum"
        salary_min = None
        salary_max = None
        salary_period = ""

    lead = Lead()
    salary.apply_to(lead)

    assert (lead.salary_min, lead.salary_max, lead.salary_period) == (60000, 70000, "year")
    assert lead.salary == "£60,000 - £70,000 per annum", "original text must be preserved"
