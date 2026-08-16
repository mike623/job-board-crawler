"""Record every scan, however it was started.

"What has run lately?" needs one answer. Report files cannot give it: a scan that fails writes
no report, so the runs most worth seeing would be the ones missing.

So each scan records itself here, from inside its entrypoint — the same place the board lock
lives, and for the same reason. The cron and a terminal inherit it without knowing the
dashboard exists, and nothing outside this repo needs changing.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "outputs" / "state"
RUNS_FILE = STATE / "runs.json"

# Keep the file bounded; the report files remain the long-term record of what was found.
MAX_RECORDS = 500

RUNNING = "running"
DONE = "done"
FAILED = "failed"
BUSY = "busy"
INTERRUPTED = "interrupted"


def trigger() -> str:
    """Who started this scan.

    The dashboard says so explicitly. Otherwise an attached terminal means a person, and its
    absence means something scheduled.
    """
    declared = os.environ.get("JOB_CRAWLER_TRIGGER")
    if declared:
        return declared
    try:
        return "terminal" if sys.stdin.isatty() else "scheduled"
    except (AttributeError, ValueError):
        return "scheduled"


def load() -> list[dict]:
    try:
        data = json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save(records: list[dict]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    RUNS_FILE.write_text(json.dumps(records[-MAX_RECORDS:], indent=2), encoding="utf-8")


def put(record: dict) -> None:
    """Insert or update a record by id.

    Read-modify-write, because the dashboard may be updating its own rows at the same time.
    """
    records = load()
    for i, existing in enumerate(records):
        if existing.get("id") == record["id"]:
            records[i] = {**existing, **record}
            break
    else:
        records.append(record)
    save(records)


@contextmanager
def record(board: str, stamp: str):
    """Record a scan for its whole life, whatever happens to it.

    Yields a dict the scan can add findings to — `jobs` in particular — before it finishes.
    """
    entry = {
        "id": f"{stamp}-{board}",
        "board": board,
        "stamp": stamp,
        "status": RUNNING,
        "trigger": trigger(),
        "pid": os.getpid(),
        "started": datetime.now().isoformat(timespec="seconds"),
        "ended": "",
        "jobs": None,
        "searches": None,
        "exit_code": None,
    }
    put(entry)

    findings: dict = {}
    try:
        yield findings
    except SystemExit as stop:
        # A scan exits 75 when another process holds the board, and non-zero when no search
        # returned a usable page. Neither is a crash, and both are worth seeing.
        code = stop.code if isinstance(stop.code, int) else 1
        entry.update(status=BUSY if code == 75 else FAILED, exit_code=code)
        raise
    except BaseException:
        entry.update(status=FAILED, exit_code=1)
        raise
    else:
        entry.update(status=DONE, exit_code=0)
    finally:
        entry.update(findings)
        entry["ended"] = datetime.now().isoformat(timespec="seconds")
        put(entry)
