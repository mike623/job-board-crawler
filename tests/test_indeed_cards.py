from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import indeed_pipeline
import salary

SPEC = {"title": "senior software engineer", "location": "leeds",
        "url": "https://uk.indeed.com/jobs?q=senior+software+engineer&l=leeds"}

# Reduced from a captured search page: a full card, one with no salary, and one whose only
# attribute chips are perks rather than employment terms.
CAPTURE = """
<div class="job_seen_beacon">
  <h2><a data-jk="39fd011d04788f60" href="/pagead/clk?mo=r&amp;ad=xyz"><span>Senior Python Developer</span></a></h2>
  <span data-testid="company-name">HippoDigital</span>
  <div data-testid="text-location">Hybrid work in Leeds LS1 4HT</div>
  <div data-testid="attribute_snippet_testid" class="salary-snippet-container">£57,500 - £72,000 a year</div>
  <div data-testid="attribute_snippet_testid">Permanent</div>
  <div data-testid="attribute_snippet_testid">Disability confident</div>
</div>
<div class="job_seen_beacon">
  <h2><a data-jk="1a0a760ec4e98127" href="/pagead/clk?mo=r&amp;ad=abc"><span>Senior Python Developer</span></a></h2>
  <span data-testid="company-name">CGI</span>
  <div data-testid="text-location">Leeds</div>
  <div data-testid="attribute_snippet_testid">Full-time + 1</div>
</div>
<div class="job_seen_beacon">
  <h2><a data-jk="228c9acb62c60191" href="/pagead/clk?mo=r&amp;ad=def"><span>Software Engineer</span></a></h2>
  <span data-testid="company-name">SThree</span>
  <div data-testid="text-location">Leeds</div>
  <div data-testid="attribute_snippet_testid" class="salary-snippet-container">£600 - £800 a day</div>
  <div data-testid="attribute_snippet_testid">Flexitime</div>
</div>
"""


def test_cards_are_read_from_the_html_where_the_job_id_lives():
    # Card links are pagead click wrappers carrying no job id; only the HTML has data-jk.
    leads = indeed_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert [x.job_id for x in leads] == ["39fd011d04788f60", "1a0a760ec4e98127", "228c9acb62c60191"]


def test_a_full_card_yields_every_displayed_field():
    first = indeed_pipeline.parse_search_cards(CAPTURE, SPEC)[0]

    assert first.role_title == "Senior Python Developer"
    assert first.company == "HippoDigital"
    assert first.location == "Hybrid work in Leeds LS1 4HT"
    assert first.salary == "£57,500 - £72,000 a year"
    assert first.contract == "Permanent"


def test_the_url_is_canonical_rather_than_a_click_wrapper():
    first = indeed_pipeline.parse_search_cards(CAPTURE, SPEC)[0]

    assert first.url == "https://uk.indeed.com/viewjob?jk=39fd011d04788f60"
    assert "pagead" not in first.url


def test_a_card_without_a_salary_leaves_it_blank():
    second = indeed_pipeline.parse_search_cards(CAPTURE, SPEC)[1]

    assert second.salary == ""
    assert second.contract == "Full-time + 1"


def test_a_perk_chip_is_not_mistaken_for_the_contract():
    third = indeed_pipeline.parse_search_cards(CAPTURE, SPEC)[2]

    assert third.contract == "", "Flexitime is a perk, not an employment term"
    assert third.salary == "£600 - £800 a day"


def test_no_posted_date_is_invented():
    # Indeed does not show one on its search cards.
    assert all(x.posted == "" for x in indeed_pipeline.parse_search_cards(CAPTURE, SPEC))


def test_cards_without_a_job_id_are_skipped():
    html = '<div class="job_seen_beacon"><h2><a href="/x">No id here</a></h2></div>'

    assert indeed_pipeline.parse_search_cards(html, SPEC) == []


def test_empty_html_yields_nothing():
    assert indeed_pipeline.parse_search_cards("", SPEC) == []
    assert indeed_pipeline.parse_search_cards("<html></html>", SPEC) == []


def test_parse_result_falls_back_to_links_when_no_cards_are_recognised():
    class FakeResult:
        html = "<html>no cards</html>"
        links = {"internal": [{"href": "https://uk.indeed.com/viewjob?jk=fallback123", "text": "A Role"}]}

    leads = indeed_pipeline.parse_result(FakeResult(), SPEC)

    assert len(leads) == 1
    assert leads[0].job_id == "fallback123"


def test_indeeds_salary_phrasing_parses():
    # Indeed writes "a year" and "an hour" where other boards write "per annum".
    assert salary.parse_salary("£57,500 - £72,000 a year")["salary_period"] == "year"
    assert salary.parse_salary("£600 - £800 a day")["salary_period"] == "day"
    assert salary.parse_salary("£37.27 - £74.54 an hour")["salary_period"] == "hour"
