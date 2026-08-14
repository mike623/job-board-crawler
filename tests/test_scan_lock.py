from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import scan_lock


def test_the_lock_is_created_while_held_and_removed_after(tmp_path):
    path = tmp_path / "reed.lock"

    with scan_lock.hold("reed", tmp_path):
        assert path.exists()
        holder = json.loads(path.read_text())
        assert holder["pid"] == os.getpid()
        assert holder["board"] == "reed"

    assert not path.exists()


def test_a_second_scan_of_the_same_board_is_refused(tmp_path):
    with scan_lock.hold("reed", tmp_path):
        with pytest.raises(scan_lock.BoardBusy) as raised:
            with scan_lock.hold("reed", tmp_path):
                pytest.fail("the second acquisition should not have succeeded")

    assert raised.value.code == scan_lock.BUSY_EXIT_CODE
    assert "already running" in raised.value.message


def test_different_boards_do_not_block_each_other(tmp_path):
    with scan_lock.hold("reed", tmp_path):
        with scan_lock.hold("totaljobs", tmp_path):
            assert (tmp_path / "reed.lock").exists()
            assert (tmp_path / "totaljobs.lock").exists()


def test_a_lock_left_by_a_dead_process_is_reclaimed(tmp_path):
    # PID 1 exists, so fake a pid that cannot: one beyond the system maximum.
    (tmp_path / "reed.lock").write_text(json.dumps({"board": "reed", "pid": 2 ** 30, "started": "earlier"}))

    with scan_lock.hold("reed", tmp_path):
        assert json.loads((tmp_path / "reed.lock").read_text())["pid"] == os.getpid()


def test_an_unreadable_lock_file_is_reclaimed_rather_than_blocking_forever(tmp_path):
    (tmp_path / "reed.lock").write_text("{ truncated")

    with scan_lock.hold("reed", tmp_path):
        assert json.loads((tmp_path / "reed.lock").read_text())["pid"] == os.getpid()


def test_the_lock_is_released_when_the_scan_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with scan_lock.hold("reed", tmp_path):
            raise RuntimeError("crawl blew up")

    assert not (tmp_path / "reed.lock").exists()
    with scan_lock.hold("reed", tmp_path):
        pass  # reacquirable


def test_holder_of_reports_the_live_holder_and_ignores_a_dead_one(tmp_path):
    assert scan_lock.holder_of("reed", tmp_path) is None

    with scan_lock.hold("reed", tmp_path):
        assert scan_lock.holder_of("reed", tmp_path)["pid"] == os.getpid()

    (tmp_path / "reed.lock").write_text(json.dumps({"board": "reed", "pid": 2 ** 30}))
    assert scan_lock.holder_of("reed", tmp_path) is None


def test_a_genuinely_separate_process_is_refused(tmp_path):
    """The case that matters: the cron and the dashboard are different processes."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'reed_crawler')!r});"
         f"import scan_lock, time, pathlib;"
         f"ctx = scan_lock.hold('reed', pathlib.Path({str(tmp_path)!r}));"
         f"ctx.__enter__(); print('held', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"

        with pytest.raises(scan_lock.BoardBusy) as raised:
            with scan_lock.hold("reed", tmp_path):
                pytest.fail("acquired a lock another live process holds")

        assert str(holder.pid) in raised.value.message
    finally:
        holder.kill()
        holder.wait()
