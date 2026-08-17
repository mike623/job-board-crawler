from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import haystack_pipeline

SPEC = {
    "title": "fullstack",
    "location": "london",
    "url": "https://haystack.cv/jobs?q=fullstack&location=london",
}

# Card structure taken from a captured search page, with the inline SVG paths and the generated
# utility classes trimmed. The second card is one that states no salary: the banknote icon and
# its value are simply absent, which is what makes positional parsing unsafe here.
CAPTURE = """\
<div class="space-y-4">
<a class="block group/card" href="/jobs/9a7874a5-d085-4193-9553-d880d4cf2353"><div><div>
  <h3 class="text-lg font-semibold">Senior Fullstack Engineer - Servicing, FinCrime</h3>
  <div><svg class="lucide lucide-building2 h-4 w-4"></svg><span role="link">Wise</span></div>
  <div><div><svg class="lucide lucide-map-pin h-4 w-4"></svg><span class="">London, UK</span>
    <img src="https://flagcdn.com/w40/gb.png" alt="GB flag"></div>
    <div><svg class="lucide lucide-banknote h-4 w-4"></svg><span>&pound;87,000 - &pound;111,000/yr</span></div>
    <div><svg class="lucide lucide-clock h-4 w-4"></svg><span>3 weeks ago</span></div></div>
  <div><div>Microservices</div><div>Spring</div><span>+7 more</span></div>
  <div><div>Technology</div></div>
</div><div title="Wise">WI</div></div></a>
<a class="block group/card" href="/jobs/13cda3f7-448c-4038-875e-5bb887c1fd3e"><div><div>
  <h3 class="text-lg font-semibold">Lead Full Stack Developer</h3>
  <div><svg class="lucide lucide-building2 h-4 w-4"></svg><span role="link">Hackajob</span></div>
  <div><div><svg class="lucide lucide-map-pin h-4 w-4"></svg><span class="">Leeds, Yorkshire</span></div>
    <div><svg class="lucide lucide-clock h-4 w-4"></svg><span>1 hour ago</span></div></div>
  <div><div>Full-time</div><div>Technology</div></div>
</div><div title="Hackajob">HL</div></div></a>
</div>
<a href="/jobs">Browse All Jobs</a>
<a href="/jobs?country=United%20Kingdom">United Kingdom</a>
"""


def test_cards_are_read_from_the_html_because_the_markdown_has_no_field_boundaries() -> None:
    leads = haystack_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert len(leads) == 2
    first = leads[0]
    assert first.role_title == "Senior Fullstack Engineer - Servicing, FinCrime"
    assert first.company == "Wise"
    assert first.location == "London, UK"
    assert first.salary == "£87,000 - £111,000/yr"
    assert first.posted == "3 weeks ago"
    assert first.job_id == "9a7874a5-d085-4193-9553-d880d4cf2353"
    assert first.url == "https://haystack.cv/jobs/9a7874a5-d085-4193-9553-d880d4cf2353"


def test_a_card_without_a_salary_leaves_the_field_blank_rather_than_shifting_the_next_one_in() -> None:
    second = haystack_pipeline.parse_search_cards(CAPTURE, SPEC)[1]

    assert second.company == "Hackajob"
    assert second.location == "Leeds, Yorkshire"
    assert second.salary == ""
    assert second.posted == "1 hour ago"


def test_navigation_links_are_not_mistaken_for_job_cards() -> None:
    # /jobs and /jobs?country=... sit in the same page; only a job UUID counts.
    assert all("/jobs/" in lead.url for lead in haystack_pipeline.parse_search_cards(CAPTURE, SPEC))


def test_a_failed_search_yields_no_leads_rather_than_an_exception() -> None:
    # Haystack answers a backend failure with a normal page carrying this message.
    error_page = f"<div><p>0 jobs found</p><p>{haystack_pipeline.SEARCH_ERROR}</p></div>"

    assert haystack_pipeline.parse_search_cards(error_page, SPEC) == []
