import asyncio
from typing import Iterable, List, Optional

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - optional dependency
    async_playwright = None


async def render_page_html(
    url: str,
    *,
    wait_seconds: float = 2.0,
    timeout_ms: int = 30000,
) -> str:
    """Render a page in a headless browser for JavaScript-heavy storefronts."""
    if async_playwright is None:
        raise RuntimeError("playwright is not installed. Install it with pip install playwright and python -m playwright install chromium.")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_seconds > 0:
                await page.wait_for_timeout(int(wait_seconds * 1000))
            return await page.content()
        finally:
            await browser.close()


async def fetch_rendered_pages(
    urls: Iterable[str],
    *,
    wait_seconds: float = 2.0,
    timeout_ms: int = 30000,
    max_concurrency: int = 3,
) -> List[str]:
    """Fetch HTML for JS-heavy pages while limiting concurrency."""
    url_list = list(dict.fromkeys(url.strip() for url in urls if url and url.strip()))
    if not url_list:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(url: str) -> str:
        async with semaphore:
            try:
                return await render_page_html(url, wait_seconds=wait_seconds, timeout_ms=timeout_ms)
            except Exception as exc:  # pragma: no cover - runtime environment only
                print(f"Error rendering {url}: {exc}")
                return ""

    return await asyncio.gather(*(fetch_one(url) for url in url_list))
