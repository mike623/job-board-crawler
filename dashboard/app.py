"""The dashboard web service.

Server-rendered, single user, bound to loopback. It reads the crawler's report files and
renders them; it holds no database and no cache.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from . import aggregate

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

app = FastAPI(title="Job Board Crawler", docs_url=None, redoc_url=None)


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
