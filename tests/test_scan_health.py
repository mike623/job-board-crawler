from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import scan_health


class Result:
    def __init__(self, success=True, markdown="", html=""):
        self.success = success
        self.markdown = markdown
        self.html = html
        self.status_code = 200 if success else 503
        self.error_message = None


def page(chars=5000):
    return "x" * chars


def test_a_real_page_is_usable():
    assert scan_health.classify(Result(markdown=page())) == scan_health.OK


def test_the_observed_failure_a_successful_fetch_with_nothing_in_it():
    # Live occurrence: status 200, success True, zero bytes of markdown and HTML, zero jobs.
    # The identical search retried immediately returned 25 results.
    assert scan_health.classify(Result(markdown="", html="")) == scan_health.EMPTY


def test_a_near_empty_body_is_also_treated_as_empty():
    assert scan_health.classify(Result(markdown="   \n  ", html="<html></html>")) == scan_health.EMPTY


def test_an_unsuccessful_fetch_is_a_failure_not_an_empty_body():
    assert scan_health.classify(Result(success=False)) == scan_health.FAILED


def test_html_alone_is_enough_to_count_as_a_page():
    # Some boards render to HTML that the markdown extractor makes little of.
    assert scan_health.classify(Result(markdown="", html=page())) == scan_health.OK


def test_a_search_that_matched_nothing_is_still_a_successful_run(capsys):
    # The distinction this whole module exists for: a real page listing no vacancies is fine.
    health = scan_health.RunHealth("reed")
    health.record(Result(markdown=page()))

    health.finish()  # must not raise

    assert "1/1 searches returned a usable page" in capsys.readouterr().out


def test_a_run_where_every_search_came_back_empty_fails(capsys):
    health = scan_health.RunHealth("totaljobs")
    for _ in range(3):
        health.record(Result(markdown=""))

    with pytest.raises(SystemExit) as raised:
        health.finish()

    assert "no search returned a usable page" in str(raised.value)
    assert "0/3" in capsys.readouterr().out


def test_one_good_search_is_enough_for_the_run_to_stand(capsys):
    health = scan_health.RunHealth("reed")
    health.record(Result(markdown=page()))
    health.record(Result(markdown=""))
    health.record(Result(success=False))

    health.finish()  # must not raise

    out = capsys.readouterr().out
    assert "1/3 searches returned a usable page" in out
    assert "1 empty" in out
    assert "1 failed" in out


def test_a_run_with_no_searches_at_all_is_not_reported_as_broken():
    health = scan_health.RunHealth("indeed")

    assert health.all_broken is False
    health.finish()


def test_outcomes_are_visible_per_search_not_just_in_the_total():
    health = scan_health.RunHealth("reed")

    assert health.record(Result(markdown=page())) == scan_health.OK
    assert health.record(Result(markdown="")) == scan_health.EMPTY
    assert health.record(Result(success=False)) == scan_health.FAILED
