import asyncio
import os
from typing import Iterable, List, Optional

import aiohttp
from dotenv import load_dotenv

from core.browser import fetch_rendered_pages

load_dotenv()

DEFAULT_USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)


def build_headers(user_agent: Optional[str] = None) -> dict:
    return {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


async def fetch_page(
    url: str,
    session: aiohttp.ClientSession,
    *,
    semaphore: asyncio.Semaphore,
    timeout: int = 20,
    retries: int = 3,
    user_agent: Optional[str] = None,
) -> str:
    """Fetch a single URL with retries and a concurrency limiter."""
    headers = build_headers(user_agent)
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            async with semaphore:
                request_timeout = aiohttp.ClientTimeout(total=timeout)
                async with session.get(url, headers=headers, timeout=request_timeout) as response:
                    if response.status == 429 and attempt < retries:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    response.raise_for_status()
                    return await response.text()
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(2 ** attempt, 5))
                continue

    if last_error is not None:
        print(f"Error fetching {url}: {last_error}")
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
) -> List[str]:
    """Fetch several URLs concurrently while respecting a semaphore limit."""
    url_list = list(dict.fromkeys(url for url in urls if url and url.strip()))
    if not url_list:
        return []

    if use_rendered_browser:
        return await fetch_rendered_pages(
            url_list,
            wait_seconds=wait_seconds,
            timeout_ms=timeout * 1000,
            max_concurrency=max(1, min(max_concurrency, 3)),
        )

    semaphore = asyncio.Semaphore(max_concurrency)
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_page(
                url,
                session,
                semaphore=semaphore,
                timeout=timeout,
                retries=retries,
                user_agent=user_agent,
            )
            for url in url_list
        ]
        return await asyncio.gather(*tasks)

