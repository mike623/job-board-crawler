import asyncio
from pathlib import Path
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

URL='https://www.reed.co.uk/jobs/senior-software-engineer-cards/56616299?source=searchResults'

async def main():
    browser_config=BrowserConfig(headless=True,browser_type='chromium',verbose=True)
    run_config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS,magic=True,simulate_user=True,override_navigator=True,remove_overlay_elements=True,remove_consent_popups=True,wait_for='css:body',page_timeout=45000,delay_before_return_html=6,scan_full_page=True,scroll_delay=0.5)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        r=await crawler.arun(url=URL,config=run_config)
    out=Path(__file__).resolve().parents[1]/'outputs/reed/job_pages/test_full_jd.md'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(str(r.markdown or ''),encoding='utf-8')
    print('success',r.success,'status',r.status_code,'err',r.error_message)
    md=str(r.markdown or '')
    print('len',len(md),'path',out)
    print(md[:3000])
asyncio.run(main())
