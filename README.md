# :mag: job-board-crawler

![Python version](https://img.shields.io/badge/python-3.11%2B-blue.svg)
[![Crawl4AI](https://img.shields.io/badge/built%20with-Crawl4AI-6f42c1.svg)](https://github.com/unclecode/crawl4ai)
![Boards](https://img.shields.io/badge/boards-Reed%20%7C%20Totaljobs%20%7C%20Indeed%20%7C%20Talent.com-success.svg)

> Low-volume job-board discovery pipelines that collect **full job descriptions**, not search-result snippets

Job boards render their listings with JavaScript, rate-limit aggressively, and show you a truncated teaser instead of the actual advert. This project drives a real headless browser through four UK job boards, scores what it finds, then fetches the complete job description for only the best matches — slowly enough that you don't get blocked.

Every stage writes plain timestamped JSON to disk, so a run is inspectable, resumable, and diffable.

```bash
# Find jobs
python reed_crawler/run_reed_scan.py --config config.yml

# Fetch the full advert for the top 10 matches
python reed_crawler/enrich_full_jds.py --top 10

# Preview what would be exported downstream
python reed_crawler/export_to_career_ops.py --dry-run
```

## Features

- **Four boards, one config.** Reed, Totaljobs, Indeed, and Talent.com, each driven from a single `config.yml`.
- **Full job descriptions.** Search cards are treated as leads only. The export stage refuses to emit anything that isn't a verified full advert.
- **Built-in relevance scoring.** Title, seniority, tech-stack, location, and salary signals rank leads so enrichment spends its crawl budget on the top matches.
- **Slow by design.** Per-board page caps and configurable delays keep request volume low enough to avoid bans.
- **Resumable stages.** Each stage reads the newest JSON from the previous one, so scan today and enrich tomorrow.
- **Deduplication.** Within a run by job ID, and across runs by checking the downstream pipeline before re-importing.
- **Local HTML dashboard.** Every run and every job row in one filterable static page — no server required.
- **Junk filtering.** Indeed postings matching configured reject phrases ("not represent a live vacancy", "talent pool") are flagged rather than imported.

## How it works

Each board runs the same stages. Stages communicate only through timestamped JSON files, never in memory, so you can stop after any one of them.

```
config.yml
    │
    ▼
┌─────────┐   search result pages     outputs/<board>/raw/*.html,*.md
│  scan   │──────────────────────▶    outputs/<board>/reports/<board>_raw_<stamp>.json
└─────────┘   parse → dedupe → score  outputs/<board>/reports/<board>_deduped_<stamp>.json
    │
    ▼
┌─────────┐   top-N job detail pages  outputs/<board>/job_pages/<job_id>.html,.md
│ enrich  │──────────────────────▶    outputs/<board>/reports/<board>_enriched_full_jd_<stamp>.json
└─────────┘   sets evidence_level
    │
    ▼
┌─────────┐   full_jd records only    <workspace>/jds/<board>-<id>-<company>-<role>.md
│ export  │──────────────────────▶    <workspace>/data/pipeline.md
└─────────┘   skips duplicates
```

The `evidence_level` field is the gate between discovery and export. It is set to `full_jd` only when the detail-page crawl succeeded **and** yielded more than 500 characters of description text. Everything else stays `search_result_card` — or `rejected` on Indeed, when a reject phrase matched — and is skipped by the export stage.

> [!NOTE]
> The four board pipelines intentionally duplicate their scoring, dedupe, and browser-config helpers rather than sharing a base class. Each board's markup is quirky in its own way, and keeping them independent means a change to fix Totaljobs can't silently break Reed.

## Getting started

### Prerequisites

- Python 3.11 or later
- A Chromium install for Playwright, which `crawl4ai-doctor` sets up for you

### Installation

```bash
git clone git@github.com:mike623/job-board-crawler.git
cd job-board-crawler

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

# Install and verify the headless browser
crawl4ai-doctor
```

Using [uv](https://github.com/astral-sh/uv) instead:

```bash
uv venv && . .venv/bin/activate
uv pip install -r requirements.txt
```

### Configuration

Copy the template and edit it for your own search:

```bash
cp config.example.yml config.yml
```

`config.yml` is gitignored — it holds your salary targets and locations.

Titles and locations are declared once in named groups, then referenced per board:

```yaml
search:
  titles:
    primary:
      - senior software engineer
      - backend developer
  locations:
    core:
      - london
      - manchester

boards:
  reed:
    enabled: true
    title_groups: [primary]
    location_groups: [core]
    proximity: 50
    max_pages_per_run: 8    # search pages per run
    full_jd:
      enabled: true
      top_n: 10             # detail pages per run
      delay_seconds: 12     # pause between detail crawls
```

Reed, Totaljobs, and Indeed build their URLs from the `title_groups` × `location_groups` cross product. Talent.com instead takes explicit query params — see [Talent.com](#talentcom) below.

Check what URLs your config produces without crawling anything:

```bash
python reed_crawler/board_config.py
```

```
reed: 8 urls
  - senior software engineer / london: https://www.reed.co.uk/jobs/senior-software-engineer-jobs-in-london?proximity=50
  ...
```

> [!IMPORTANT]
> The low `max_pages_per_run`, low `top_n`, and multi-second `delay_seconds` defaults are deliberate, not untuned placeholders. Job boards block scrapers that move quickly. Raise these values gradually and expect to get blocked if you don't.

## Usage

### Scanning

Reed splits its stages across three scripts; the other boards use subcommands on a single script.

```bash
# Reed
python reed_crawler/run_reed_scan.py --config config.yml

# Totaljobs
python reed_crawler/totaljobs_pipeline.py scan --config config.yml

# Talent.com — keep smoke tests to one page
python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1

# Indeed — disabled in config by default
python reed_crawler/indeed_pipeline.py scan --config config.yml --allow-disabled
```

`--limit N` caps the number of search pages for a run, overriding `max_pages_per_run`. Use it for smoke tests.

### Enriching and exporting

```bash
# Reed
python reed_crawler/enrich_full_jds.py --top 20
python reed_crawler/export_to_career_ops.py --dry-run

# Totaljobs, Indeed
python reed_crawler/totaljobs_pipeline.py enrich --config config.yml --top 5
python reed_crawler/totaljobs_pipeline.py export --config config.yml --dry-run

# All three stages back to back
python reed_crawler/totaljobs_pipeline.py run --config config.yml --dry-run
```

With no `--input`, each stage picks up the newest JSON produced by the previous one, so the commands above chain naturally across separate sessions.

> [!WARNING]
> `export` writes markdown files and appends to `data/pipeline.md` in an external workspace. Always run it with `--dry-run` first and read the JSON summary it prints.

The export target resolves in this order:

1. The `CAREER_OPS_WORKSPACE` environment variable
2. `career_ops.workspace` in `config.yml`
3. A `../career-ops` directory next to this repo

Exported job descriptions are markdown files with YAML frontmatter:

```markdown
---
source: reed
source_url: "https://www.reed.co.uk/jobs/senior-software-engineer/12345678"
company: "Example Ltd"
role: "Senior Software Engineer"
salary: "£70,000 - £80,000 per annum"
evidence_level: full_jd
---

# Senior Software Engineer — Example Ltd
...
```

Re-running `export` is safe: a job is skipped when its job ID, source URL, or target filename already appears in the downstream `pipeline.md`.

### Board reference

| Board | Stages | URL strategy | Notes |
| --- | --- | --- | --- |
| **Reed** | scan, enrich, export | Slug path + `proximity` | Markdown H2 parsing; the most reliable board |
| **Totaljobs** | scan, enrich, export, run | Slug path + `radius` | Job links harvested from the crawl link graph |
| **Indeed** | scan, enrich, export, run | Query params + `radius` | Disabled by default; supports `reject_phrases` |
| **Talent.com** | scan | Explicit `k` / `l` params | Discovery only; no enrich or export path yet |

### Talent.com

Talent.com can return an unhydrated shell for `https://uk.talent.com/jobs?k=...&l=...` even when a real browser shows results. Supplying a live result `id` from a browser session forces the list to hydrate:

```yaml
boards:
  talent:
    max_pages_per_run: 2
    delay_seconds: 60
    search_params:
      - k: Senior Software Engineer
        l: london
        id: "611275213865225891"   # seeds hydration
      - k: Lead Software Engineer
        l: london
```

Which produces:

```
https://uk.talent.com/jobs?k=Senior+Software+Engineer&l=london&id=611275213865225891
```

Talent.com rate-limits harder than the other boards. Keep `max_pages_per_run` at 2 and `delay_seconds` at 60.

## Outputs

All runtime output lands under `outputs/`, which is gitignored:

```
outputs/<board>/raw/        # per-search markdown, HTML, and link captures
outputs/<board>/reports/    # timestamped JSON for each stage
outputs/<board>/job_pages/  # full job-description captures
```

Report filenames follow `<board>_<stage>_<YYYY-MM-DD>_<HHMMSS>.json`. Stage discovery and the dashboard both parse this shape, so don't rename them.

Raw HTML and markdown are kept for every page crawled. When a parser starts returning zero leads — which usually means the board changed its markup — the captures under `raw/` are what you diff to find out why.

### Dashboard

```bash
python reed_crawler/generate_html_report.py
open outputs/crawl_runs.html
```

A single self-contained HTML page listing every run and every job row, filterable by board, evidence level, and free text. Useful for spotting a board that silently stopped returning results.

## Testing

```bash
# Syntax check every module
python -m py_compile reed_crawler/*.py

# Unit tests — URL generation and Talent.com card parsing, no network required
python -m pytest

# A single test
python -m pytest tests/test_talent_pipeline.py::test_talent_markdown_card_parser_extracts_job_metadata
```

> [!NOTE]
> Tests assert against `config.example.yml`, not your personal `config.yml`. If you add a config key, add it to both files.

## Troubleshooting

**A scan returns zero jobs.** Check the raw capture in `outputs/<board>/raw/` for that search. An HTML file containing a consent wall or a CAPTCHA means you're being blocked — increase `crawl.delay_seconds` and reduce `max_pages_per_run`. Well-formed HTML with results present means the parser needs updating for changed markup.

**Enrichment produces `search_result_card` instead of `full_jd`.** The detail page either failed to load or yielded under 500 characters. The `full_jd_status_code` and `full_jd_error` fields in the enriched JSON tell you which, and the full page capture is in `outputs/<board>/job_pages/`.

**Export imports nothing.** Either no record reached `full_jd`, or every candidate was already present in the downstream `pipeline.md`. The JSON summary printed by `export` distinguishes the two in its `skipped` list.

**Crawls hang or time out.** Run `crawl4ai-doctor` to confirm the Playwright browser is installed and working. Set `crawl.headless: false` in `config.yml` to watch the browser and see where it stalls.

The `probe_*.py` scripts at the repo root are standalone single-URL crawls, useful for testing a board's response in isolation without running a full pipeline.
