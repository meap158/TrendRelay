from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    stealth_sync(page)
    page.goto("https://ads.tiktok.com/business/creativecenter/inspiration/popular/music/pc/en")
    page.wait_for_timeout(5000)
    print(page.title())
    print("\n--- HTML ---\n")
    print(page.content()[:2000])
    browser.close()