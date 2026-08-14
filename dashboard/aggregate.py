"""Derive the current state of the crawl from the report files on disk.

There is no stored index. Everything here is recomputed per request from the deduped report
JSON each scan writes, so the dashboard can never disagree with what the crawler actually
produced.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
BOARDS = ["reed", "totaljobs", "indeed", "talent"]

_STAMP = re.compile(r"_(\d{4}-\d{2}-\d{2}_\d{6})\.json$")

# Fields worth carrying onto the aggregated job; anything else stays in the report file.
_CARRIED = (
    "role_title", "company", "location", "salary", "salary_min", "salary_max",
    "salary_period", "contract", "posted", "url", "search_title", "search_location",
)


@dataclass
class Job:
    board: str
    job_id: str
    first_seen: str
    last_seen: str
    times_seen: int
    live: bool
    fields: dict = field(default_factory=dict)

    def get(self, name, default=""):
        return self.fields.get(name) or default

    @property
    def role_title(self) -> str:
        return self.get("role_title", "Unknown role")

    @property
    def company(self) -> str:
        return self.get("company", "")

    @property
    def pay(self) -> int | None:
        return self.fields.get("salary_max") or self.fields.get("salary_min")


@dataclass
class BoardSummary:
    board: str
    scanned: bool = False
    known: int = 0
    live: int = 0
    vanished: int = 0
    new: int = 0
    runs: int = 0
    last_run: str = ""
    last_run_jobs: int = 0

    @property
    def last_run_display(self) -> str:
        if not self.last_run:
            return "never"
        date, time = self.last_run.split("_")
        return f"{date} {time[:2]}:{time[2:4]}"


def deduped_reports(board: str, outputs: Path = OUTPUTS) -> list[tuple[str, Path]]:
    """Every deduped report for a board, oldest first, paired with its run stamp."""
    found = []
    for path in sorted((outputs / board / "reports").glob(f"{board}_deduped_*.json")):
        match = _STAMP.search(path.name)
        if match:
            found.append((match.group(1), path))
    return found


def _read(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def jobs_for_board(board: str, outputs: Path = OUTPUTS) -> list[Job]:
    """Collapse every run of a board into one record per job.

    A job counts as live when it appeared in the most recent run *of a search it was found
    under* — not merely the board's most recent run. Runs are routinely partial: a --limit
    smoke test or a max_pages_per_run cap covers a subset of searches, and judging liveness
    against the board's last run would report every job outside that subset as vanished.
    """
    latest_run_of_search: dict[tuple, str] = {}
    ids_seen: dict[tuple, set] = defaultdict(set)
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    times: Counter = Counter()
    merged: dict[str, dict] = defaultdict(dict)

    for stamp, path in deduped_reports(board, outputs):
        for row in _read(path):
            job_id = str(row.get("job_id") or "").strip()
            if not job_id:
                continue
            search = (row.get("search_title") or "", row.get("search_location") or "")
            if stamp > latest_run_of_search.get(search, ""):
                latest_run_of_search[search] = stamp
            ids_seen[(stamp, search)].add(job_id)

            first[job_id] = min(first.get(job_id, stamp), stamp)
            last[job_id] = max(last.get(job_id, ""), stamp)
            times[job_id] += 1
            # Later runs win, but a later blank never erases a value an earlier run captured.
            for key in _CARRIED:
                value = row.get(key)
                if value not in (None, ""):
                    merged[job_id][key] = value

    live_ids: set[str] = set()
    for search, stamp in latest_run_of_search.items():
        live_ids |= ids_seen.get((stamp, search), set())

    return [
        Job(
            board=board,
            job_id=job_id,
            first_seen=first[job_id],
            last_seen=last[job_id],
            times_seen=times[job_id],
            live=job_id in live_ids,
            fields=merged[job_id],
        )
        for job_id in sorted(first, key=lambda j: (first[j], j), reverse=True)
    ]


def summarise_board(board: str, outputs: Path = OUTPUTS) -> BoardSummary:
    reports = deduped_reports(board, outputs)
    if not reports:
        return BoardSummary(board=board)

    jobs = jobs_for_board(board, outputs)
    last_run = max(stamp for stamp, _ in reports)
    live = sum(1 for j in jobs if j.live)
    return BoardSummary(
        board=board,
        scanned=True,
        known=len(jobs),
        live=live,
        vanished=len(jobs) - live,
        new=sum(1 for j in jobs if j.first_seen == last_run),
        runs=len(reports),
        last_run=last_run,
        last_run_jobs=len(_read(dict(reports)[last_run])),
    )


def summarise_all(outputs: Path = OUTPUTS) -> list[BoardSummary]:
    return [summarise_board(board, outputs) for board in BOARDS]


def totals(summaries: list[BoardSummary]) -> dict:
    return {
        "known": sum(s.known for s in summaries),
        "live": sum(s.live for s in summaries),
        "vanished": sum(s.vanished for s in summaries),
        "new": sum(s.new for s in summaries),
        "runs": sum(s.runs for s in summaries),
    }
