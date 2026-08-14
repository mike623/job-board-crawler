from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import totaljobs_pipeline

SPEC = {
    "title": "senior software engineer",
    "location": "sheffield",
    "url": "https://www.totaljobs.com/jobs/senior-software-engineer/in-sheffield?radius=30",
}

# Shapes taken verbatim from captured search pages: a full card, a card whose salary slot reads
# "Unspecified", a card carrying a badge after the posted date, and a logo line belonging to the
# card that follows it.
CAPTURE = """\
## [Senior Software Engineer Java](https://www.totaljobs.com/job/senior-software-engineer-java/get2talent-job107822162)
Get2Talent
Leeds (LS1)
From £55,000 to £85,000 per annum Plus benefits
Are you an experienced Senior Software Engineer or Technical Lead who enjoys remaining hands-on?
more
3 days ago
[![Nexperia](https://tjgliveassets.s3.eu-west-1.amazonaws.com/company-logos/abc.png)](https://www.totaljobs.com/jobs/nexperia)
## [Senior Application Engineer](https://www.totaljobs.com/job/senior-application-engineer/nexperia-job107123456)
Nexperia
Marcliffe Ind Est, Stockport (SK7), SK7 5BJ
Unspecified
At Nexperia Manchester, we are expanding our Applications team.
more
2 weeks ago
PREMIUM
## [Senior Software Engineer - C# & Delphi](https://www.totaljobs.com/job/senior-software-engineer-c/luxion-group-limited-job107660811)
Luxion Group Limited
Skelmanthorpe, Huddersfield (HD8), HD8 9UG
55000
Senior Software Engineer - C# & DelphiTitle:Department:Procode IT
more
1 month ago
FEATURED
"""


def test_cards_yield_the_fields_the_link_graph_could_never_recover() -> None:
    leads = totaljobs_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert len(leads) == 3
    first = leads[0]
    assert first.role_title == "Senior Software Engineer Java"
    assert first.company == "Get2Talent"
    assert first.location == "Leeds (LS1)"
    assert first.salary == "From £55,000 to £85,000 per annum Plus benefits"
    assert first.posted == "3 days ago"
    assert first.job_id == "107822162"


def test_unspecified_salary_becomes_blank_rather_than_the_placeholder_text() -> None:
    leads = totaljobs_pipeline.parse_search_cards(CAPTURE, SPEC)

    nexperia = leads[1]
    assert nexperia.company == "Nexperia"
    assert nexperia.salary == ""
    assert nexperia.posted == "2 weeks ago"


def test_badges_are_not_mistaken_for_the_posted_date() -> None:
    leads = totaljobs_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert leads[1].posted == "2 weeks ago"  # card is followed by PREMIUM
    assert leads[2].posted == "1 month ago"  # card is followed by FEATURED


def test_a_logo_line_is_not_read_as_the_next_cards_company() -> None:
    # The logo markup for card N+1 is emitted after card N's body.
    leads = totaljobs_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert leads[1].company == "Nexperia"
    assert "![" not in leads[0].posted


def test_a_short_card_leaves_salary_blank_instead_of_borrowing_the_snippet() -> None:
    short = """\
## [Contract Developer](https://www.totaljobs.com/job/contract-developer/acme-job107999999)
Acme Ltd
Sheffield (S1)
We are looking for a contractor to join us.
more
5 days ago
"""
    (lead,) = totaljobs_pipeline.parse_search_cards(short, SPEC)

    assert lead.company == "Acme Ltd"
    assert lead.location == "Sheffield (S1)"
    assert lead.salary == ""
    assert lead.posted == "5 days ago"


def test_non_job_headings_are_ignored() -> None:
    noise = """\
## [Browse jobs by location](https://www.totaljobs.com/jobs/in-sheffield)
Some category blurb
more
today
"""
    assert totaljobs_pipeline.parse_search_cards(noise, SPEC) == []


def test_parse_result_falls_back_to_the_link_graph_when_no_cards_are_present() -> None:
    class FakeResult:
        markdown = "no job cards here"
        links = {"internal": [{
            "href": "/job/senior-developer/acme-job107766010",
            "text": "Senior Developer",
        }]}

    leads = totaljobs_pipeline.parse_result(FakeResult(), SPEC)

    assert len(leads) == 1
    assert leads[0].role_title == "Senior Developer"
    assert leads[0].job_id == "107766010"
