"""Start scans from the dashboard and follow them while they run.

A scan takes minutes, so it cannot happen inside a request. Each one is a subprocess running
exactly the command the cron runs — no reimplementation of the pipeline, no crawl4ai inside
the web process, and a crash takes the child rather than the dashboard.

Records and logs are persisted, so closing the browser or restarting the service does not
lose the history of what ran.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "outputs" / "state"
RUNS_FILE = STATE / "runs.json"
LOG_DIR = STATE / "logs"

# The exact commands the cron uses. Only scanning is exposed: anything that writes outside
# this project stays at the terminal.
COMMANDS = {
    "reed": ["reed_crawler/run_reed_scan.py", "--config", "config.yml"],
    "totaljobs": ["reed_crawler/totaljobs_pipeline.py", "scan", "--config", "config.yml"],
    "talent": ["reed_crawler/talent_pipeline.py", "scan", "--config", "config.yml"],
    "indeed": ["reed_crawler/indeed_pipeline.py", "scan", "--config", "config.yml", "--allow-disabled"],
}

RUNNING = "running"
DONE = "done"
FAILED = "failed"
BUSY = "busy"
INTERRUPTED = "interrupted"

BUSY_EXIT_CODE = 75
MAX_RECORDS = 200


@dataclass
class ScanRun:
    id: str
    board: str
    status: str = RUNNING
    started: str = ""
    ended: str = ""
    exit_code: int | None = None
    trigger: str = "dashboard"
    log: str = ""

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"{self.id}.log"

    @property
    def display_started(self) -> str:
        return self.started[:16].replace("T", " ")


def _load_raw() -> list[dict]:
    try:
        data = json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(records: list[dict]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    RUNS_FILE.write_text(json.dumps(records[-MAX_RECORDS:], indent=2), encoding="utf-8")


def _alive(pid) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reconcile() -> None:
    """Mark runs the service was watching when it stopped.

    Without this a killed dashboard leaves records showing as running for ever.
    """
    records = _load_raw()
    changed = False
    for record in records:
        if record.get("status") == RUNNING and not _alive(record.get("pid")):
            record["status"] = INTERRUPTED
            record["ended"] = record.get("ended") or datetime.now().isoformat(timespec="seconds")
            changed = True
    if changed:
        _save(records)


def history(limit: int = 20) -> list[ScanRun]:
    known = {f.name for f in ScanRun.__dataclass_fields__.values()}
    runs = [ScanRun(**{k: v for k, v in r.items() if k in known}) for r in _load_raw()]
    return list(reversed(runs))[:limit]


def get(run_id: str) -> ScanRun | None:
    return next((r for r in history(MAX_RECORDS) if r.id == run_id), None)


def _record(run: ScanRun, pid: int | None = None) -> None:
    records = _load_raw()
    payload = asdict(run)
    if pid is not None:
        payload["pid"] = pid
    for i, existing in enumerate(records):
        if existing.get("id") == run.id:
            records[i] = {**existing, **payload}
            break
    else:
        records.append(payload)
    _save(records)


class Scans:
    """Tracks the scans this process has started."""

    def __init__(self) -> None:
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    def board_is_running(self, board: str) -> bool:
        return any(r.board == board and r.status == RUNNING for r in history(MAX_RECORDS))

    async def start(self, board: str, trigger: str = "dashboard") -> ScanRun:
        if board not in COMMANDS:
            raise KeyError(board)

        stamp = datetime.now()
        run = ScanRun(
            id=f"{stamp.strftime('%Y%m%d-%H%M%S')}-{board}",
            board=board,
            started=stamp.isoformat(timespec="seconds"),
            trigger=trigger,
        )
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            run.log = str(run.log_path.relative_to(ROOT))
        except ValueError:
            # Logs need not live under the repo.
            run.log = str(run.log_path)

        process = await asyncio.create_subprocess_exec(
            sys.executable, *COMMANDS[board],
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self.processes[run.id] = process
        _record(run, pid=process.pid)
        asyncio.create_task(self._watch(run, process))
        return run

    async def _watch(self, run: ScanRun, process) -> None:
        """Drain the child's output to its log file until it exits."""
        try:
            with run.log_path.open("w", encoding="utf-8") as log:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    log.write(line.decode(errors="replace"))
                    log.flush()
            await process.wait()
        finally:
            run.exit_code = process.returncode
            run.ended = datetime.now().isoformat(timespec="seconds")
            if process.returncode == 0:
                run.status = DONE
            elif process.returncode == BUSY_EXIT_CODE:
                # The board's lock was held — by the cron, a terminal, or another trigger.
                run.status = BUSY
            else:
                run.status = FAILED
            _record(run)
            self.processes.pop(run.id, None)

    async def start_and_wait(self, board: str, trigger: str = "pool") -> ScanRun:
        """Start a scan and return once it has finished.

        The pool needs this: a slot released the moment the subprocess spawns would bound
        nothing at all.
        """
        run = await self.start(board, trigger)
        while True:
            current = get(run.id)
            if current is None or current.status != RUNNING:
                return current or run
            await asyncio.sleep(0.2)

    async def stream(self, run_id: str):
        """Server-sent events carrying the log as it is written.

        Reads the file rather than the pipe, so a reader arriving late still sees everything
        from the beginning, and several viewers can follow the same scan.
        """
        run = get(run_id)
        if run is None:
            yield "event: error\ndata: unknown run\n\n"
            return

        path = run.log_path
        for _ in range(100):  # the file appears a moment after the process starts
            if path.exists():
                break
            await asyncio.sleep(0.05)

        position = 0
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    for line in handle:
                        yield f"data: {line.rstrip()}\n\n"
                    position = handle.tell()

            current = get(run_id)
            if current is None or current.status != RUNNING:
                status = current.status if current else "unknown"
                yield f"event: finished\ndata: {status}\n\n"
                return
            await asyncio.sleep(0.5)


scans = Scans()
