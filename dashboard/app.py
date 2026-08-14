"""The dashboard web service.

Server-rendered, single user, bound to loopback. It reads the crawler's report files and
renders them; it holds no database and no cache.
"""
from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import aggregate, pipeline, pool, scans
from .scan_lock_view import board_lock_state

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

@asynccontextmanager
async def lifespan(_: FastAPI):
    # A dashboard killed mid-scan leaves records showing as running for ever.
    scans.reconcile()
    yield


app = FastAPI(title="Job Board Crawler", docs_url=None, redoc_url=None, lifespan=lifespan)


CSV_COLUMNS = [
    "board", "job_id", "role_title", "company", "location", "salary",
    "salary_min", "salary_max", "salary_period", "contract", "posted",
    "first_seen", "last_seen", "times_seen", "live", "in_pipeline", "url",
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
            "scan_history": scans.history(10),
            "locks": {b: board_lock_state(b) for b in aggregate.BOARDS},
            "scannable": list(scans.COMMANDS),
        },
    )


@app.post("/scan/{board}")
async def start_scan(board: str):
    if board not in scans.COMMANDS:
        raise HTTPException(status_code=404, detail="unknown board")
    if scans.scans.board_is_running(board):
        raise HTTPException(status_code=409, detail=f"{board} is already being scanned")
    run = await scans.scans.start(board)
    return RedirectResponse(f"/scan/{run.id}", status_code=303)


@app.post("/scan-all")
async def start_all():
    """Scan every enabled board at once, bounded per host."""
    config = pool.load_config()
    boards = [b for b in pool.enabled_boards(config, list(scans.COMMANDS))
              if not scans.scans.board_is_running(b)]
    if not boards:
        raise HTTPException(status_code=409, detail="every enabled board is already being scanned")
    worker_pool = pool.Pool(start_scan=scans.scans.start_and_wait, max_workers=len(boards))
    asyncio.create_task(worker_pool.run(boards))
    # The queue is visible on the overview; each scan gets its own log page.
    return RedirectResponse("/", status_code=303)


@app.get("/scan/{run_id}")
def scan_view(request: Request, run_id: str):
    run = scans.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return templates.TemplateResponse(request, "scan.html", {"run": run})


@app.get("/scan/{run_id}/log")
async def scan_log(run_id: str):
    return StreamingResponse(
        scans.scans.stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _filters(board: str, state: str, min_pay: int | None, q: str, sort: str, actioned: str = "") -> dict:
    return {"board": board, "state": state, "min_pay": min_pay, "q": q, "sort": sort, "actioned": actioned}


def _annotated_jobs():
    """Every job, carrying its downstream status when that workspace is readable."""
    jobs = aggregate.all_jobs()
    statuses = pipeline.load()
    pipeline.annotate(jobs, statuses)
    return jobs, bool(statuses)


@app.get("/jobs")
def job_list(
    request: Request,
    board: str = "",
    state: str = "live",
    min_pay: int | None = None,
    q: str = "",
    sort: str = Query("first_seen"),
    actioned: str = "",
):
    every, has_pipeline = _annotated_jobs()
    jobs = aggregate.select(every, board=board, state=state, min_pay=min_pay,
                            query=q, sort=sort, actioned=actioned)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": jobs,
            "boards": aggregate.BOARDS,
            "sorts": list(aggregate.SORTS),
            "filters": _filters(board, state, min_pay, q, sort, actioned),
            "shown": len(jobs),
            "has_pipeline": has_pipeline,
        },
    )


@app.get("/jobs/{board}/{job_id}")
def job_detail(request: Request, board: str, job_id: str):
    if board not in aggregate.BOARDS:
        raise HTTPException(status_code=404, detail="unknown board")
    job = aggregate.find_job(board, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    statuses = pipeline.load()
    pipeline.annotate([job], statuses)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"job": job, "sightings": aggregate.sightings(board, job_id), "has_pipeline": bool(statuses)},
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
               q: str = "", sort: str = "first_seen", actioned: str = ""):
    """The current filter, as a spreadsheet."""
    every, _ = _annotated_jobs()
    jobs = aggregate.select(every, board=board, state=state, min_pay=min_pay,
                            query=q, sort=sort, actioned=actioned)

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
            "in_pipeline": "yes" if (job.pipeline and job.pipeline.present) else "no",
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
