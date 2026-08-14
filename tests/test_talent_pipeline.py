from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config
import talent_pipeline


class FakeResult:
    def __init__(self, markdown: str = "", links: dict | None = None) -> None:
        self.markdown = markdown
        self.links = links or {}


def test_talent_url_generation_uses_explicit_k_l_and_optional_id() -> None:
    # Asserts against config.example.yml, not config.yml: the real config is gitignored
    # and personal, so a fresh clone would have nothing to test against.
    cfg = board_config.load_config(ROOT / "config.example.yml")

    rows = board_config.build_board_urls(cfg, "talent")

    assert len(rows) == 2
    assert rows[0] == {
        "board": "talent",
        "title": "Senior Software Engineer",
        "location": "london",
        "url": "https://uk.talent.com/jobs?k=Senior+Software+Engineer&l=london&id=000000000000000000",
    }
    assert rows[1]["url"] == "https://uk.talent.com/jobs?k=Lead+Software+Engineer&l=london"


def test_talent_markdown_card_parser_extracts_job_metadata() -> None:
    markdown = """
# Senior Software Engineer Jobs in Leeds
## Software Engineer
Kerridge Commercial Systems•Leeds, Leeds, Leeds, GB
Full-time +1
At Klipboard we've introduced a flexible hybrid work policy... [Show more](https://uk.talent.com/view?id=611275213865225891)
Last updated: 30+ days ago
## Lead Software Engineer
Jet2.com and Jet2holidays•West Yorkshire, England
Full-time
This is an exciting role... [Show more](https://uk.talent.com/view?id=632580411604150232)
Last updated: 3 hours ago • New!
"""
    spec = {"title": "Senior Software Engineer", "location": "leeds", "url": "https://uk.talent.com/jobs?k=Senior+Software+Engineer&l=leeds"}

    leads = talent_pipeline.parse_markdown_cards(markdown, spec)

    assert len(leads) == 2
    assert leads[0].role_title == "Software Engineer"
    assert leads[0].company == "Kerridge Commercial Systems"
    assert leads[0].location == "Leeds, Leeds, Leeds, GB"
    assert leads[0].job_id == "611275213865225891"
    assert leads[1].role_title == "Lead Software Engineer"


def test_talent_parse_result_prefers_markdown_cards_over_show_more_links() -> None:
    markdown = """
## Lead Software Engineer
Informed Solutions•Leeds, England, GB
Full-time
Quick Apply
Make a difference... [Show more](https://uk.talent.com/view?id=619894814244674762)
Last updated: 30+ days ago
"""
    result = FakeResult(
        markdown=markdown,
        links={"internal": [{"href": "/view?id=619894814244674762", "text": "Show more"}]},
    )
    spec = {"title": "Senior Software Engineer", "location": "leeds", "url": "https://uk.talent.com/jobs?k=Senior+Software+Engineer&l=leeds"}

    leads = talent_pipeline.parse_result(result, spec)

    assert len(leads) == 1
    assert leads[0].role_title == "Lead Software Engineer"
    assert leads[0].company == "Informed Solutions"
