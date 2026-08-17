"""One scan per board at a time.

Two scans of the same board at once double that host's request rate — defeating the delays
that exist to avoid being blocked — and race each other writing captures.

The lock lives here, in the scan entrypoints, rather than in whatever starts them. That way
the external cron, a terminal, and the dashboard all observe it without any of them needing to
know the others exist.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "outputs" / "state" / "locks"

# Sysexits' EX_TEMPFAIL: a caller can tell "someone else is scanning" from a real failure.
BUSY_EXIT_CODE = 75


class BoardBusy(SystemExit):
    """Raised when another live process is already scanning this board.

    SystemExit prints its argument only when that argument is not an integer, and the exit
    status has to be the integer for a caller to distinguish "busy" from a crash. So the
    message is written out here and the exception carries the status.
    """

    def __init__(self, board: str, holder: dict):
        self.message = (
            f"{board}: a scan is already running (pid {holder.get('pid')}, "
            f"started {holder.get('started', 'an unknown time')}). Refusing to start a second one — "
            f"concurrent scans double the request rate this board is throttled to avoid."
        )
        print(self.message, file=sys.stderr)
        super().__init__(BUSY_EXIT_CODE)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@contextmanager
def hold(board: str, lock_dir: Path = LOCK_DIR):
    """Hold the lock for a board, or raise BoardBusy if another live process has it.

    A lock left behind by a process that died is reclaimed rather than blocking forever.
    """
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{board}.lock"
    payload = json.dumps({
        "board": board,
        "pid": os.getpid(),
        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    while True:
        try:
            # O_EXCL makes the create-or-fail decision atomic between competing processes.
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _read(path)
            if holder and isinstance(holder.get("pid"), int) and _alive(holder["pid"]):
                raise BoardBusy(board, holder)
            # Stale: the holder is gone, or the file is unreadable. Clear it and retry once.
            print(f"{board}: clearing a stale lock left by pid {(holder or {}).get('pid', '?')}")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        else:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
            break

    try:
        yield path
    finally:
        # Released on success, on failure, and on interrupt.
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def holder_of(board: str, lock_dir: Path = LOCK_DIR) -> dict | None:
    """Who currently holds the lock, if anyone. Used by the dashboard to show board state."""
    holder = _read(lock_dir / f"{board}.lock")
    if holder and isinstance(holder.get("pid"), int) and _alive(holder["pid"]):
        return holder
    return None
