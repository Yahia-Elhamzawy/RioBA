import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "browser_data")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--start-maximized'],
            ignore_default_args=["--enable-automation"],
            no_viewport=True
        )
        print("Success")
        await browser.close()

asyncio.run(main())
