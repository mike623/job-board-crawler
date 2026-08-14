from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
REPORTS = OUTPUTS / "reports"

BOARDS = ["reed", "totaljobs", "indeed"]


def parse_report_filename(path: Path) -> dict:
    # Examples:
    # reed_deduped_2026-08-06_165857.json
    # totaljobs_enriched_full_jd_2026-08-06_185002.json
    m = re.match(r"(?P<board>[^_]+)_(?P<kind>.+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{6})\.json$", path.name)
    if not m:
        return {"board": path.parts[-3] if len(path.parts) >= 3 else "unknown", "kind": "unknown", "stamp": path.stem}
    d = m.groupdict()
    d["stamp"] = f"{d['date']}_{d['time']}"
    d["display_time"] = f"{d['date']} {d['time'][:2]}:{d['time'][2:4]}:{d['time'][4:]}"
    return d


def safe(s) -> str:
    return html.escape("" if s is None else str(s))


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e)}


def job_title(job: dict) -> str:
    return job.get("role_title") or job.get("title") or job.get("role") or "Unknown role"


def job_company(job: dict) -> str:
    return job.get("company") or "Unknown company"


def job_id(job: dict, board: str) -> str:
    return job.get("job_id") or job.get(f"{board}_job_id") or job.get("reed_job_id") or job.get("totaljobs_job_id") or job.get("indeed_job_id") or ""


def collect_rows() -> tuple[list[dict], dict]:
    rows: list[dict] = []
    runs = defaultdict(lambda: {"raw": 0, "deduped": 0, "enriched": 0, "full_jd": 0, "rejected": 0, "files": []})

    for board in BOARDS:
        reports_dir = OUTPUTS / board / "reports"
        if not reports_dir.exists():
            continue
        for path in sorted(reports_dir.glob(f"{board}_*.json")):
            meta = parse_report_filename(path)
            kind = meta.get("kind", "unknown")
            stamp = meta.get("stamp", path.stem)
            data = load_json(path)
            jobs = data if isinstance(data, list) else []
            run_key = f"{board}:{stamp}"
            runs[run_key].update({"board": board, "stamp": stamp, "display_time": meta.get("display_time", stamp)})
            runs[run_key]["files"].append(str(path.relative_to(ROOT)))
            if "raw" in kind:
                runs[run_key]["raw"] += len(jobs)
            elif "deduped" in kind:
                runs[run_key]["deduped"] += len(jobs)
            elif "enriched" in kind:
                runs[run_key]["enriched"] += len(jobs)
                runs[run_key]["full_jd"] += sum(1 for j in jobs if j.get("evidence_level") == "full_jd")
                runs[run_key]["rejected"] += sum(1 for j in jobs if j.get("evidence_level") == "rejected")

            for idx, job in enumerate(jobs, 1):
                rows.append({
                    "board": board,
                    "run_stamp": stamp,
                    "run_time": meta.get("display_time", stamp),
                    "stage": kind,
                    "row": idx,
                    "job_id": job_id(job, board),
                    "title": job_title(job),
                    "company": job_company(job),
                    "location": job.get("location") or job.get("search_location") or "",
                    "salary": job.get("salary") or "",
                    "contract": job.get("contract") or "",
                    "score": job.get("score", ""),
                    "evidence": job.get("evidence_level") or ("search_result_card" if "deduped" in kind or "raw" in kind else ""),
                    "status": job.get("full_jd_status_code") or "",
                    "jd_len": job.get("full_jd_length") or "",
                    "reject": ", ".join(job.get("reject_phrases_found") or []),
                    "url": job.get("url") or job.get("source_url") or "",
                    "jd_file": job.get("full_jd_markdown_path") or "",
                    "source_json": str(path.relative_to(ROOT)),
                })
    return rows, runs


def render(rows: list[dict], runs: dict) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    board_counts = defaultdict(int)
    evidence_counts = defaultdict(int)
    for r in rows:
        board_counts[r["board"]] += 1
        evidence_counts[r["evidence"] or "unknown"] += 1

    run_cards = []
    for key, run in sorted(runs.items(), key=lambda kv: kv[1].get("stamp", ""), reverse=True):
        run_cards.append(f"""
        <tr>
          <td><code>{safe(run.get('stamp'))}</code></td>
          <td>{safe(run.get('board'))}</td>
          <td>{safe(run.get('display_time'))}</td>
          <td class="num">{run.get('raw', 0)}</td>
          <td class="num">{run.get('deduped', 0)}</td>
          <td class="num">{run.get('enriched', 0)}</td>
          <td class="num good">{run.get('full_jd', 0)}</td>
          <td class="num warn">{run.get('rejected', 0)}</td>
          <td>{'<br>'.join(f'<code>{safe(f)}</code>' for f in run.get('files', []))}</td>
        </tr>""")

    job_rows = []
    for r in sorted(rows, key=lambda x: (x["run_stamp"], x["board"], x["stage"], x["row"]), reverse=True):
        url = f"<a href='{safe(r['url'])}' target='_blank'>source</a>" if r["url"] else ""
        jd_file = r["jd_file"]
        jd = f"<code>{safe(jd_file)}</code>" if jd_file else ""
        cls = ""
        if r["evidence"] == "full_jd":
            cls = "good"
        elif r["evidence"] == "rejected":
            cls = "bad"
        elif r["stage"].startswith("deduped"):
            cls = "muted"
        job_rows.append(f"""
        <tr data-board="{safe(r['board'])}" data-stage="{safe(r['stage'])}" data-evidence="{safe(r['evidence'])}">
          <td><code>{safe(r['run_stamp'])}</code></td>
          <td>{safe(r['board'])}</td>
          <td>{safe(r['stage'])}</td>
          <td><code>{safe(r['job_id'])}</code></td>
          <td>{safe(r['title'])}</td>
          <td>{safe(r['company'])}</td>
          <td>{safe(r['location'])}</td>
          <td>{safe(r['salary'])}</td>
          <td>{safe(r['contract'])}</td>
          <td class="num">{safe(r['score'])}</td>
          <td class="{cls}">{safe(r['evidence'])}</td>
          <td class="num">{safe(r['status'])}</td>
          <td class="num">{safe(r['jd_len'])}</td>
          <td class="bad">{safe(r['reject'])}</td>
          <td>{url}</td>
          <td>{jd}</td>
          <td><code>{safe(r['source_json'])}</code></td>
        </tr>""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Job-board Crawl Runs</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; line-height: 1.35; }}
  h1, h2 {{ margin-bottom: 0.35rem; }}
  .meta {{ color: #777; margin-top: 0; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0 24px; }}
  .card {{ border: 1px solid #ccc; border-radius: 10px; padding: 12px 16px; min-width: 150px; }}
  .card b {{ font-size: 1.4rem; display: block; }}
  .controls {{ position: sticky; top: 0; z-index: 2; background: Canvas; border: 1px solid #ccc; border-radius: 10px; padding: 12px; margin: 20px 0; }}
  input, select {{ padding: 6px; margin-right: 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 8px; vertical-align: top; }}
  th {{ position: sticky; top: 70px; background: Canvas; z-index: 1; }}
  tr:nth-child(even) {{ background: color-mix(in srgb, CanvasText 4%, Canvas); }}
  code {{ font-size: 12px; word-break: break-all; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .good {{ color: #128a2e; font-weight: 600; }}
  .warn {{ color: #b36b00; font-weight: 600; }}
  .bad {{ color: #b00020; font-weight: 600; }}
  .muted {{ color: #777; }}
</style>
<script>
function applyFilters() {{
  const q = document.getElementById('q').value.toLowerCase();
  const board = document.getElementById('board').value;
  const evidence = document.getElementById('evidence').value;
  let shown = 0;
  document.querySelectorAll('#jobs tbody tr').forEach(tr => {{
    const okQ = !q || tr.innerText.toLowerCase().includes(q);
    const okB = !board || tr.dataset.board === board;
    const okE = !evidence || tr.dataset.evidence === evidence;
    const show = okQ && okB && okE;
    tr.style.display = show ? '' : 'none';
    if (show) shown++;
  }});
  document.getElementById('shown').innerText = shown;
}}
</script>
</head>
<body>
<h1>Job-board Crawl Runs</h1>
<p class="meta">Generated {safe(generated)} from <code>{safe(str(OUTPUTS))}</code></p>
<div class="cards">
  <div class="card"><b>{len(runs)}</b> runs</div>
  <div class="card"><b>{len(rows)}</b> job rows</div>
  {''.join(f'<div class="card"><b>{count}</b>{safe(board)} rows</div>' for board, count in sorted(board_counts.items()))}
  {''.join(f'<div class="card"><b>{count}</b>{safe(ev)}</div>' for ev, count in sorted(evidence_counts.items()))}
</div>

<h2>Runs</h2>
<table id="runs">
<thead><tr><th>Run</th><th>Board</th><th>Time</th><th>Raw</th><th>Deduped</th><th>Enriched</th><th>Full JD</th><th>Rejected</th><th>Files</th></tr></thead>
<tbody>{''.join(run_cards)}</tbody>
</table>

<h2>Jobs crawled by run</h2>
<div class="controls">
  Search <input id="q" oninput="applyFilters()" placeholder="title/company/location/id" size="34" />
  Board <select id="board" onchange="applyFilters()"><option value="">all</option>{''.join(f'<option value="{safe(b)}">{safe(b)}</option>' for b in BOARDS)}</select>
  Evidence <select id="evidence" onchange="applyFilters()"><option value="">all</option>{''.join(f'<option value="{safe(e)}">{safe(e)}</option>' for e in sorted(evidence_counts))}</select>
  Showing <b id="shown">{len(rows)}</b>
</div>
<table id="jobs">
<thead><tr><th>Run</th><th>Board</th><th>Stage</th><th>Job ID</th><th>Title</th><th>Company</th><th>Location</th><th>Salary</th><th>Contract</th><th>Score</th><th>Evidence</th><th>Status</th><th>JD chars</th><th>Reject reason</th><th>URL</th><th>JD file</th><th>Source JSON</th></tr></thead>
<tbody>{''.join(job_rows)}</tbody>
</table>
</body>
</html>"""


def main() -> None:
    rows, runs = collect_rows()
    REPORTS.mkdir(parents=True, exist_ok=True)
    html_text = render(rows, runs)
    out = REPORTS / "crawl_runs.html"
    out.write_text(html_text, encoding="utf-8")
    # Convenience copy at outputs root for browser/open commands.
    (OUTPUTS / "crawl_runs.html").write_text(html_text, encoding="utf-8")
    print(json.dumps({"html": str(out), "copy": str(OUTPUTS / "crawl_runs.html"), "runs": len(runs), "job_rows": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
