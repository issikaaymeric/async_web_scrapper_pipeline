import asyncio
import logging
import os
import ssl
from typing import Iterable, List, Optional

import aiohttp
from dotenv import load_dotenv

from core.browser import fetch_rendered_pages

try:
    import certifi # type: ignore
except ImportError:  # optional; falls back to the system trust store
    certifi = None

load_dotenv()

logger = logging.getLogger("scraper.fetcher")

DEFAULT_USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def make_ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle when available (fixes missing-CA errors on Windows/macOS)."""
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def build_headers(user_agent: Optional[str] = None, accept_language: Optional[str] = None) -> dict:
    # Accept-Encoding is intentionally NOT set: aiohttp advertises only the codecs it can
    # decode. Forcing "br" without the Brotli package makes responses undecodable.
    return {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept-Language": accept_language or "en-US,en;q=0.9",
        "Referer": "https://www.google.com",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

def _retry_delay(response: aiohttp.ClientResponse, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return float(min(int(retry_after), 60))
    return float(min(2**attempt, 30))


async def fetch_page(
    url: str,
    session: aiohttp.ClientSession,
    *,
    semaphore: asyncio.Semaphore,
    timeout: int = 20,
    retries: int = 3,
    user_agent: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> str:
    """Fetch one URL. Retries only transient failures; returns "" on permanent failure."""
    headers = build_headers(user_agent, accept_language)
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    last_error = "unknown error"

    for attempt in range(1, retries + 1):
        delay = float(min(2**attempt, 5))
        try:
            async with semaphore:
                async with session.get(url, headers=headers, timeout=request_timeout) as response:
                    if response.status in RETRYABLE_STATUS:
                        last_error = f"HTTP {response.status}"
                        delay = _retry_delay(response, attempt)
                    elif response.status >= 400:
                        logger.warning("HTTP %s for %s (not retrying)", response.status, url)
                        return ""
                    else:
                        return await response.text(errors="replace")
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries:
            await asyncio.sleep(delay)  # sleep outside the semaphore

    logger.warning("Giving up on %s: %s", url, last_error)
    return ""


async def fetch_all(
    urls: Iterable[str],
    *,
    max_concurrency: int = 10,
    timeout: int = 20,
    retries: int = 3,
    user_agent: Optional[str] = None,
    use_rendered_browser: bool = False,
    wait_seconds: float = 2.0,
    accept_language: Optional[str] = None,
    locale: Optional[str] = None,
) -> List[str]:
    """Fetch URLs concurrently. The result is index-aligned with `urls` ("" for blank/failed)."""
    cleaned = [url.strip() if url else "" for url in urls]
    unique = list(dict.fromkeys(url for url in cleaned if url))
    if not unique:
        return [""] * len(cleaned)

    if use_rendered_browser:
        pages = await fetch_rendered_pages(
            unique,
            wait_seconds=wait_seconds,
            timeout_ms=timeout * 1000,
            max_concurrency=max(1, min(max_concurrency, 3)),
            user_agent=user_agent or DEFAULT_USER_AGENT,
            locale=locale,
            accept_language=accept_language,
        )
    else:
        semaphore = asyncio.Semaphore(max_concurrency)
        connector = aiohttp.TCPConnector(ssl=make_ssl_context(), limit=max_concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            pages = await asyncio.gather(
                *(
                    fetch_page(
                        url,
                        session,
                        semaphore=semaphore,
                        timeout=timeout,
                        retries=retries,
                        user_agent=user_agent,
                        accept_language=accept_language,
                    )
                    for url in unique
                )
            )

    by_url = dict(zip(unique, pages))
    return [by_url.get(url, "") for url in cleaned]