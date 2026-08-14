from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import build_board_urls, load_config, jittered, raw_capture_stem, run_stamp
import salary as salary_parser
import scan_health
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "indeed"
RAW = OUT / "raw"
JOB_PAGES = OUT / "job_pages"
REPORTS = OUT / "reports"
DEFAULT_CAREER_OPS = Path(os.environ.get("CAREER_OPS_WORKSPACE") or ROOT.parent / "career-ops")


@dataclass
class IndeedLead:
    source: str
    search_title: str
    search_location: str
    role_title: str
    company: str
    salary: str
    location: str
    contract: str
    posted: str
    url: str
    job_id: str
    raw_block: str
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def slug(s: str, max_len: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


def indeed_job_id(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    if qs.get("jk"):
        return qs["jk"][0]
    m = re.search(r"[?&]jk=([^&]+)", url)
    return m.group(1) if m else ""


def canonical_job_url(url: str) -> str:
    jid = indeed_job_id(url)
    return f"https://uk.indeed.com/viewjob?jk={jid}" if jid else url


def dedupe(leads: list[IndeedLead]) -> list[IndeedLead]:
    seen = {}
    for lead in leads:
        key = lead.job_id or "|".join([lead.role_title.lower(), lead.company.lower(), lead.location.lower()])
        if key not in seen:
            seen[key] = lead
    return list(seen.values())


def browser_config(cfg: dict) -> BrowserConfig:
    crawl = cfg.get("crawl", {}) or {}
    return BrowserConfig(
        headless=bool(crawl.get("headless", True)),
        browser_type="chromium",
        verbose=True,
        viewport_width=1920,
        viewport_height=1080,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )


def crawl_config(cfg: dict) -> CrawlerRunConfig:
    crawl = cfg.get("crawl", {}) or {}
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        wait_for="css:body",
        page_timeout=int(crawl.get("page_timeout_ms", 60000)),
        delay_before_return_html=float(crawl.get("delay_before_return_html_seconds", 10)),
        scan_full_page=bool(crawl.get("scan_full_page", True)),
        scroll_delay=float(crawl.get("scroll_delay", 1.5)),
        screenshot=False,
    )


def parse_links(result, spec: dict) -> list[IndeedLead]:
    leads = []
    for _, arr in (result.links or {}).items():
        for link in arr or []:
            href = link.get("href") or ""
            text = (link.get("text") or "").strip()
            url = urljoin(spec["url"], href)
            jid = indeed_job_id(url)
            if not jid:
                continue
            if "/viewjob" not in url and "addlLoc/redirect" not in url and "jk=" not in url:
                continue
            if text.lower() in {"skip to main content", "view similar jobs with this employer", ""}:
                role = "Unknown role"
            else:
                role = text
            leads.append(IndeedLead("indeed", spec["title"], spec["location"], role, "", "", "", "", "", canonical_job_url(url), jid, text))
    return leads


async def scan(cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    board = cfg.get("boards", {}).get("indeed", {})
    if not board.get("enabled") and not allow_disabled:
        raise SystemExit("Indeed is disabled in config.yml. Use --allow-disabled for manual smoke tests.")
    specs = build_board_urls({**cfg, "boards": {**cfg.get("boards", {}), "indeed": {**board, "enabled": True}}}, "indeed")
    if limit:
        specs = specs[:limit]
    RAW.mkdir(parents=True, exist_ok=True)
    with scan_lock.hold("indeed"):
        stamp = run_stamp()
        health = scan_health.RunHealth("indeed")
        all_leads = []
        async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
            for spec in specs:
                print(f"Crawling Indeed {spec['title']!r} / {spec['location']!r}: {spec['url']}")
                r = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
                md = str(r.markdown or "")
                html = r.html or ""
                stem = raw_capture_stem(f"{slug(spec['title'])}__{slug(spec['location'])}", stamp)
                (RAW / f"{stem}.md").write_text(md, encoding="utf-8")
                (RAW / f"{stem}.html").write_text(html, encoding="utf-8")
                leads = parse_links(r, spec)
                print(f"  status={r.status_code} {health.record(r)} leads={len(leads)}")
                all_leads.extend(leads)
                await asyncio.sleep(jittered(float((cfg.get("crawl") or {}).get("delay_seconds", 15))))
        for lead in all_leads:
            salary_parser.apply_to(lead)
        deduped = sorted(dedupe(all_leads), key=salary_parser.sort_key, reverse=True)
        REPORTS.mkdir(parents=True, exist_ok=True)
        raw_path = REPORTS / f"indeed_raw_{stamp}.json"
        dedup_path = REPORTS / f"indeed_deduped_{stamp}.json"
        raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
        dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")
        print(f"Indeed raw={len(all_leads)} deduped={len(deduped)}")
        print(f"Deduped JSON: {dedup_path}")
        health.finish()
        return dedup_path


def latest(pattern: str) -> Path:
    files = sorted(REPORTS.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No {pattern} found")
    return files[0]


def extract_detail(md: str, reject_phrases: list[str]) -> dict:
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    title = company = location = contract = posted = salary = ""
    for i, ln in enumerate(lines):
        if ln.startswith("# ") and not title:
            title = ln[2:].strip()
        if ln == "## Location" and i + 1 < len(lines):
            location = lines[i + 1]
        if ln == "### Pay":
            for t in lines[i+1:i+5]:
                if "£" in t:
                    salary = t.lstrip("* ")
                    break
        if ln == "### Job type":
            for t in lines[i+1:i+5]:
                if t.startswith("*"):
                    contract = t.lstrip("* ")
                    break
    idx = md.find("## Full job description")
    jd = md[idx:].strip() if idx >= 0 else md.strip()
    lower = jd.lower()
    rejected = [p for p in reject_phrases if p.lower() in lower]
    return {"title": title, "company": company, "location": location, "contract": contract, "posted": posted, "salary": salary, "full_jd": jd, "reject_phrases_found": rejected}


async def enrich(cfg: dict, input_path: Path | None = None, top: int | None = None, delay: float | None = None) -> Path:
    input_path = input_path or latest("indeed_deduped_*.json")
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    board = cfg.get("boards", {}).get("indeed", {})
    full = board.get("full_jd", {}) or {}
    reject_phrases = board.get("reject_phrases", []) or []
    top_n = top if top is not None else int(full.get("top_n", 3))
    delay_s = delay if delay is not None else float(full.get("delay_seconds", 30))
    jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:top_n]
    JOB_PAGES.mkdir(parents=True, exist_ok=True)
    enriched = []
    async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
        for job in jobs:
            print(f"Full JD Indeed {job.get('job_id')}: {job.get('role_title')}")
            r = await crawler.arun(url=job["url"], config=crawl_config(cfg))
            md = str(r.markdown or "")
            html = r.html or ""
            detail = extract_detail(md, reject_phrases)
            jid = job.get("job_id") or slug(job.get("url", ""), 32)
            (JOB_PAGES / f"{jid}.md").write_text(md, encoding="utf-8")
            (JOB_PAGES / f"{jid}.html").write_text(html, encoding="utf-8")
            merged = dict(job)
            if detail.get("title") and merged.get("role_title") == "Unknown role":
                merged["role_title"] = detail["title"]
            for k in ["company", "location", "contract", "posted", "salary"]:
                if detail.get(k):
                    merged[k] = detail[k]
            reject_found = detail.get("reject_phrases_found", [])
            full_jd = detail.get("full_jd", "")
            evidence = "full_jd" if r.success and len(full_jd) > 500 and not reject_found else "rejected" if reject_found else "search_result_card"
            merged.update({"full_jd_crawled": bool(r.success), "full_jd_status_code": r.status_code, "full_jd_error": r.error_message, "full_jd_markdown_path": str(JOB_PAGES / f"{jid}.md"), "full_jd_html_path": str(JOB_PAGES / f"{jid}.html"), "full_jd": full_jd, "full_jd_length": len(full_jd), "evidence_level": evidence, "reject_phrases_found": reject_found})
            print(f"  status={r.status_code} success={r.success} full_jd_len={len(full_jd)} evidence={evidence}")
            enriched.append(merged)
            await asyncio.sleep(delay_s)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = REPORTS / f"indeed_enriched_full_jd_{stamp}.json"
    out.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    print(f"Enriched JSON: {out}")
    return out


def export(cfg: dict, input_path: Path | None = None, dry_run: bool = False) -> None:
    input_path = input_path or latest("indeed_enriched_full_jd_*.json")
    career_ops = Path((cfg.get("career_ops") or {}).get("workspace") or DEFAULT_CAREER_OPS)
    jds_dir = career_ops / "jds"
    pipeline_path = career_ops / "data" / "pipeline.md"
    pipeline = pipeline_path.read_text(encoding="utf-8") if pipeline_path.exists() else "# Job Pipeline\n\n## Pendientes\n\n## Processed\n"
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    entries = []
    imported = skipped = 0
    for job in jobs:
        if job.get("evidence_level") != "full_jd" or not (job.get("full_jd") or "").strip():
            skipped += 1
            continue
        jid = job.get("job_id") or slug(job.get("url", ""), 32)
        company = job.get("company") or "Unknown company"
        role = job.get("role_title") or "Unknown role"
        jd_name = f"indeed-{jid}-{slug(company,32)}-{slug(role,44)}.md"
        jd_rel = f"jds/{jd_name}"
        if job.get("url", "") in pipeline or f"local:{jd_rel}" in pipeline or jid in pipeline:
            skipped += 1
            continue
        body = f"""---
source: indeed
source_url: {json.dumps(job.get('url') or '')}
indeed_job_id: {json.dumps(jid)}
company: {json.dumps(company)}
role: {json.dumps(role)}
location: {json.dumps(job.get('location') or '')}
salary: {json.dumps(job.get('salary') or '')}
contract: {json.dumps(job.get('contract') or '')}
posted: {json.dumps(job.get('posted') or '')}
imported: {datetime.now().strftime('%Y-%m-%d')}
evidence_level: full_jd
---

# {role} — {company}

- Source: Indeed
- Source URL: {job.get('url') or ''}
- Indeed job id: {jid}
- Location: {job.get('location') or ''}
- Salary: {job.get('salary') or ''}
- Contract: {job.get('contract') or ''}
- Posted: {job.get('posted') or ''}

## Full job description

{(job.get('full_jd') or '').strip()}
"""
        if not dry_run:
            jds_dir.mkdir(parents=True, exist_ok=True)
            (jds_dir / jd_name).write_text(body, encoding="utf-8")
        entries.append(" | ".join([f"- [ ] local:{jd_rel}", company, role, job.get("location") or "", job.get("salary") or "", f"note: Indeed full JD import; source={job.get('url')}; job_id={jid}"]))
        imported += 1
    if entries:
        m = re.search(r"(^##\s+Pendientes\s*$)", pipeline, flags=re.M) or re.search(r"(^##\s+Pending\s*$)", pipeline, flags=re.M)
        block = "\n".join(entries) + "\n"
        pipeline = pipeline[:m.end()] + "\n" + block + pipeline[m.end():] if m else pipeline.rstrip() + "\n\n## Pendientes\n" + block
        if not dry_run:
            pipeline_path.write_text(pipeline, encoding="utf-8")
    print(json.dumps({"input": str(input_path), "dry_run": dry_run, "imported_count": imported, "skipped_count": skipped}, indent=2))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan", "enrich", "export", "run"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--input")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--top", type=int)
    ap.add_argument("--delay", type=float)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-disabled", action="store_true", help="manual smoke test even when boards.indeed.enabled=false")
    args = ap.parse_args()
    cfg = load_config(ROOT / args.config)
    if args.command == "scan":
        await scan(cfg, args.limit, args.allow_disabled)
    elif args.command == "enrich":
        await enrich(cfg, Path(args.input) if args.input else None, args.top, args.delay)
    elif args.command == "export":
        export(cfg, Path(args.input) if args.input else None, args.dry_run)
    elif args.command == "run":
        dedup = await scan(cfg, args.limit, args.allow_disabled)
        enriched = await enrich(cfg, dedup, args.top, args.delay)
        export(cfg, enriched, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
