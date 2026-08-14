import asyncio, json
from pathlib import Path
from urllib.parse import urljoin
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

URL = "https://www.totaljobs.com/jobs/full-stack-developer-nodejs/in-london?radius=30&searchOrigin=membersarea&rsearch=1&q=Full+Stack+Developer+Node.Js"
OUT = Path("outputs/totaljobs/probe")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    browser_config = BrowserConfig(
        headless=True,
        browser_type="chromium",
        verbose=True,
        viewport_width=1920,
        viewport_height=1080,
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        wait_for="css:body",
        page_timeout=60000,
        delay_before_return_html=8,
        scan_full_page=True,
        scroll_delay=0.5,
        screenshot=False,
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=URL, config=run_config)

    md = str(result.markdown or "")
    html = result.html or ""
    cleaned = result.cleaned_html or ""
    links = result.links or {}
    all_links = []
    for kind in ("internal", "external"):
        for item in links.get(kind, []) or []:
            href = item.get("href") if isinstance(item, dict) else str(item)
            text = item.get("text", "") if isinstance(item, dict) else ""
            abs_url = urljoin(URL, href)
            all_links.append({"kind": kind, "href": href, "url": abs_url, "text": text})

    likely_jobs = [l for l in all_links if "/job/" in l["url"] or "totaljobs.com/job/" in l["url"]]

    (OUT / "totaljobs.md").write_text(md, encoding="utf-8")
    (OUT / "totaljobs.html").write_text(html, encoding="utf-8")
    (OUT / "totaljobs.cleaned.html").write_text(cleaned, encoding="utf-8")
    (OUT / "totaljobs.links.json").write_text(json.dumps(all_links, indent=2), encoding="utf-8")
    (OUT / "totaljobs.likely_jobs.json").write_text(json.dumps(likely_jobs, indent=2), encoding="utf-8")

    print(json.dumps({
        "success": result.success,
        "status_code": result.status_code,
        "error_message": result.error_message,
        "markdown_len": len(md),
        "html_len": len(html),
        "cleaned_html_len": len(cleaned),
        "links_count": len(all_links),
        "likely_jobs_count": len(likely_jobs),
        "outdir": str(OUT.resolve()),
        "markdown_head": md[:1200],
        "first_likely_jobs": likely_jobs[:10],
    }, indent=2))

asyncio.run(main())
