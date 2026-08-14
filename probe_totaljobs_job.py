import asyncio, json, re
from pathlib import Path
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

URL = "https://www.totaljobs.com/job/senior-full-stack-developer/anson-mccade-job107761254"
OUT = Path("outputs/totaljobs/probe")

async def main():
    browser_config = BrowserConfig(headless=True,browser_type="chromium",verbose=True,viewport_width=1920,viewport_height=1080)
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS,magic=True,simulate_user=True,override_navigator=True,remove_overlay_elements=True,remove_consent_popups=True,wait_for="css:body",page_timeout=60000,delay_before_return_html=8,scan_full_page=True,scroll_delay=0.5)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        r = await crawler.arun(url=URL, config=run_config)
    md = str(r.markdown or "")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"totaljobs_job_page.md").write_text(md, encoding="utf-8")
    (OUT/"totaljobs_job_page.html").write_text(r.html or "", encoding="utf-8")
    print(json.dumps({"success":r.success,"status_code":r.status_code,"error":r.error_message,"markdown_len":len(md),"head":md[:2500]}, indent=2))
asyncio.run(main())
