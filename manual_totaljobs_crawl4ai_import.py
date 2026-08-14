from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

URLS = [
    "https://www.totaljobs.com/job/staff-software-engineer/stepstone-uk-job107798687",
    "https://www.totaljobs.com/job/full-stack-software-developer/data-careers-job107787016",
]

CRAWLER_ROOT = Path(__file__).resolve().parent
CAREER_OPS = Path(os.environ.get("CAREER_OPS_WORKSPACE") or CRAWLER_ROOT.parent / "career-ops")
OUT = CRAWLER_ROOT / "outputs" / "totaljobs" / "manual"


def job_id(url: str) -> str:
    m = re.search(r"job(\d+)", url)
    return m.group(1) if m else re.sub(r"\W+", "-", url)[-32:]


def slug(s: str, max_len: int = 72) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


def lines(md: str) -> list[str]:
    return [ln.strip() for ln in (md or "").splitlines() if ln.strip()]


def extract_detail(md: str, fallback_url: str) -> dict:
    clean = lines(md)
    title = company = location = salary = contract = posted = ""
    title_i = None
    for i, ln in enumerate(clean):
        if ln.startswith("# "):
            cand = ln[2:].strip()
            if cand and not re.search(r"totaljobs|sign in|jobs by", cand, re.I):
                title = cand
                title_i = i
                break
    if title_i is not None:
        tail = clean[title_i + 1 : title_i + 16]
        for t in tail:
            val = re.sub(r"^\*+\s*", "", t).strip()
            if not val or val.lower() in {"apply", "save"}:
                continue
            if "view profile" in val.lower() and not company:
                company = re.sub(r"View Profile$", "", val, flags=re.I).strip()
            elif not company and not any(x in val.lower() for x in ["apply", "save job", "posted", "per annum", "per day", "per hour"]):
                if len(val) < 90:
                    company = val
            if "£" in val and not salary:
                salary = val
            elif re.search(r"permanent|contract|full[- ]time|part[- ]time|temporary", val, re.I) and not contract:
                contract = val
            elif re.search(r"published|posted|ago|today|yesterday", val, re.I) and not posted:
                posted = val
            elif not location and re.search(r"\b(remote|hybrid|london|manchester|leeds|sheffield|doncaster|uk|united kingdom|england|yorkshire|birmingham|nottingham)\b", val, re.I):
                location = val
    if not title:
        path = urlparse(fallback_url).path
        title = (path.split("/job/")[1].split("/")[0] if "/job/" in path else "").replace("-", " ").title() or "Unknown role"
    if not company:
        hostbit = fallback_url.rstrip("/").split("/")[-1]
        company = re.sub(r"-?job\d+$", "", hostbit).replace("-", " ").title() or "Unknown company"

    jd = md.strip()
    if title and f"# {title}" in md:
        jd = md[md.find(f"# {title}") :].strip()
    stops = []
    for pat in [r"\nSimilar jobs", r"\nRecommended jobs", r"\nJobs by", r"\nCreate alert", r"\nShare this job"]:
        m = re.search(pat, jd, flags=re.I)
        if m and m.start() > 500:
            stops.append(m.start())
    if stops:
        jd = jd[: min(stops)].strip()
    return {"title": title, "company": company, "location": location, "salary": salary, "contract": contract, "posted": posted, "full_jd": jd}


async def crawl() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    browser_config = BrowserConfig(
        headless=True,
        browser_type="chromium",
        verbose=False,
        viewport_width=1920,
        viewport_height=1080,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        wait_for="css:body",
        page_timeout=90000,
        delay_before_return_html=10,
        scan_full_page=True,
        scroll_delay=1.0,
        screenshot=False,
    )
    results = []
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for url in URLS:
            jid = job_id(url)
            result = await crawler.arun(url=url, config=run_config)
            md = str(result.markdown or "")
            html = result.html or ""
            (OUT / f"{jid}.md").write_text(md, encoding="utf-8")
            (OUT / f"{jid}.html").write_text(html, encoding="utf-8")
            detail = extract_detail(md, url)
            evidence = "full_jd" if result.success and len(detail.get("full_jd", "")) > 500 else "failed_or_thin"
            results.append(
                {
                    "url": url,
                    "job_id": jid,
                    "success": bool(result.success),
                    "status_code": result.status_code,
                    "error": result.error_message,
                    "markdown_len": len(md),
                    "html_len": len(html),
                    "markdown_path": str(OUT / f"{jid}.md"),
                    "html_path": str(OUT / f"{jid}.html"),
                    "evidence_level": evidence,
                    **detail,
                }
            )
            await asyncio.sleep(3)
    return results


def export(results: list[dict]) -> dict:
    pipeline_path = CAREER_OPS / "data" / "pipeline.md"
    pipeline = pipeline_path.read_text(encoding="utf-8")
    imported = []
    skipped = []
    for job in results:
        if job["evidence_level"] != "full_jd":
            skipped.append({"job_id": job["job_id"], "reason": "not full_jd", "markdown_len": job["markdown_len"], "success": job["success"], "error": job["error"]})
            continue
        if job["job_id"] in pipeline or job["url"] in pipeline:
            skipped.append({"job_id": job["job_id"], "reason": "duplicate in pipeline"})
            continue
        jd_name = f"totaljobs-{job['job_id']}-{slug(job['company'], 32)}-{slug(job['title'], 44)}.md"
        jd_rel = f"jds/{jd_name}"
        body = f'''---
source: totaljobs
source_url: {json.dumps(job['url'])}
totaljobs_job_id: {json.dumps(job['job_id'])}
company: {json.dumps(job['company'])}
role: {json.dumps(job['title'])}
location: {json.dumps(job['location'])}
salary: {json.dumps(job['salary'])}
contract: {json.dumps(job['contract'])}
posted: {json.dumps(job['posted'])}
imported: {datetime.now().strftime('%Y-%m-%d')}
evidence_level: full_jd
---

# {job['title']} — {job['company']}

- Source: Totaljobs
- Source URL: {job['url']}
- Totaljobs job id: {job['job_id']}
- Location: {job['location']}
- Salary: {job['salary']}
- Contract: {job['contract']}
- Posted: {job['posted']}

## Full job description

{job['full_jd'].strip()}
'''
        (CAREER_OPS / "jds").mkdir(exist_ok=True)
        (CAREER_OPS / jd_rel).write_text(body, encoding="utf-8")
        imported.append(
            " | ".join(
                [
                    f"- [ ] local:{jd_rel}",
                    job["company"],
                    job["title"],
                    job["location"],
                    job["salary"],
                    f"note: Totaljobs manual full JD import; source={job['url']}; job_id={job['job_id']}",
                ]
            )
        )
    if imported:
        m = re.search(r"(^##\s+Pendientes\s*$)", pipeline, flags=re.M) or re.search(r"(^##\s+Pending\s*$)", pipeline, flags=re.M)
        block = "\n".join(imported) + "\n"
        if m:
            pipeline = pipeline[: m.end()] + "\n" + block + pipeline[m.end() :]
        else:
            pipeline = pipeline.rstrip() + "\n\n## Pendientes\n" + block
        pipeline_path.write_text(pipeline, encoding="utf-8")
    return {"imported_count": len(imported), "imported_entries": imported, "skipped": skipped}


async def main() -> None:
    results = await crawl()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    enriched = OUT / f"totaljobs_manual_enriched_{stamp}.json"
    enriched.write_text(json.dumps(results, indent=2), encoding="utf-8")
    export_result = export(results)
    print(
        json.dumps(
            {
                "ok": True,
                "crawler": "crawl4ai",
                "enriched_json": str(enriched),
                **export_result,
                "results": [
                    {k: v for k, v in r.items() if k != "full_jd"} | {"full_jd_length": len(r.get("full_jd", "")), "full_jd_head": r.get("full_jd", "")[:500]}
                    for r in results
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
