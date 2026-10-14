import asyncio
import logging
from importlib import import_module
from typing import Iterable, List, Optional

try:
    _playwright_async_api = import_module("playwright.async_api")
    PlaywrightTimeoutError = _playwright_async_api.TimeoutError
    async_playwright = _playwright_async_api.async_playwright
except ImportError:  # optional dependency
    async_playwright = None
    PlaywrightTimeoutError = asyncio.TimeoutError

logger = logging.getLogger("scraper.browser")

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
NETWORK_IDLE_CAP_MS = 10_000


async def _block_heavy_resources(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def fetch_rendered_pages(
    urls: Iterable[str],
    *,
    wait_seconds: float = 2.0,
    timeout_ms: int = 30000,
    max_concurrency: int = 3,
    user_agent: Optional[str] = None,
    locale: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> List[str]:
    """Render pages in ONE shared headless Chromium, limiting concurrent tabs."""
    if async_playwright is None:
        raise RuntimeError(
            "playwright is not installed. Install it with "
            "`pip install playwright` and `python -m playwright install chromium`."
        )

    url_list = list(dict.fromkeys(url.strip() for url in urls if url and url.strip()))
    if not url_list:
        return []

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    # Playwright has no `accept_language` argument: the language is set through
    # `locale` (navigator.language + Accept-Language) and, optionally, extra headers.
    context_options: dict = {"user_agent": user_agent}
    if locale:
        context_options["locale"] = locale
    if accept_language:
        context_options["extra_http_headers"] = {"Accept-Language": accept_language}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(**context_options)
            await context.route("**/*", _block_heavy_resources)

            async def fetch_one(url: str) -> str:
                async with semaphore:
                    page = await context.new_page()
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        try:
                            await page.wait_for_load_state(
                                "networkidle", timeout=min(timeout_ms, NETWORK_IDLE_CAP_MS)
                            )
                        except PlaywrightTimeoutError:
                            pass  # long-polling pages never go idle; use what has rendered
                        if wait_seconds > 0:
                            await page.wait_for_timeout(int(wait_seconds * 1000))
                        return await page.content()
                    except Exception as exc:
                        logger.warning("Error rendering %s: %s", url, exc)
                        return ""
                    finally:
                        await page.close()

            return list(await asyncio.gather(*(fetch_one(url) for url in url_list)))
        finally:
            await browser.close()


async def render_page_html(
    url: str,
    *,
    wait_seconds: float = 2.0,
    timeout_ms: int = 30000,
    user_agent: Optional[str] = None,
    locale: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> str:
    """Render a single page; kept for backward compatibility."""
    pages = await fetch_rendered_pages(
        [url],
        wait_seconds=wait_seconds,
        timeout_ms=timeout_ms,
        max_concurrency=1,
        user_agent=user_agent,
        locale=locale,
        accept_language=accept_language,
    )
    return pages[0] if pages else ""