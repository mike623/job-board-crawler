# :mag: job-board-crawler

![Python version](https://img.shields.io/badge/python-3.11%2B-blue.svg)
[![Crawl4AI](https://img.shields.io/badge/built%20with-Crawl4AI-6f42c1.svg)](https://github.com/unclecode/crawl4ai)
[![FastAPI](https://img.shields.io/badge/dashboard-FastAPI-009485.svg)](https://fastapi.tiangolo.com/)
![Boards](https://img.shields.io/badge/boards-Reed%20%7C%20Totaljobs%20%7C%20Indeed%20%7C%20Talent.com%20%7C%20Adzuna-success.svg)

> Watches five UK job boards, keeps track of what appears and disappears, and shows you the difference

Job boards render their listings with JavaScript, rate-limit aggressively, and never tell you what changed since yesterday. This drives a real headless browser through Reed, Totaljobs, Indeed and Talent.com — slowly enough not to get blocked — reads Adzuna from its JSON API, then serves a local dashboard over everything it has collected.

Because every scan is kept, the dashboard can answer things a single search cannot: which jobs are new, which have quietly disappeared, how long one has been open, and which you have already dealt with.

```bash
python -m dashboard          # http://127.0.0.1:8080
```

## Features

- **Five boards, one config.** Reed, Totaljobs, Indeed, Talent.com and Adzuna, all driven from a single `config.yml`.
- **Job-centric history.** Every job appears once, with when it was first and last seen and how many scans have seen it.
- **Structured salary.** Free text like `£70k - 85k per year` or `71,250-118,000 Annual` becomes a sortable minimum, maximum and period.
- **Scan from the browser.** Start a board and watch its output stream live; the scan survives closing the page.
- **Slow by design.** Per-host request rates are bounded and delays are jittered, so concurrency never costs a board extra traffic.
- **One scan per board.** A file lock the cron inherits, so a manual scan and a scheduled one cannot collide.
- **Knows what you've actioned.** Cross-references a downstream workspace, read-only, so you can filter to what is untouched.
- **Honest failures.** A page that comes back empty is reported as a broken scan, not as a search with no matches.

## How it works

```
                    ┌──────────────┐
   config.yml ─────▶│     scan     │  one lock per board, jittered delays
                    └──────┬───────┘
                           │ writes, never overwrites
                           ▼
        outputs/<board>/raw/<search>__<stamp>.{md,html}   ← evidence
        outputs/<board>/reports/<board>_deduped_<stamp>.json
                           │
                           │ re-read on every request, no index
                           ▼
                    ┌──────────────┐
                    │  dashboard   │──▶ overview · jobs · runs · CSV
                    └──────────────┘
```

Nothing is cached and nothing is precomputed. The dashboard reads the same report files the crawler writes, so it cannot disagree with them, and deleting the service loses nothing.

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

crawl4ai-doctor              # installs and verifies the headless browser
cp config.example.yml config.yml
```

`config.yml` is gitignored — it holds your salary targets and locations.

### Configuration

Titles and locations are declared once in named groups, then referenced per board:

```yaml
search:
  titles:
    primary: [senior software engineer, backend developer]
  locations:
    core: [london, manchester]

boards:
  reed:
    enabled: true
    proxies: []             # egress identities; empty means one crawl at a time
    title_groups: [primary]
    location_groups: [core]
    proximity: 50
    max_pages_per_run: 8
```

Check what URLs your config produces without crawling anything:

```bash
python reed_crawler/board_config.py
```

> [!IMPORTANT]
> The low `max_pages_per_run` and multi-second `delay_seconds` defaults are deliberate. Job boards block scrapers that move quickly. Raise them gradually and expect to be blocked if you don't.

## The dashboard

```bash
python -m dashboard              # http://127.0.0.1:8080
python -m dashboard --reload     # restart on code and template changes
```

Bound to loopback only, with no host option: it can start scans, so it must not be reachable from the network.

| Page | What it answers |
| --- | --- |
| `/` | How is each board doing, and what shall I scan? |
| `/jobs` | What is out there, and what have I not looked at? |
| `/jobs/<board>/<id>` | What is this advert, and how long has it been open? |
| `/runs` | Did the crawler break? |
| `/export.csv` | Give me the current filter as a spreadsheet |

Filter jobs by board, by a pay floor, and by whether they have already reached your downstream workspace. Sort by pay, dates, company or how many times a job has been seen.

### Running scans

Scans take minutes, so they run as background subprocesses — exactly the commands the cron runs. Output streams to the browser, and closing the page does not stop the scan.

Scanning all boards runs them concurrently. They are separate hosts, so this costs no board a single extra request: within one host the limit stays at one crawl unless proxies are configured, because rate limits are per IP and splitting by search term changes what you ask for, not how often.

> [!TIP]
> With one worker per board the pool saturates at the number of enabled boards. It is there for queueing and visibility, not for speed.

## The command line

The dashboard is a convenience; every scan is a plain script.

```bash
python reed_crawler/scan_all.py --config config.yml [--limit N]   # every enabled board; what the cron runs

python reed_crawler/run_reed_scan.py --config config.yml [--limit N]
python reed_crawler/totaljobs_pipeline.py scan --config config.yml [--limit N]
python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1
python reed_crawler/indeed_pipeline.py scan --config config.yml [--allow-disabled]
python reed_crawler/adzuna_pipeline.py scan --config config.yml [--allow-disabled]
```

`scan_all.py` takes its board list from `boards.<name>.enabled`, so turning a board on or off is a config edit rather than a change to the cron. A single board's script still runs on its own; `--allow-disabled` scans Indeed when the config has it off, for manual smoke tests only.

`--limit N` caps the search pages for one run. Every scan takes its board's lock, so the cron, a terminal and the dashboard all stay out of each other's way; a scan that finds the board busy exits **75**, and one where no search returned a usable page exits non-zero.

### Board reference

| Board | Parsed from | Notes |
| --- | --- | --- |
| **Reed** | Markdown headings | Full field coverage; the most reliable board |
| **Totaljobs** | Markdown cards | Anchored on the card's `more` line |
| **Indeed** | HTML | Card links are click wrappers with no job id; the HTML has one |
| **Talent.com** | Markdown cards | Needs a seed result `id` to hydrate — see below |
| **Adzuna** | JSON API | Not crawled at all: the site 403s every bot. Needs free API credentials — see below |

### Talent.com

Talent.com can return an unhydrated shell for `https://uk.talent.com/jobs?k=...&l=...`. Supplying a live result `id` forces the list to render:

```yaml
boards:
  talent:
    max_pages_per_run: 2
    delay_seconds: 60
    search_params:
      - k: Senior Software Engineer
        l: london
        id: "611275213865225891"   # seeds hydration
```

It rate-limits harder than the others; keep the volume low.

### Adzuna

`adzuna.co.uk` sits behind CloudFront, which answers every automated request with a bare 403 — curl and headless Chromium alike, whatever user agent is offered. There is no markup to parse, so this board reads Adzuna's [JSON search API](https://developer.adzuna.com/) instead. Register free, then:

```yaml
boards:
  adzuna:
    enabled: true
    app_id: "..."          # or export ADZUNA_APP_ID
    app_key: "..."         # or export ADZUNA_APP_KEY
    title_groups: [primary]
    location_groups: [core]
    distance: 30           # kilometres
    results_per_page: 50   # the API's maximum
```

`config.yml` is gitignored, so credentials are safe there; the environment variables win when set. They are attached at request time only, so no capture, report or log line ever contains the key. Pay comes back as numbers rather than as advertiser prose — where `salary_is_predicted` is set, the salary is Adzuna's own estimate and says so.

## Outputs

Everything runtime lands under `outputs/`, which is gitignored:

```
outputs/<board>/raw/        page captures, one set per run
outputs/<board>/reports/    timestamped JSON per stage
outputs/state/locks/        which board is being scanned
outputs/state/runs.json     scans started from the dashboard
outputs/state/logs/         their output
```

Captures carry the run stamp, so scans accumulate rather than overwrite, and a capture can be matched to the report written beside it. When a parser starts returning nothing — usually a board changing its markup — those captures are what you diff.

## Testing

```bash
python -m pytest                          # ~150 tests, no network required
python -m py_compile reed_crawler/*.py dashboard/*.py
```

> [!NOTE]
> Tests assert against `config.example.yml`, not your personal `config.yml`. Adding a config key means adding it to both.

## Troubleshooting

**A scan reports `empty-body`.** The fetch succeeded but the page had nothing in it — usually transient, occasionally a block. The capture is still written; check it for a consent wall or a CAPTCHA. If every search in a run does this, the run exits non-zero.

**A scan exits 75.** The board is already being scanned, by the cron, a terminal or the dashboard. Nothing is wrong; wait for the other one.

**A board returns zero jobs with a healthy page.** The parser needs updating for changed markup. Diff the newest capture in `outputs/<board>/raw/` against an older one.

**Crawls hang.** Run `crawl4ai-doctor`, then set `crawl.headless: false` to watch the browser and see where it stalls.

The `probe_*.py` scripts are standalone single-URL crawls for testing a board in isolation. Modules marked `SUNSET` at the top are kept for reference and are not wired to anything.
