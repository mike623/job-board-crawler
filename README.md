# Job Board Crawler

Standalone Crawl4AI job-board discovery pipelines for Reed, Totaljobs, Indeed, and Talent.com.

The project keeps job-board scraping separate from downstream evaluation/import logic:

- **Discovery scans** crawl low-volume search result pages and write raw/deduped evidence under `outputs/`.
- **Optional enrichment** can fetch selected full job descriptions for boards where that path is implemented.
- **Downstream import** only ever materialises full-JD evidence; search-card snippets are never exported.

## Boards

Current board support:

- Reed search-card scan + full-JD enrichment/export.
- Totaljobs search-card scan + full-JD enrichment/export.
- Indeed smoke/experimental scan, disabled by default.
- Talent.com search-card scan using explicit `k` / `l` search params and optional selected result `id` hydration.

## Setup

```bash
git clone git@github.com:mike623/job-board-crawler.git
cd job-board-crawler
cp config.example.yml config.yml   # then edit for your own search
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
crawl4ai-doctor
```

If using `uv`:

```bash
uv venv
. .venv/bin/activate
uv pip install -r requirements.txt
```

## Validate generated search URLs without crawling

```bash
.venv/bin/python reed_crawler/board_config.py
```

## Run low-volume discovery scans

```bash
# Reed
.venv/bin/python reed_crawler/run_reed_scan.py --config config.yml

# Totaljobs
.venv/bin/python reed_crawler/totaljobs_pipeline.py scan --config config.yml

# Talent.com: keep --limit 1 for smoke tests to avoid rate-limit pressure
.venv/bin/python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1
```

## Talent.com notes

Talent.com can return an empty shell for `https://uk.talent.com/jobs?k=...&l=...` even when the browser shows hydrated results. The current config supports an optional selected result `id` param:

```yaml
boards:
  talent:
    max_pages_per_run: 2
    delay_seconds: 60
    search_params:
      - k: Senior Software Engineer
        l: london
        id: "<a live result id>"
```

That produces:

```text
https://uk.talent.com/jobs?k=Senior+Software+Engineer&l=london&id=<a live result id>
```

Keep Talent scans low-volume (`max_pages_per_run: 2`, `delay_seconds: 60`) to reduce rate-limit and block risk.

## Enrich and export full JDs

Downstream evaluation should use full job descriptions, not search-card snippets. After a Reed scan, enrich top scored jobs:

```bash
.venv/bin/python reed_crawler/enrich_full_jds.py --top 20
```

For board-specific pipelines with built-in enrich/export commands:

```bash
.venv/bin/python reed_crawler/totaljobs_pipeline.py enrich --config config.yml --top 5
.venv/bin/python reed_crawler/totaljobs_pipeline.py export --config config.yml --dry-run
```

Use dry-run first before writing to the downstream workspace. That target comes from `career_ops.workspace` in `config.yml`, overridable with the `CAREER_OPS_WORKSPACE` env var.

## Outputs

Runtime outputs are intentionally ignored by git:

```text
outputs/<board>/raw/      # per-search markdown/html/link captures
outputs/<board>/reports/  # JSON + Markdown summaries
outputs/<board>/job_pages/ # optional full-JD captures
```

## Visualize crawl runs

Generate a local HTML dashboard listing every run and every job row:

```bash
.venv/bin/python reed_crawler/generate_html_report.py
open outputs/crawl_runs.html
```

## Tests and checks

```bash
.venv/bin/python -m py_compile reed_crawler/*.py
.venv/bin/python -m pytest
```

If `pytest` is not installed in the venv:

```bash
uv pip install pytest
```

## Cron usage

If you drive this from cron, run the `scan` commands only and keep enrich/export as a manual, reviewed step. Keep the slow-mode delays in `config.yml` — they exist to avoid getting blocked, not because they are untuned.
