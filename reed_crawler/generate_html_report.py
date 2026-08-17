#!/usr/bin/env python3
"""Generate a simple static HTML visualization of crawl runs and job records."""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
REPORTS = OUTPUTS / "reports"
STAMP_RE = re.compile(r"^(?P<board>[a-z]+)_(?P<stage>[a-z_]+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{6})\.json$")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # keep report generation best-effort
        return {"_error": str(exc)}


def report_files() -> list[Path]:
    return sorted(OUTPUTS.glob("*/reports/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def collect_runs() -> list[dict[str, Any]]:
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    for path in report_files():
        m = STAMP_RE.match(path.name)
        if not m:
            continue
        board = m.group("board")
        stage = m.group("stage")
        stamp = f"{m.group('date')}_{m.group('time')}"
        key = (board, stamp)
        data = load_json(path)
        count = len(data) if isinstance(data, list) else 0
        run = runs.setdefault(
            key,
            {
                "board": board,
                "stamp": stamp,
                "stages": {},
                "files": [],
                "jobs": [],
            },
        )
        run["stages"][stage] = count
        run["files"].append(path)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    job = dict(item)
                    job["_stage"] = stage
                    job["_file"] = str(path)
                    run["jobs"].append(job)
    return sorted(runs.values(), key=lambda r: r["stamp"], reverse=True)


def render(runs: list[dict[str, Any]]) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_jobs = sum(len(r["jobs"]) for r in runs)
    by_board: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for run in runs:
        for stage, count in run["stages"].items():
            by_board[run["board"]][stage] += count

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Job-board crawl runs</title>",
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;line-height:1.35}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #ddd;padding:6px 8px;vertical-align:top}th{background:#f5f5f5;text-align:left}.muted{color:#666}.job{margin:8px 0;padding:8px;border:1px solid #ddd;border-radius:6px}.ok{color:#057a24}.warn{color:#9a6700}a{color:#0645ad}</style>",
        "</head><body>",
        f"<h1>Job-board crawl runs</h1><p class='muted'>Generated {esc(generated)} from {esc(str(OUTPUTS))}. Runs: {len(runs)}. Job records across run/stage files: {total_jobs}.</p>",
        "<h2>Board totals</h2><table><tr><th>Board</th><th>Raw</th><th>Deduped</th><th>Enriched full JD</th></tr>",
    ]
    for board in sorted(by_board):
        stages = by_board[board]
        parts.append(f"<tr><td>{esc(board)}</td><td>{stages.get('raw',0)}</td><td>{stages.get('deduped',0)}</td><td>{stages.get('enriched_full_jd',0)}</td></tr>")
    parts.append("</table><h2>Runs</h2><table><tr><th>Stamp</th><th>Board</th><th>Stages</th><th>Files</th></tr>")
    for run in runs:
        stages = ", ".join(f"{esc(k)}={v}" for k, v in sorted(run["stages"].items()))
        files = "<br>".join(esc(str(p.relative_to(ROOT))) for p in sorted(run["files"]))
        anchor = f"{run['board']}-{run['stamp']}"
        parts.append(f"<tr><td><a href='#{esc(anchor)}'>{esc(run['stamp'])}</a></td><td>{esc(run['board'])}</td><td>{stages}</td><td>{files}</td></tr>")
    parts.append("</table><h2>Jobs by run</h2>")
    for run in runs:
        anchor = f"{run['board']}-{run['stamp']}"
        parts.append(f"<h3 id='{esc(anchor)}'>{esc(run['board'])} {esc(run['stamp'])}</h3>")
        for job in run["jobs"]:
            title = job.get("role_title") or job.get("title") or "Untitled"
            company = job.get("company") or "Unknown company"
            url = str(job.get("url") or "").split(' "')[0]
            evidence = job.get("evidence_level") or ("full_jd" if job.get("full_jd_crawled") else "search_result")
            jd_path = job.get("full_jd_markdown_path") or ""
            link = f"<a href='{esc(url)}'>source</a>" if url.startswith("http") else ""
            parts.append(
                "<div class='job'>"
                f"<strong>{esc(title)}</strong> — {esc(company)} "
                f"<span class='muted'>[{esc(job.get('_stage'))}; {esc(evidence)}; id={esc(job.get('job_id'))}]</span><br>"
                f"{esc(job.get('location'))} · {esc(job.get('salary'))} · {esc(job.get('posted'))} {link}<br>"
                f"<span class='muted'>{esc(jd_path)}</span>"
                "</div>"
            )
    parts.append("</body></html>")
    return "\n".join(parts)


def main() -> None:
    runs = collect_runs()
    html_text = render(runs)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    for path in (OUTPUTS / "crawl_runs.html", REPORTS / "crawl_runs.html"):
        path.write_text(html_text, encoding="utf-8")
        print(path)
    print(f"runs={len(runs)} job_records={sum(len(r['jobs']) for r in runs)}")


if __name__ == "__main__":
    main()
