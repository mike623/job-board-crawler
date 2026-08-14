from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard import aggregate
from dashboard.app import app


def write_run(outputs: Path, board: str, stamp: str, rows: list[dict]) -> None:
    reports = outputs / board / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{board}_deduped_{stamp}.json").write_text(json.dumps(rows), encoding="utf-8")


def job(job_id: str, title="leeds dev", location="leeds", **extra) -> dict:
    return {"job_id": job_id, "search_title": title, "search_location": location,
            "role_title": f"Role {job_id}", **extra}


def test_a_board_that_was_never_scanned_reports_nothing_rather_than_erroring(tmp_path):
    summary = aggregate.summarise_board("reed", tmp_path)

    assert summary.scanned is False
    assert (summary.known, summary.live, summary.runs) == (0, 0, 0)
    assert summary.last_run_display == "never"


def test_a_job_absent_from_the_latest_run_of_its_search_is_vanished(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1"), job("2")])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("1")])

    summary = aggregate.summarise_board("reed", tmp_path)

    assert summary.known == 2
    assert summary.live == 1
    assert summary.vanished == 1


def test_a_partial_run_does_not_make_the_rest_of_the_board_look_vanished(tmp_path):
    # The regression this guards: a --limit smoke test, or a max_pages_per_run cap, covers only
    # some searches. Judging liveness against the board's last run would bury everything else.
    write_run(tmp_path, "reed", "2026-08-01_090000", [
        job("leeds-1", location="leeds"), job("manc-1", location="manchester"),
    ])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("leeds-1", location="leeds")])

    summary = aggregate.summarise_board("reed", tmp_path)

    assert summary.live == 2, "the Manchester job was never re-checked, so it is not vanished"
    assert summary.vanished == 0


def test_a_job_dropped_by_a_later_run_of_its_own_search_is_vanished(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [
        job("leeds-1", location="leeds"), job("manc-1", location="manchester"),
    ])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("manc-2", location="manchester")])

    summary = aggregate.summarise_board("reed", tmp_path)

    assert summary.live == 2      # leeds-1 unre-checked, manc-2 fresh
    assert summary.vanished == 1  # manc-1 was re-checked and had gone


def test_first_and_last_seen_and_the_sighting_count(tmp_path):
    for stamp in ["2026-08-01_090000", "2026-08-02_090000", "2026-08-03_090000"]:
        write_run(tmp_path, "reed", stamp, [job("1")])

    (only,) = aggregate.jobs_for_board("reed", tmp_path)

    assert only.first_seen == "2026-08-01_090000"
    assert only.last_seen == "2026-08-03_090000"
    assert only.times_seen == 3
    assert only.live is True


def test_a_later_blank_never_erases_a_value_an_earlier_run_captured(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1", company="Acme", salary_max=70000)])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("1", company="", salary_max=None)])

    (only,) = aggregate.jobs_for_board("reed", tmp_path)

    assert only.company == "Acme"
    assert only.pay == 70000


def test_new_counts_only_jobs_first_seen_in_the_latest_run(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("old")])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("old"), job("fresh")])

    assert aggregate.summarise_board("reed", tmp_path).new == 1


def test_unreadable_report_files_are_skipped_rather_than_crashing(tmp_path):
    reports = tmp_path / "reed" / "reports"
    reports.mkdir(parents=True)
    (reports / "reed_deduped_2026-08-01_090000.json").write_text("{ not json", encoding="utf-8")

    assert aggregate.summarise_board("reed", tmp_path).known == 0


def test_rows_without_a_job_id_are_ignored(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1"), {"job_id": "", "role_title": "junk"}])

    assert aggregate.summarise_board("reed", tmp_path).known == 1


@pytest.fixture
def client():
    return TestClient(app)


def test_the_landing_page_renders(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "jobs known" in body
    assert "still live" in body
    for board in aggregate.BOARDS:
        assert board in body


def test_no_api_docs_are_exposed(client):
    # Nothing here is a public API; the schema endpoints are noise on a single-user tool.
    assert client.get("/docs").status_code == 404
