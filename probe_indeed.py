import asyncio, json
from pathlib import Path
from urllib.parse import urljoin
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

URL = "https://uk.indeed.com/jobs?q=senior+software+engineer&l=Leeds&radius=50"
OUT = Path("outputs/indeed/probe")
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
        delay_before_return_html=10,
        scan_full_page=True,
        scroll_delay=1.5,
        screenshot=False,
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=URL, config=run_config)

    md = str(result.markdown or "")
    html = result.html or ""
    cleaned = result.cleaned_html or ""
    links = []
    for group, arr in (result.links or {}).items():
        for link in arr or []:
            href = link.get("href") or ""
            text = (link.get("text") or "").strip()
            links.append({"group": group, "text": text[:200], "url": urljoin(URL, href)})

    likely = [l for l in links if "/viewjob" in l["url"] or "jk=" in l["url"]]
    (OUT / "indeed.md").write_text(md, encoding="utf-8")
    (OUT / "indeed.html").write_text(html, encoding="utf-8")
    (OUT / "indeed.cleaned.html").write_text(cleaned, encoding="utf-8")
    (OUT / "indeed.links.json").write_text(json.dumps(links, indent=2), encoding="utf-8")
    (OUT / "indeed.likely_jobs.json").write_text(json.dumps(likely, indent=2), encoding="utf-8")
    title = ""
    if "Just a moment" in md or "Additional Verification Required" in md or "captcha" in md.lower():
        title = "possible_bot_block"
    print(json.dumps({
        "success": result.success,
        "status_code": result.status_code,
        "error_message": result.error_message,
        "markdown_len": len(md),
        "html_len": len(html),
        "cleaned_html_len": len(cleaned),
        "links_count": len(links),
        "likely_jobs_count": len(likely),
        "signal": title,
        "outdir": str(OUT.resolve()),
        "markdown_head": md[:1600],
        "first_likely_jobs": likely[:10],
    }, indent=2))

asyncio.run(main())
