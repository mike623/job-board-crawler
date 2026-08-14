"""The dashboard web service.

Server-rendered, single user, bound to loopback. It reads the crawler's report files and
renders them; it holds no database and no cache.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from . import aggregate

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

app = FastAPI(title="Job Board Crawler", docs_url=None, redoc_url=None)


CSV_COLUMNS = [
    "board", "job_id", "role_title", "company", "location", "salary",
    "salary_min", "salary_max", "salary_period", "contract", "posted",
    "first_seen", "last_seen", "times_seen", "live", "url",
]


@app.get("/")
def landing(request: Request):
    summaries = aggregate.summarise_all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "summaries": summaries,
            "totals": aggregate.totals(summaries),
        },
    )


def _filters(board: str, state: str, min_pay: int | None, q: str, sort: str) -> dict:
    return {"board": board, "state": state, "min_pay": min_pay, "q": q, "sort": sort}


@app.get("/jobs")
def job_list(
    request: Request,
    board: str = "",
    state: str = "live",
    min_pay: int | None = None,
    q: str = "",
    sort: str = Query("first_seen"),
):
    jobs = aggregate.select(aggregate.all_jobs(), board=board, state=state,
                            min_pay=min_pay, query=q, sort=sort)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": jobs,
            "boards": aggregate.BOARDS,
            "sorts": list(aggregate.SORTS),
            "filters": _filters(board, state, min_pay, q, sort),
            "shown": len(jobs),
        },
    )


@app.get("/jobs/{board}/{job_id}")
def job_detail(request: Request, board: str, job_id: str):
    if board not in aggregate.BOARDS:
        raise HTTPException(status_code=404, detail="unknown board")
    job = aggregate.find_job(board, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"job": job, "sightings": aggregate.sightings(board, job_id)},
    )


@app.get("/runs")
def run_log(request: Request, board: str = "", page: int = 1, per_page: int = 50):
    every = aggregate.runs(board)
    shown, page, pages = aggregate.page_of(every, page, max(1, min(per_page, 200)))
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": shown,
            "boards": aggregate.BOARDS,
            "board": board,
            "page": page,
            "pages": pages,
            "total": len(every),
            "per_page": per_page,
        },
    )


@app.get("/export.csv")
def export_csv(board: str = "", state: str = "live", min_pay: int | None = None,
               q: str = "", sort: str = "first_seen"):
    """The current filter, as a spreadsheet."""
    jobs = aggregate.select(aggregate.all_jobs(), board=board, state=state,
                            min_pay=min_pay, query=q, sort=sort)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        writer.writerow({
            "board": job.board,
            "job_id": job.job_id,
            "first_seen": job.first_seen,
            "last_seen": job.last_seen,
            "times_seen": job.times_seen,
            "live": "yes" if job.live else "no",
            **{k: job.fields.get(k, "") for k in
               ("role_title", "company", "location", "salary", "salary_min",
                "salary_max", "salary_period", "contract", "posted", "url")},
        })
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="jobs.csv"'},
    )
