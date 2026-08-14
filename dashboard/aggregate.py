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


@dataclass
class Run:
    board: str
    stamp: str
    jobs: int
    searches: int
    healthy: bool

    @property
    def display_time(self) -> str:
        date, time = self.stamp.split("_")
        return f"{date} {time[:2]}:{time[2:4]}"


def runs(board: str = "", outputs: Path = OUTPUTS) -> list[Run]:
    """Every recorded scan, most recent first.

    A run finding nothing is flagged: the crawler cannot always tell a broken fetch from a
    search with no matches, and a board quietly returning zero is what this view is for.
    """
    found: list[Run] = []
    for name in ([board] if board else BOARDS):
        for stamp, path in deduped_reports(name, outputs):
            rows = _read(path)
            searches = {(r.get("search_title"), r.get("search_location")) for r in rows}
            found.append(Run(board=name, stamp=stamp, jobs=len(rows),
                             searches=len(searches), healthy=bool(rows)))
    return sorted(found, key=lambda r: r.stamp, reverse=True)


def page_of(items: list, page: int, per_page: int) -> tuple[list, int, int]:
    """Slice a list into a page, returning the slice, the clamped page and the page count."""
    pages = max(1, -(-len(items) // per_page))
    page = min(max(page, 1), pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, pages


def all_jobs(outputs: Path = OUTPUTS) -> list[Job]:
    jobs: list[Job] = []
    for board in BOARDS:
        jobs.extend(jobs_for_board(board, outputs))
    return jobs


def find_job(board: str, job_id: str, outputs: Path = OUTPUTS) -> Job | None:
    for job in jobs_for_board(board, outputs):
        if job.job_id == job_id:
            return job
    return None


def sightings(board: str, job_id: str, outputs: Path = OUTPUTS) -> list[str]:
    """Every run stamp in which this job was seen, most recent first."""
    seen = []
    for stamp, path in deduped_reports(board, outputs):
        if any(str(row.get("job_id") or "") == job_id for row in _read(path)):
            seen.append(stamp)
    return sorted(seen, reverse=True)


SORTS = {
    "first_seen": lambda j: j.first_seen,
    "last_seen": lambda j: j.last_seen,
    "pay": lambda j: (j.pay is not None, j.pay or 0),
    "company": lambda j: (j.company or "￿").lower(),
    "role": lambda j: j.role_title.lower(),
    "times_seen": lambda j: j.times_seen,
}

# Ascending reads more naturally for names; everything else is most-interesting-first.
_ASCENDING = {"company", "role"}


def select(jobs: list[Job], board: str = "", state: str = "", min_pay: int | None = None,
           query: str = "", sort: str = "first_seen") -> list[Job]:
    """Apply the filters and ordering the job list offers."""
    chosen = jobs
    if board:
        chosen = [j for j in chosen if j.board == board]
    if state == "live":
        chosen = [j for j in chosen if j.live]
    elif state == "vanished":
        chosen = [j for j in chosen if not j.live]
    if min_pay:
        # An advert with no figure cannot be shown to clear a floor, so it is excluded.
        chosen = [j for j in chosen if (j.pay or 0) >= min_pay]
    if query:
        needle = query.lower()
        chosen = [j for j in chosen
                  if needle in j.role_title.lower()
                  or needle in j.company.lower()
                  or needle in str(j.get("location")).lower()]
    key = SORTS.get(sort, SORTS["first_seen"])
    return sorted(chosen, key=key, reverse=sort not in _ASCENDING)


def totals(summaries: list[BoardSummary]) -> dict:
    return {
        "known": sum(s.known for s in summaries),
        "live": sum(s.live for s in summaries),
        "vanished": sum(s.vanished for s in summaries),
        "new": sum(s.new for s in summaries),
        "runs": sum(s.runs for s in summaries),
    }
