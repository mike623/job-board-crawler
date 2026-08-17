# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All scripts run as file paths from the repo root using the venv interpreter (not `python -m`, not installed console scripts):

```bash
# Setup
python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt
crawl4ai-doctor
cp config.example.yml config.yml   # config.yml is gitignored

# Validate generated search URLs without crawling (fast sanity check after config/URL-builder edits)
.venv/bin/python reed_crawler/board_config.py

# Discovery scans
.venv/bin/python reed_crawler/run_reed_scan.py --config config.yml [--limit N]
.venv/bin/python reed_crawler/totaljobs_pipeline.py scan --config config.yml [--limit N]
.venv/bin/python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1
.venv/bin/python reed_crawler/haystack_pipeline.py scan --config config.yml [--limit N]
.venv/bin/python reed_crawler/indeed_pipeline.py scan --config config.yml   # disabled in config; needs explicit enable

# Enrich + export (Reed uses two separate scripts; other boards use subcommands)
.venv/bin/python reed_crawler/enrich_full_jds.py --top 20
.venv/bin/python reed_crawler/export_to_career_ops.py --dry-run
.venv/bin/python reed_crawler/totaljobs_pipeline.py enrich --config config.yml --top 5
.venv/bin/python reed_crawler/totaljobs_pipeline.py export --config config.yml --dry-run
.venv/bin/python reed_crawler/totaljobs_pipeline.py run --config config.yml --dry-run   # scan+enrich+export
.venv/bin/python reed_crawler/haystack_pipeline.py enrich --config config.yml --top 5
.venv/bin/python reed_crawler/haystack_pipeline.py export --config config.yml --dry-run

# Dashboard
.venv/bin/python reed_crawler/generate_html_report.py && open outputs/crawl_runs.html

# Checks
.venv/bin/python -m py_compile reed_crawler/*.py
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_talent_pipeline.py::test_talent_markdown_card_parser_extracts_job_metadata
```

`--dry-run` first on any `export`: it writes JD files and `data/pipeline.md` into the downstream workspace, resolved as `CAREER_OPS_WORKSPACE` env var → `career_ops.workspace` in config → `../career-ops` sibling directory.

## Architecture

Five board pipelines share one shape but are deliberately **not** abstracted into a common base — each board's HTML/markdown quirks live in its own module.

**Stage pipeline (all boards):**

```
config.yml → build URLs → crawl search pages → parse leads → dedupe → score
  → outputs/<board>/reports/<board>_raw_<stamp>.json + <board>_deduped_<stamp>.json
  → enrich (crawl top-N job detail pages) → <board>_enriched_full_jd_<stamp>.json
  → export → career-ops/jds/*.md + career-ops/data/pipeline.md
```

Stages communicate **only through timestamped JSON files in `outputs/`**. Each stage's `latest_*()` helper globs `outputs/<board>/reports/` and picks the newest by mtime, so stages can be run days apart or resumed independently. Never change the `<board>_<kind>_<YYYY-MM-DD>_<HHMMSS>.json` filename shape — `generate_html_report.py:parse_report_filename` and every `latest_*()` glob depend on it.

**Module layout:**

- `board_config.py` — single source of URL construction for all five boards, plus `load_config`. `build_board_urls(cfg, board)` returns `[{board, title, location, url}]`, respects `enabled`, and truncates to `max_pages_per_run`. Talent uses the `search_params` branch (explicit `k`/`l`/`id`); the other boards use the `title_groups` × `location_groups` cross product.
- `reed_utils.py` — Reed-only `SearchSpec`/`Job` dataclasses, markdown parsing, dedupe, scoring, report writing. Has its own duplicate `reed_search_url`/`slug_text`; `board_config.py` is the newer canonical copy.
- `run_reed_scan.py` / `enrich_full_jds.py` / `export_to_career_ops.py` — Reed's three stages, split across three scripts (historical; Reed came first).
- `totaljobs_pipeline.py`, `indeed_pipeline.py` — single-file pipelines with `scan|enrich|export|run` subcommands.
- `talent_pipeline.py` — `scan` only; no enrich/export path exists yet.
- `haystack_pipeline.py` — same `scan|enrich|export|run` shape, but parses cards out of the rendered **HTML** with BeautifulSoup rather than markdown (see Invariants), and applies `reject_phrases` at enrich time the way Indeed does.
- `generate_html_report.py` — reads every `outputs/{reed,totaljobs,indeed,haystack}/reports/*.json` and renders one filterable static HTML table. Talent is not in its `BOARDS` list.
- `probe_*.py`, `test_full_jd.py`, `manual_totaljobs_crawl4ai_import.py` — one-off crawl4ai probes and a hardcoded-URL manual importer. Not part of the automated flow; `test_full_jd.py` is a script, not a pytest test.

**Duplicated-by-design code:** `score_lead`/`score_job`, `dedupe`, `slug`, `browser_config`, and `crawl_config` are copy-pasted across the board modules with small per-board variations. When changing scoring or crawl behaviour, decide explicitly whether the change applies to one board or all, and edit each copy.

## Invariants

- **Evidence level gates the export.** Career-Ops must only ever receive full job descriptions. `evidence_level` is set to `"full_jd"` only when the detail crawl succeeded *and* extracted text exceeds 500 chars (Indeed and Haystack additionally require no `reject_phrases` hit, else `"rejected"` — Haystack syndicates adverts that arrive truncated with "click apply for full job details"). Every `export` skips anything not `full_jd`. Do not loosen this without an explicit instruction — `config.yml:career_ops.import_only_evidence_level` records the boundary.
- **Slow mode is intentional.** Low `max_pages_per_run`, `top_n`, and multi-second `delay_seconds` values exist to avoid bans, not because they're untuned. Talent.com in particular wants `delay_seconds: 60` and `--limit 1` for smoke tests. Don't raise volumes or parallelise crawls to "speed things up".
- **Export dedupe is substring matching against `pipeline.md`.** A job is skipped if its `job_id`, `url`, or `local:jds/<file>` path already appears anywhere in the pipeline text. Short/numeric job ids can false-positive.
- **Haystack is parsed from HTML, not markdown.** haystack.cv renders a whole card as one link whose text concatenates title, company, location, salary and posted date with no separator, so markdown cannot recover the fields. `parse_search_cards` walks the HTML and anchors each field on the lucide icon that labels it (`lucide-building2` → company, `lucide-map-pin` → location, `lucide-banknote` → salary, `lucide-clock` → posted). The surrounding utility classes are generated and will churn; the icon names are the stable part.
- **An empty Haystack scan is normal.** Its search backend intermittently answers "Something went wrong loading jobs"; the scan retries once, then records zero leads. Its free-text `q` also matches loosely (a "fullstack" query returns sales roles) and appears to require every term, so multi-word titles return far fewer results than short ones. Pagination is a "Load More" button, so a scan sees only the first ~20 cards per search — deliberate, in keeping with slow mode.
- **Talent.com needs a seed `id`.** `https://uk.talent.com/jobs?k=...&l=...` can return an unhydrated shell; the first `search_params` entry carries a real result `id` to force hydration. Talent parsing prefers markdown cards (`parse_markdown_cards`) and only falls back to link scraping.

## Config

`config.yml` is the single input for every pipeline. Board sections (`boards.<name>`) reference named groups from `search.titles` / `search.locations`. The flat top-level `titles`/`locations`/`proximity`/`max_pages_per_run` keys at the bottom are a legacy fallback path still read by `run_reed_scan.build_specs`; prefer the `boards`/`search` sections in new code.

`config.yml` is gitignored and personal; `config.example.yml` is the committed template. `tests/test_talent_pipeline.py` asserts against **`config.example.yml`** (it expects exactly 2 talent URLs, matching `max_pages_per_run: 2`), so editing the example's talent block or `max_pages_per_run` breaks that test — update both together. Keep the two files structurally in sync when adding config keys.

## Cron

This project is driven from an external daily cron script that runs Reed, Totaljobs, and Talent **scan only** — no enrich, no export. Changes to scan CLI flags or output paths can break that caller, which is not in this repo.

## Agent skills

### Issue tracker

GitHub Issues on `mike623/job-board-crawler`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
