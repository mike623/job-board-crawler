from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import run_record
from dashboard import aggregate


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(run_record, "STATE", tmp_path)
    monkeypatch.setattr(run_record, "RUNS_FILE", tmp_path / "runs.json")
    return tmp_path


def stored():
    return run_record.load()


# ---- recording ----

def test_a_completed_scan_is_recorded_with_what_it_found():
    with run_record.record("reed", "2026-08-16_090000") as findings:
        findings.update(jobs=42, searches=8)

    (entry,) = stored()
    assert entry["board"] == "reed"
    assert entry["status"] == run_record.DONE
    assert entry["exit_code"] == 0
    assert (entry["jobs"], entry["searches"]) == (42, 8)
    assert entry["ended"]


def test_a_scan_is_visible_as_running_before_it_finishes():
    with run_record.record("reed", "2026-08-16_090000"):
        assert stored()[0]["status"] == run_record.RUNNING


def test_a_crashing_scan_is_recorded_rather_than_vanishing():
    with pytest.raises(RuntimeError):
        with run_record.record("reed", "2026-08-16_090000"):
            raise RuntimeError("the crawler fell over")

    assert stored()[0]["status"] == run_record.FAILED


def test_a_scan_that_found_no_usable_page_is_recorded_as_failed():
    # scan_health raises SystemExit when no search returned a usable page.
    with pytest.raises(SystemExit):
        with run_record.record("totaljobs", "2026-08-16_090000"):
            raise SystemExit("no search returned a usable page")

    assert stored()[0]["status"] == run_record.FAILED


def test_a_scan_blocked_by_the_board_lock_is_recorded_as_busy_not_failed():
    with pytest.raises(SystemExit):
        with run_record.record("reed", "2026-08-16_090000"):
            raise SystemExit(75)

    entry = stored()[0]
    assert entry["status"] == run_record.BUSY
    assert entry["exit_code"] == 75


def test_records_are_keyed_by_run_so_updates_replace_rather_than_duplicate():
    run_record.put({"id": "x", "board": "reed", "status": "running"})
    run_record.put({"id": "x", "status": "done", "jobs": 3})

    (entry,) = stored()
    assert (entry["status"], entry["jobs"], entry["board"]) == ("done", 3, "reed")


def test_the_file_is_bounded(monkeypatch):
    monkeypatch.setattr(run_record, "MAX_RECORDS", 5)
    for i in range(12):
        run_record.put({"id": f"run-{i:02d}", "board": "reed"})

    kept = stored()
    assert len(kept) == 5
    assert kept[-1]["id"] == "run-11", "the newest must survive"


def test_a_corrupt_file_does_not_stop_a_scan_recording(isolated):
    (isolated / "runs.json").write_text("{ truncated", encoding="utf-8")

    with run_record.record("reed", "2026-08-16_090000"):
        pass

    assert stored()[0]["status"] == run_record.DONE


# ---- trigger ----

def test_the_dashboard_declares_itself(monkeypatch):
    monkeypatch.setenv("JOB_CRAWLER_TRIGGER", "dashboard")

    assert run_record.trigger() == "dashboard"


def test_without_a_declaration_an_attached_terminal_means_a_person(monkeypatch):
    monkeypatch.delenv("JOB_CRAWLER_TRIGGER", raising=False)
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": lambda self: True})())

    assert run_record.trigger() == "terminal"


def test_and_its_absence_means_something_scheduled(monkeypatch):
    monkeypatch.delenv("JOB_CRAWLER_TRIGGER", raising=False)
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": lambda self: False})())

    assert run_record.trigger() == "scheduled"


# ---- the identity handed down ----

def test_a_caller_can_hand_the_run_stamp_down():
    # The dashboard needs the id before the scan starts, so it can redirect and attach a log.
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'reed_crawler')!r});"
         "import board_config; print(board_config.run_stamp())"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "JOB_CRAWLER_RUN_STAMP": "2026-01-01_120000"},
    )

    assert result.stdout.strip() == "2026-01-01_120000"


# ---- the merged view ----

class FakeRecord:
    def __init__(self, id, board, status="done", trigger="scheduled", jobs=0, searches=0, has_log=False):
        self.id, self.board, self.status, self.trigger = id, board, status, trigger
        self.jobs, self.searches, self.has_log = jobs, searches, has_log


def test_a_failed_scan_appears_even_though_it_wrote_no_report(tmp_path):
    # The whole reason report files cannot be the single source.
    merged = aggregate.runs(outputs=tmp_path, recorded=[
        FakeRecord("2026-08-16_090000-reed", "reed", status="failed"),
    ])

    assert len(merged) == 1
    assert merged[0].status == "failed"
    assert merged[0].board == "reed"


def test_a_record_and_its_report_are_one_row_not_two(tmp_path):
    from test_dashboard import job, write_run
    write_run(tmp_path, "reed", "2026-08-16_090000", [job("1"), job("2")])

    merged = aggregate.runs(outputs=tmp_path, recorded=[
        FakeRecord("2026-08-16_090000-reed", "reed", status="done", trigger="dashboard"),
    ])

    assert len(merged) == 1
    assert merged[0].jobs == 2          # from the report
    assert merged[0].trigger == "dashboard"   # from the record


def test_runs_predating_the_records_are_still_listed(tmp_path):
    from test_dashboard import job, write_run
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1")])

    merged = aggregate.runs(outputs=tmp_path, recorded=[])

    assert len(merged) == 1
    assert merged[0].status == ""       # nothing recorded its outcome


def test_stamp_shapes_from_before_and_after_the_change_both_read_as_dates():
    assert aggregate.format_stamp("20260815-001116") == "2026-08-15 00:11"
    assert aggregate.format_stamp("2026-08-16_083607") == "2026-08-16 08:36"
    assert aggregate.format_stamp("nonsense") == "nonsense"


def test_runs_are_ordered_by_time_across_both_stamp_shapes(tmp_path):
    merged = aggregate.runs(outputs=tmp_path, recorded=[
        FakeRecord("20260815-001116-talent", "talent"),
        FakeRecord("2026-08-16_083607-reed", "reed"),
    ])

    assert [r.board for r in merged] == ["reed", "talent"]
