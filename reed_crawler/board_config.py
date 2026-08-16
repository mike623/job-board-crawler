from __future__ import annotations

import os
import random
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE_REED = "https://www.reed.co.uk"
BASE_TOTALJOBS = "https://www.totaljobs.com"
BASE_INDEED = "https://uk.indeed.com"
BASE_TALENT = "https://uk.talent.com"


def slug_text(s: str) -> str:
    return "-".join(str(s).lower().replace("/", " ").split())


def load_config(path: str | Path = ROOT / "config.yml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def run_stamp() -> str:
    """One timestamp per scan run, shared by that run's raw captures, reports and record.

    A caller that needs the identity up front — the dashboard, which redirects to the run's
    page before the scan has started — passes it down instead.
    """
    return os.environ.get("JOB_CRAWLER_RUN_STAMP") or datetime.now().strftime("%Y-%m-%d_%H%M%S")


def raw_capture_stem(name: str, stamp: str) -> str:
    """Filename stem for a raw capture.

    The stamp is what stops a scan overwriting the previous scan's capture of the same search,
    and it ties a capture back to the report written by the same run.
    """
    return f"{name}__{stamp}"


def values_from_groups(search_cfg: dict, kind: str, groups: list[str]) -> list[str]:
    values = []
    grouped = search_cfg.get(kind, {}) or {}
    for group in groups:
        for item in grouped.get(group, []) or []:
            if item not in values:
                values.append(item)
    return values


def board_titles(cfg: dict, board: str) -> list[str]:
    board_cfg = cfg["boards"][board]
    return values_from_groups(cfg.get("search", {}), "titles", board_cfg.get("title_groups", []))


def board_locations(cfg: dict, board: str) -> list[str]:
    board_cfg = cfg["boards"][board]
    return values_from_groups(cfg.get("search", {}), "locations", board_cfg.get("location_groups", []))


def reed_search_url(title: str, location: str, proximity: int) -> str:
    return f"{BASE_REED}/jobs/{slug_text(title)}-jobs-in-{slug_text(location)}?proximity={proximity}"


def totaljobs_search_url(title: str, location: str, radius: int) -> str:
    slug = slug_text(title).replace(".", "")
    return f"{BASE_TOTALJOBS}/jobs/{slug}/in-{slug_text(location)}?radius={radius}&q={quote_plus(title)}"


def indeed_search_url(title: str, location: str, radius: int) -> str:
    return f"{BASE_INDEED}/jobs?q={quote_plus(title)}&l={quote_plus(location)}&radius={radius}"


def talent_search_url(keyword: str, location: str, result_id: str = "") -> str:
    url = f"{BASE_TALENT}/jobs?k={quote_plus(keyword)}&l={quote_plus(location)}"
    if result_id:
        url += f"&id={quote_plus(result_id)}"
    return url


def build_board_urls(cfg: dict, board: str) -> list[dict]:
    board_cfg = cfg["boards"][board]
    if not board_cfg.get("enabled", False):
        return []
    if board_cfg.get("search_params"):
        rows = []
        for item in board_cfg.get("search_params") or []:
            keyword = item.get("k") or item.get("keyword") or item.get("title") or ""
            location = item.get("l") or item.get("location") or ""
            result_id = str(item.get("id") or "")
            if not keyword or not location:
                continue
            if board == "talent":
                url = talent_search_url(keyword, location, result_id)
            else:
                raise ValueError(f"Custom search_params are not supported for board: {board}")
            rows.append({"board": board, "title": keyword, "location": location, "url": url})
        max_pages = board_cfg.get("max_pages_per_run")
        return rows[: int(max_pages)] if max_pages else rows

    titles = board_titles(cfg, board)
    locations = board_locations(cfg, board)
    rows = []
    for title in titles:
        for location in locations:
            if board == "reed":
                url = reed_search_url(title, location, int(board_cfg.get("proximity", 50)))
            elif board == "totaljobs":
                url = totaljobs_search_url(title, location, int(board_cfg.get("radius", 30)))
            elif board == "indeed":
                url = indeed_search_url(title, location, int(board_cfg.get("radius", 50)))
            elif board == "talent":
                url = talent_search_url(title, location)
            else:
                raise ValueError(f"Unsupported board: {board}")
            rows.append({"board": board, "title": title, "location": location, "url": url})
    max_pages = board_cfg.get("max_pages_per_run")
    return rows[: int(max_pages)] if max_pages else rows


def jittered(seconds: float, spread: float = 0.35) -> float:
    """A delay near `seconds`, varied by up to `spread` either way.

    A perfectly regular cadence is itself a bot signal: humans are irregular. The average rate
    is unchanged, so this costs nothing against the throttling the delays exist to respect.
    """
    if seconds <= 0:
        return 0.0
    return seconds * random.uniform(1 - spread, 1 + spread)


if __name__ == "__main__":
    cfg = load_config()
    for board in cfg.get("boards", {}):
        rows = build_board_urls(cfg, board)
        print(f"{board}: {len(rows)} urls")
        for row in rows[:5]:
            print(f"  - {row['title']} / {row['location']}: {row['url']}")
