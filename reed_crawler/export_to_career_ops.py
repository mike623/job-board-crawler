from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REED_REPORTS = ROOT / "outputs" / "reed" / "reports"
DEFAULT_CAREER_OPS = Path(os.environ.get("CAREER_OPS_WORKSPACE") or ROOT.parent / "career-ops")


def slug(s: str, max_len: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


def latest_enriched() -> Path:
    files = sorted(REED_REPORTS.glob("reed_enriched_full_jd_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No reed_enriched_full_jd_*.json found. Run enrich_full_jds.py first.")
    return files[0]


def load_existing_pipeline(path: Path) -> str:
    if not path.exists():
        return "# Job Pipeline\n\n## Pendientes\n\n## Processed\n"
    return path.read_text(encoding="utf-8")


def has_duplicate(pipeline_text: str, job: dict, jd_rel: str) -> bool:
    needles = [
        job.get("job_id", ""),
        job.get("url", ""),
        f"local:{jd_rel}",
    ]
    return any(n and n in pipeline_text for n in needles)


def make_jd_markdown(job: dict) -> str:
    title = job.get("role_title") or "Unknown role"
    company = job.get("company") or "Unknown company"
    full_jd = (job.get("full_jd") or "").strip()
    source_url = job.get("url") or ""
    now = datetime.now().strftime("%Y-%m-%d")
    return f"""---
source: reed
source_url: {json.dumps(source_url)}
reed_job_id: {json.dumps(job.get('job_id') or '')}
company: {json.dumps(company)}
role: {json.dumps(title)}
location: {json.dumps(job.get('location') or '')}
salary: {json.dumps(job.get('salary') or '')}
contract: {json.dumps(job.get('contract') or '')}
posted: {json.dumps(job.get('posted') or '')}
imported: {now}
evidence_level: full_jd
---

# {title} — {company}

- Source: Reed
- Source URL: {source_url}
- Reed job id: {job.get('job_id') or ''}
- Location: {job.get('location') or ''}
- Salary: {job.get('salary') or ''}
- Contract: {job.get('contract') or ''}
- Posted: {job.get('posted') or ''}

## Full job description

{full_jd}
"""


def insert_pending_entries(pipeline_text: str, entries: list[str]) -> str:
    if not entries:
        return pipeline_text
    block = "\n".join(entries) + "\n"
    m = re.search(r"(^##\s+Pendientes\s*$)", pipeline_text, flags=re.M)
    if m:
        insert_at = m.end()
        return pipeline_text[:insert_at] + "\n" + block + pipeline_text[insert_at:]
    m = re.search(r"(^##\s+Pending\s*$)", pipeline_text, flags=re.M)
    if m:
        insert_at = m.end()
        return pipeline_text[:insert_at] + "\n" + block + pipeline_text[insert_at:]
    return pipeline_text.rstrip() + "\n\n## Pendientes\n" + block


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None, help="reed_enriched_full_jd_*.json; defaults to latest")
    ap.add_argument("--career-ops", default=str(DEFAULT_CAREER_OPS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    input_path = Path(args.input) if args.input else latest_enriched()
    career_ops = Path(args.career_ops)
    jds_dir = career_ops / "jds"
    pipeline_path = career_ops / "data" / "pipeline.md"
    report_dir = career_ops / "reports"

    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    if args.limit:
        jobs = jobs[: args.limit]

    pipeline_text = load_existing_pipeline(pipeline_path)
    entries: list[str] = []
    imported = []
    skipped = []

    for job in jobs:
        if job.get("evidence_level") != "full_jd" or not (job.get("full_jd") or "").strip():
            skipped.append({"job_id": job.get("job_id"), "reason": "missing full_jd evidence"})
            continue
        job_id = job.get("job_id") or slug(job.get("url", ""), 32)
        role = job.get("role_title") or "Unknown role"
        company = job.get("company") or "Unknown company"
        jd_name = f"reed-{job_id}-{slug(company, 32)}-{slug(role, 44)}.md"
        jd_path = jds_dir / jd_name
        jd_rel = f"jds/{jd_name}"
        if has_duplicate(pipeline_text, job, jd_rel):
            skipped.append({"job_id": job_id, "reason": "duplicate in pipeline"})
            continue
        entry = " | ".join([
            f"- [ ] local:{jd_rel}",
            company,
            role,
            job.get("location") or "",
            job.get("salary") or "",
            f"note: Reed full JD import; source={job.get('url')}; job_id={job_id}",
        ])
        entries.append(entry)
        imported.append({"job_id": job_id, "company": company, "role": role, "jd_path": str(jd_path), "pipeline_entry": entry})
        if not args.dry_run:
            jds_dir.mkdir(parents=True, exist_ok=True)
            jd_path.write_text(make_jd_markdown(job), encoding="utf-8")

    new_pipeline = insert_pending_entries(pipeline_text, entries)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    summary = {
        "input": str(input_path),
        "career_ops": str(career_ops),
        "dry_run": args.dry_run,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "imported": imported,
        "skipped": skipped,
    }
    if not args.dry_run:
        pipeline_path.write_text(new_pipeline, encoding="utf-8")
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"reed-import-summary-{stamp}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
