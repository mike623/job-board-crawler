"""Read the crawler's board locks so the dashboard can show what is already scanning.

The lock is owned by the scan entrypoints; this is only ever a look.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reed_crawler"))

import scan_lock


def board_lock_state(board: str) -> dict | None:
    """Who is scanning this board right now, if anyone — the cron included."""
    return scan_lock.holder_of(board)
