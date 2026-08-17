"""Haystack (haystack.cv) discovery, enrichment and export.

Haystack is a client-rendered aggregator, which changes two things versus the other boards:

* Its search cards carry no delimiters in the rendered markdown — a card's title runs straight
  into its company name — so cards are parsed out of the HTML, anchored on the lucide icon that
  labels each field, rather than out of the markdown.
* Its search backend intermittently answers with "Something went wrong loading jobs". A scan
  that returns nothing is therefore normal and is retried once before being believed.

Adverts are syndicated, and some arrive truncated ("... click apply for full job details"), so
enrichment applies `reject_phrases` the same way the Indeed pipeline does.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import build_board_urls, load_config, raw_capture_stem, run_stamp
import salary as salary_parser

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "haystack"
RAW = OUT / "raw"
JOB_PAGES = OUT / "job_pages"
REPORTS = OUT / "reports"
DEFAULT_CAREER_OPS = Path(os.environ.get("CAREER_OPS_WORKSPACE") or ROOT.parent / "career-ops")

# Rendered when Haystack's own search backend fails; the page is otherwise a normal, empty result list.
SEARCH_ERROR = "Something went wrong loading jobs"


@dataclass
class HaystackLead:
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
    score: float = 0.0
    score_notes: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def slug(s: str, max_len: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


JOB_HREF = re.compile(r"/jobs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def haystack_job_id(url: str) -> str:
    m = JOB_HREF.search(url or "")
    return m.group(1) if m else ""


def score_lead(job: HaystackLead) -> HaystackLead:
    text = " ".join([job.role_title, job.company, job.location, job.contract, job.salary, job.raw_block]).lower()
    score = 0.0
    notes = []
    positives = ["senior", "lead", "principal", "staff", "full stack", "backend", "typescript", "node", "react", "aws", "platform", "architecture", "remote", "hybrid"]
    negatives = ["junior", "graduate", "apprentice", "placement", "no experience", "wordpress", "php", "onsite only", "salesforce"]
    for p in positives:
        if p in text:
            score += 0.4
            notes.append(f"+{p}")
    for n in negatives:
        if n in text:
            score -= 0.8
            notes.append(f"-{n}")
    # Haystack's free-text search is loose — a "fullstack" query returns sales roles — so a
    # posting that actually matches the location we asked for is worth more here than elsewhere.
    target = (job.search_location or "").lower().strip()
    if (target and target in text) or "remote" in text:
        score += 0.8
        notes.append("+location")
    if re.search(r"£\s*(7[0-9]|8[0-9]|9[0-9]|1\d\d)[,k]", text):
        score += 1.0
        notes.append("+salary")
    job.score = round(score, 2)
    job.score_notes = ", ".join(notes)
    return job


def dedupe(leads: list[HaystackLead]) -> list[HaystackLead]:
    seen: dict[str, HaystackLead] = {}
    for lead in leads:
        key = lead.job_id or "|".join([lead.role_title.lower(), lead.company.lower(), lead.location.lower()])
        if key not in seen:
            seen[key] = lead
    return list(seen.values())


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


# Each card field is introduced by a lucide icon. The surrounding utility classes are generated
# and churn with every redesign; the icon names say what the value means, so they are the anchor.
CARD_ICON_FIELDS = {
    "building2": "company",
    "map-pin": "location",
    "banknote": "salary",
    "clock": "posted",
}


def _icon_value(card, icon: str) -> str:
    svg = card.find("svg", class_=f"lucide-{icon}")
    if not svg:
        return ""
    span = svg.find_next_sibling("span")
    return span.get_text(" ", strip=True) if span else ""


def parse_search_cards(html: str, spec: dict) -> list[HaystackLead]:
    """Parse job cards out of the rendered search HTML.

    Markdown is unusable here: Haystack emits a whole card as one link whose text concatenates
    title, company, location, salary and posted date with nothing between them.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    leads: list[HaystackLead] = []
    for anchor in soup.find_all("a", href=JOB_HREF):
        url = urljoin(spec["url"], anchor.get("href") or "")
        jid = haystack_job_id(url)
        if not jid:
            continue
        heading = anchor.find(["h2", "h3"])
        fields = {name: _icon_value(anchor, icon) for icon, name in CARD_ICON_FIELDS.items()}
        leads.append(HaystackLead(
            source="haystack",
            search_title=spec["title"],
            search_location=spec["location"],
            role_title=heading.get_text(" ", strip=True) if heading else "Unknown role",
            company=fields["company"],
            salary=fields["salary"],
            location=fields["location"],
            # Cards render job type and category as unlabelled badges that cannot be told apart;
            # the contract is left to enrichment, which reads it from a labelled field.
            contract="",
            posted=fields["posted"],
            url=url,
            job_id=jid,
            raw_block=anchor.get_text(" ", strip=True),
        ))
    return leads


async def scan_searches(cfg: dict, limit: int | None = None) -> Path:
    specs = build_board_urls(cfg, "haystack")
    if limit:
        specs = specs[:limit]
    board = cfg.get("boards", {}).get("haystack", {})
    if not board.get("enabled"):
        raise SystemExit("Haystack is disabled in config.yml")
    RAW.mkdir(parents=True, exist_ok=True)
    stamp = run_stamp()
    delay_s = float((cfg.get("crawl") or {}).get("delay_seconds", 15))
    all_leads: list[HaystackLead] = []
    async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
        for spec in specs:
            print(f"Crawling Haystack {spec['title']!r} / {spec['location']!r}: {spec['url']}")
            result = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
            leads = parse_search_cards(result.html or "", spec)
            failed = SEARCH_ERROR in str(result.markdown or "")
            if not leads and failed:
                # The search backend, not the crawl, failed. One retry is usually enough.
                print(f"  search backend error, retrying once after {delay_s}s")
                await asyncio.sleep(delay_s)
                result = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
                leads = parse_search_cards(result.html or "", spec)
                failed = SEARCH_ERROR in str(result.markdown or "")
            stem = raw_capture_stem(f"{slug(spec['title'])}__{slug(spec['location'])}", stamp)
            (RAW / f"{stem}.md").write_text(str(result.markdown or ""), encoding="utf-8")
            (RAW / f"{stem}.html").write_text(result.html or "", encoding="utf-8")
            print(f"  status={result.status_code} success={result.success} cards={len(leads)} search_error={failed}")
            all_leads.extend(leads)
            await asyncio.sleep(delay_s)
    for lead in all_leads:
        salary_parser.apply_to(lead)
    deduped = sorted([score_lead(x) for x in dedupe(all_leads)], key=lambda j: j.score, reverse=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    raw_path = REPORTS / f"haystack_raw_{stamp}.json"
    dedup_path = REPORTS / f"haystack_deduped_{stamp}.json"
    raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
    dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")
    print(f"Haystack raw={len(all_leads)} deduped={len(deduped)}")
    print(f"Deduped JSON: {dedup_path}")
    return dedup_path


def latest(pattern: str) -> Path:
    files = sorted(REPORTS.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No {pattern} found in {REPORTS}")
    return files[0]


COMPANY_LINK = re.compile(r"^\[(?P<name>[^\]]+)\]\(https://haystack\.cv/company/[^)]+\)\s*$")

# "### Quick Overview" is a flat list of alternating label/value lines.
OVERVIEW_LABELS = {"salary", "work type", "schedule", "level", "location", "posted", "experience"}

# Everything Haystack renders after the advert itself.
JD_END_MARKERS = ["\nApply Now", "\nSaveShare", "\n### About", "\n## Similar Jobs"]


def _quick_overview(lines: list[str]) -> dict:
    try:
        start = lines.index("### Quick Overview") + 1
    except ValueError:
        return {}
    values: dict[str, str] = {}
    label = ""
    for line in lines[start:]:
        if line.startswith("#"):
            break
        key = line.lower().rstrip(":")
        if key in OVERVIEW_LABELS:
            label = key
        elif label:
            values.setdefault(label, line)
            label = ""
    return values


def extract_detail(md: str, reject_phrases: list[str]) -> dict:
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    title = company = ""
    for line in lines:
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        m = COMPANY_LINK.match(line)
        if m and not company:
            company = m.group("name").strip()

    overview = _quick_overview(lines)
    # "Schedule" is the contract shape (Full-time); "Work Type" is on-site/hybrid/remote.
    contract = " ".join(x for x in [overview.get("schedule", ""), overview.get("work type", "")] if x).strip()

    start = md.find("## Job Description")
    jd = md[start:] if start >= 0 else md
    stop = len(jd)
    for marker in JD_END_MARKERS:
        idx = jd.find(marker)
        if idx > 0:
            stop = min(stop, idx)
    jd = jd[:stop].strip()

    lower = jd.lower()
    return {
        "title": title,
        "company": company,
        "location": overview.get("location", ""),
        "contract": contract,
        "posted": "",
        "salary": overview.get("salary", ""),
        "full_jd": jd,
        "reject_phrases_found": [p for p in reject_phrases if p.lower() in lower],
    }


async def enrich(cfg: dict, input_path: Path | None = None, top: int | None = None, delay: float | None = None) -> Path:
    input_path = input_path or latest("haystack_deduped_*.json")
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    board = cfg.get("boards", {}).get("haystack", {})
    full = board.get("full_jd", {}) or {}
    reject_phrases = board.get("reject_phrases", []) or []
    top_n = top if top is not None else int(full.get("top_n", 5))
    delay_s = delay if delay is not None else float(full.get("delay_seconds", 20))
    jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:top_n]
    JOB_PAGES.mkdir(parents=True, exist_ok=True)
    enriched = []
    async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
        for job in jobs:
            print(f"Full JD Haystack {job.get('job_id')}: {job.get('role_title')}")
            result = await crawler.arun(url=job["url"], config=crawl_config(cfg))
            md = str(result.markdown or "")
            detail = extract_detail(md, reject_phrases)
            job_id = job.get("job_id") or slug(job.get("url", ""), 32)
            (JOB_PAGES / f"{job_id}.md").write_text(md, encoding="utf-8")
            (JOB_PAGES / f"{job_id}.html").write_text(result.html or "", encoding="utf-8")
            merged = dict(job)
            for key in ["title", "company", "location", "contract", "salary"]:
                if detail.get(key):
                    merged[{"title": "role_title"}.get(key, key)] = detail[key]
            salary_parser_fields = salary_parser.parse_salary(merged.get("salary", ""))
            merged.update(salary_parser_fields)
            reject_found = detail.get("reject_phrases_found", [])
            full_jd = detail.get("full_jd", "")
            evidence = "full_jd" if result.success and len(full_jd) > 500 and not reject_found else "rejected" if reject_found else "search_result_card"
            merged.update({
                "full_jd_crawled": bool(result.success),
                "full_jd_status_code": result.status_code,
                "full_jd_error": result.error_message,
                "full_jd_markdown_path": str(JOB_PAGES / f"{job_id}.md"),
                "full_jd_html_path": str(JOB_PAGES / f"{job_id}.html"),
                "full_jd": full_jd,
                "full_jd_length": len(full_jd),
                "reject_phrases_found": reject_found,
                "evidence_level": evidence,
            })
            print(f"  status={result.status_code} success={result.success} full_jd_len={len(full_jd)} evidence={evidence}")
            enriched.append(merged)
            await asyncio.sleep(delay_s)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"haystack_enriched_full_jd_{run_stamp()}.json"
    out.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    print(f"Enriched JSON: {out}")
    return out


def load_pipeline(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else "# Job Pipeline\n\n## Pendientes\n\n## Processed\n"


def insert_pending(pipeline: str, entries: list[str]) -> str:
    if not entries:
        return pipeline
    block = "\n".join(entries) + "\n"
    m = re.search(r"(^##\s+Pendientes\s*$)", pipeline, flags=re.M) or re.search(r"(^##\s+Pending\s*$)", pipeline, flags=re.M)
    if m:
        return pipeline[:m.end()] + "\n" + block + pipeline[m.end():]
    return pipeline.rstrip() + "\n\n## Pendientes\n" + block


def export_to_career_ops(cfg: dict, input_path: Path | None = None, dry_run: bool = False) -> None:
    input_path = input_path or latest("haystack_enriched_full_jd_*.json")
    career_ops = Path((cfg.get("career_ops") or {}).get("workspace") or DEFAULT_CAREER_OPS)
    jds_dir = career_ops / "jds"
    pipeline_path = career_ops / "data" / "pipeline.md"
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    pipeline = load_pipeline(pipeline_path)
    entries = []
    imported = skipped = 0
    for job in jobs:
        if job.get("evidence_level") != "full_jd" or not (job.get("full_jd") or "").strip():
            skipped += 1
            continue
        job_id = job.get("job_id") or slug(job.get("url", ""), 32)
        company = job.get("company") or "Unknown company"
        role = job.get("role_title") or "Unknown role"
        jd_name = f"haystack-{job_id}-{slug(company, 32)}-{slug(role, 44)}.md"
        jd_rel = f"jds/{jd_name}"
        if job.get("url", "") in pipeline or f"local:{jd_rel}" in pipeline or job_id in pipeline:
            skipped += 1
            continue
        body = f"""---
source: haystack
source_url: {json.dumps(job.get('url') or '')}
haystack_job_id: {json.dumps(job_id)}
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

- Source: Haystack
- Source URL: {job.get('url') or ''}
- Haystack job id: {job_id}
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
        entries.append(" | ".join([
            f"- [ ] local:{jd_rel}", company, role, job.get("location") or "", job.get("salary") or "",
            f"note: Haystack full JD import; source={job.get('url')}; job_id={job_id}",
        ]))
        imported += 1
    if not dry_run:
        pipeline_path.write_text(insert_pending(pipeline, entries), encoding="utf-8")
    print(json.dumps({"input": str(input_path), "career_ops": str(career_ops), "dry_run": dry_run, "imported_count": imported, "skipped_count": skipped}, indent=2))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan", "enrich", "export", "run"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--input")
    ap.add_argument("--limit", type=int, help="limit search pages for scan")
    ap.add_argument("--top", type=int)
    ap.add_argument("--delay", type=float)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config(ROOT / args.config)
    if args.command == "scan":
        await scan_searches(cfg, args.limit)
    elif args.command == "enrich":
        await enrich(cfg, Path(args.input) if args.input else None, args.top, args.delay)
    elif args.command == "export":
        export_to_career_ops(cfg, Path(args.input) if args.input else None, args.dry_run)
    elif args.command == "run":
        dedup = await scan_searches(cfg, args.limit)
        enriched = await enrich(cfg, dedup, args.top, args.delay)
        export_to_career_ops(cfg, enriched, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
