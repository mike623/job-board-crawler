# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Scripts run as file paths from the repo root using the venv interpreter (not `python -m`, no console scripts). The dashboard is the exception — it is a package.

```bash
# Setup
python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt
crawl4ai-doctor
cp config.example.yml config.yml       # config.yml is gitignored

# Dashboard
.venv/bin/python -m dashboard                  # 127.0.0.1:8080
.venv/bin/python -m dashboard --reload         # dev

# Scans (the dashboard shells out to exactly these)
.venv/bin/python reed_crawler/run_reed_scan.py --config config.yml [--limit N]
.venv/bin/python reed_crawler/totaljobs_pipeline.py scan --config config.yml [--limit N]
.venv/bin/python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1
.venv/bin/python reed_crawler/indeed_pipeline.py scan --config config.yml --allow-disabled

# Validate generated URLs without crawling — fast check after config or URL-builder edits
.venv/bin/python reed_crawler/board_config.py

# Checks
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_salary.py::test_observed_formats
.venv/bin/python -m py_compile reed_crawler/*.py dashboard/*.py
```

## Architecture

Two halves. `reed_crawler/` collects listings; `dashboard/` renders them. They share nothing but the files on disk.

```
config.yml → scan (per-board lock, jittered delays)
               ├─ outputs/<board>/raw/<search>__<stamp>.{md,html}
               └─ outputs/<board>/reports/<board>_deduped_<stamp>.json
                        │
                        ▼  re-read per request, no index, no cache
                  dashboard  → / · /jobs · /jobs/<board>/<id> · /runs · /export.csv
                             → POST /scan/<board>, /scan-all  (subprocess + SSE)
```

**Crawler modules.** `board_config.py` builds every board's URLs and owns `run_stamp`, `raw_capture_stem` and `jittered`. `salary.py` and `scan_lock.py` and `scan_health.py` are shared. Each board then has its own parsing: `reed_utils.py` + `run_reed_scan.py`, `totaljobs_pipeline.py`, `talent_pipeline.py`, `indeed_pipeline.py`.

**Dashboard modules.** `aggregate.py` is the only place that reads report JSON — jobs, board summaries, runs, filtering and sorting. `pipeline.py` reads the downstream workspace. `scans.py` runs scans as subprocesses and persists their records. `pool.py` bounds concurrency. `app.py` is routes only; `templates/` extends `base.html`.

**Duplicated by design.** `slug`, `dedupe`, `browser_config` and `crawl_config` are copy-pasted across the board modules with per-board variations. Each board's markup is quirky in its own way, and keeping them independent means a fix for Totaljobs cannot break Reed. When changing crawl behaviour, decide explicitly whether it applies to one board or all, and edit each copy.

## Invariants

Each of these was learned from a bug. Breaking one silently corrupts data or gets a board blocked.

- **Per-host request rate is the safety property, not worker count.** Boards are separate hosts, so scanning them concurrently is free. Within a host the limit is `max(1, len(proxies))` — splitting by search term changes *what* is asked for, not *how often*, because rate limits are per IP. Never let pool size govern this.
- **Raw captures carry the run stamp.** They were once written to a deterministic name, so each scan destroyed the previous evidence for that search and concurrent scans corrupted each other. `raw_capture_stem` exists for this.
- **An empty page body is a failure, not zero results.** A crawl can return success with nothing in it. `scan_health` classifies this so a board cannot silently stop producing data.
- **One scan per board.** The lock lives in the scan entrypoints so the external cron inherits it without being modified. Exit 75 means busy, not broken.
- **The downstream workspace is read-only.** `dashboard/pipeline.py` only ever reads it. A test asserts nothing under it is modified.
- **Report filenames are a contract.** `<board>_<stage>_<YYYY-MM-DD>_<HHMMSS>.json`. Stage discovery and every aggregation parse this shape.

## Config

`config.yml` is the single input; `config.example.yml` is the committed template and `config.yml` is gitignored. Board sections reference named groups from `search.titles` / `search.locations`. The flat top-level keys at the bottom are a legacy fallback still read by `run_reed_scan.build_specs`.

`tests/test_talent_pipeline.py` asserts against **`config.example.yml`**, so editing its talent block or `max_pages_per_run` breaks that test — update both together, and keep the two files structurally in sync.

Keys that no longer do anything: every board's `full_jd` block, `career_ops.import_only_evidence_level`, and Indeed's `reject_phrases`, which only ever ran against job-description text.

## Sunset code

Full job descriptions are no longer fetched or exported; the project collects listings. These modules are kept for reference, marked `SUNSET` in their first docstring, and wired to nothing: `enrich_full_jds.py`, `export_to_career_ops.py`, `test_full_jd.py` (a probe, not a test), `manual_totaljobs_crawl4ai_import.py`, and the enrich/export subcommands inside `totaljobs_pipeline.py` and `indeed_pipeline.py`. Do not build on them without checking whether that is intended.

## Cron

The external daily script calls one command and nothing else:

```bash
.venv/bin/python reed_crawler/scan_all.py --config config.yml
```

`scan_all.py` reads `boards.<name>.enabled` and runs each enabled board's entrypoint as a subprocess, so enabling or disabling a board is a config edit and never a change to a script outside this repo. It exits 0 when every board succeeded or was already locked (75), and 1 if any board failed.

`COMMANDS` in `scan_all.py` is the single table of how each board is scanned; `dashboard/scans.py` imports it so the button and the cron cannot drift. Do not add `--allow-disabled` to it — that flag is for manual smoke tests and would defeat the config.

## Agent skills

### Issue tracker

GitHub Issues on `mike623/job-board-crawler`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
