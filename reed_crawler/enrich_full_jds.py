from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "reed"
JOB_PAGES = OUT / "job_pages"
REPORTS = OUT / "reports"


def latest_deduped() -> Path:
    files = sorted(REPORTS.glob("reed_deduped_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No reed_deduped_*.json found. Run run_reed_scan.py first.")
    return files[0]


def clean_url(url: str) -> str:
    # Keep Reed job URL canonical enough for crawling.
    return url.split(' "')[0].strip()


def extract_full_jd(markdown: str) -> str:
    if not markdown:
        return ""
    marker = "## Full job description"
    idx = markdown.find(marker)
    if idx == -1:
        marker = "Full job description"
        idx = markdown.find(marker)
    text = markdown[idx + len(marker):] if idx != -1 else markdown
    # Stop before common Reed tail/navigation sections when present.
    stop_patterns = [
        r"\nApply now\b",
        r"\nReference:\b",
        r"\nCreate a job alert",
        r"\nSimilar jobs",
        r"\nRecommended jobs",
        r"\nJobs in",
    ]
    stop = len(text)
    for pat in stop_patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            stop = min(stop, m.start())
    return text[:stop].strip()


def summarize_stack(text: str) -> dict:
    t = text.lower()
    terms = [
        "typescript", "javascript", "node", "node.js", "react", "next.js", "aws", "azure",
        "java", "c#", ".net", "python", "go", "golang", "kubernetes", "docker",
        "tdd", "ddd", "microservices", "serverless", "sql", "postgres", "mongodb",
        "architecture", "technical leadership", "line management", "mentoring", "hybrid", "remote",
    ]
    found = sorted({term for term in terms if term in t})
    return {"matched_terms": found, "term_count": len(found)}


async def crawl_job(crawler: AsyncWebCrawler, job: dict, run_config: CrawlerRunConfig) -> dict:
    url = clean_url(job.get("url", ""))
    job_id = job.get("job_id") or re.sub(r"[^a-zA-Z0-9]+", "_", url)[-80:]
    print(f"Full JD {job_id}: {job.get('role_title')} — {job.get('company')}")
    result = await crawler.arun(url=url, config=run_config)
    md = str(result.markdown or "")
    html = result.html or ""
    full_jd = extract_full_jd(md)

    JOB_PAGES.mkdir(parents=True, exist_ok=True)
    (JOB_PAGES / f"{job_id}.md").write_text(md, encoding="utf-8")
    (JOB_PAGES / f"{job_id}.html").write_text(html, encoding="utf-8")

    enriched = dict(job)
    enriched.update({
        "url": url,
        "full_jd_crawled": bool(result.success),
        "full_jd_status_code": result.status_code,
        "full_jd_error": result.error_message,
        "full_jd_markdown_path": str(JOB_PAGES / f"{job_id}.md"),
        "full_jd_html_path": str(JOB_PAGES / f"{job_id}.html"),
        "full_jd_length": len(full_jd),
        "full_jd": full_jd,
        "full_jd_signals": summarize_stack(full_jd),
        "evidence_level": "full_jd" if result.success and len(full_jd) > 500 else "search_result_card",
    })
    print(f"  status={result.status_code} success={result.success} full_jd_len={len(full_jd)}")
    return enriched


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None, help="reed_deduped_*.json path; defaults to latest")
    ap.add_argument("--config", default="config.yml", help="YAML config path; defaults to config.yml")
    ap.add_argument("--top", type=int, default=None, help="number of top scored jobs to enrich; defaults to boards.reed.full_jd.top_n")
    ap.add_argument("--delay", type=float, default=None, help="seconds between full-JD crawls; defaults to boards.reed.full_jd.delay_seconds")
    args = ap.parse_args()

    cfg_path = ROOT / args.config
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    reed_full = (((cfg or {}).get("boards") or {}).get("reed") or {}).get("full_jd") or {}
    crawl_cfg = (cfg or {}).get("crawl") or {}
    top_n = args.top if args.top is not None else int(reed_full.get("top_n", 20))
    delay_seconds = args.delay if args.delay is not None else float(reed_full.get("delay_seconds", 2.0))

    input_path = Path(args.input) if args.input else latest_deduped()
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[: top_n]

    browser_config = BrowserConfig(headless=bool(crawl_cfg.get("headless", True)), browser_type="chromium", verbose=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        wait_for="css:body",
        page_timeout=int(crawl_cfg.get("page_timeout_ms", 45000)),
        delay_before_return_html=float(crawl_cfg.get("delay_before_return_html_seconds", 6)),
        scan_full_page=bool(crawl_cfg.get("scan_full_page", True)),
        scroll_delay=float(crawl_cfg.get("scroll_delay", 0.5)),
        screenshot=False,
    )

    enriched = []
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for job in jobs:
            enriched.append(await crawl_job(crawler, job, run_config))
            await asyncio.sleep(delay_seconds)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_json = REPORTS / f"reed_enriched_full_jd_{stamp}.json"
    out_md = REPORTS / f"reed_enriched_full_jd_{stamp}.md"
    out_json.write_text(json.dumps(enriched, indent=2), encoding="utf-8")

    lines = ["# Reed enriched full JD report", "", f"Input: {input_path}", f"Jobs enriched: {len(enriched)}", ""]
    for i, j in enumerate(enriched, 1):
        lines += [
            f"## {i}. {j.get('role_title')} — {j.get('company') or 'Unknown'}",
            f"- Evidence: {j.get('evidence_level')} ({j.get('full_jd_length')} chars)",
            f"- Salary: {j.get('salary')}",
            f"- Location: {j.get('location')}",
            f"- Type: {j.get('contract')}",
            f"- Terms: {', '.join(j.get('full_jd_signals', {}).get('matched_terms', []))}",
            f"- URL: {j.get('url')}",
            f"- JD file: {j.get('full_jd_markdown_path')}",
            "",
        ]
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print("\nSummary")
    print(f"Input: {input_path}")
    print(f"Jobs enriched: {len(enriched)}")
    print(f"JSON: {out_json}")
    print(f"Report: {out_md}")


if __name__ == "__main__":
    asyncio.run(main())
