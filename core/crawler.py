import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import aiohttp

from core.fetcher import DEFAULT_USER_AGENT, fetch_all
from core.parser import extract_internal_links, parse_product_html


@dataclass
class CrawlResult:
    products: list[dict]
    pages_visited: int


async def _load_robots_policy(
    start_url: str,
    user_agent: str,
) -> tuple[RobotFileParser | None, float]:
    parts = urlsplit(start_url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    parser = RobotFileParser()
    parser.set_url(robots_url)

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                robots_url,
                headers={"User-Agent": user_agent},
                timeout=timeout,
            ) as response:
                if response.status == 404:
                    parser.parse([])
                    return parser, 0.0
                if response.status >= 400:
                    print(f"Cannot read robots.txt ({response.status}); refusing to crawl {start_url}.")
                    return None, 0.0
                parser.parse((await response.text()).splitlines())
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
        print(f"Cannot read robots.txt for {start_url}; refusing to crawl: {exc}")
        return None, 0.0

    delay = parser.crawl_delay(user_agent) or parser.crawl_delay("*") or 0.0
    return parser, max(float(delay), 0.0)


async def crawl_site(
    start_url: str,
    *,
    max_products: int,
    max_pages: int,
    max_depth: int,
    max_concurrency: int,
    user_agent: str | None = None,
    use_rendered_browser: bool = False,
    wait_seconds: float = 2.0,
) -> CrawlResult:
    """Crawl one host breadth-first within explicit page/depth limits and robots rules."""
    start_parts = urlsplit(start_url)
    if start_parts.scheme not in {"http", "https"} or not start_parts.hostname:
        raise ValueError("The starting URL must be an absolute HTTP or HTTPS URL.")
    if max_products < 1 or max_pages < 1 or max_depth < 0 or max_concurrency < 1:
        raise ValueError("Product/page/concurrency limits must be positive and depth cannot be negative.")

    agent = user_agent or DEFAULT_USER_AGENT
    policy, crawl_delay = await _load_robots_policy(start_url, agent)
    if policy is None:
        return CrawlResult(products=[], pages_visited=0)

    if not policy.can_fetch(agent, start_url):
        print(f"robots.txt disallows the starting URL: {start_url}")
        return CrawlResult(products=[], pages_visited=0)

    visited: set[str] = set()
    frontier: list[tuple[str, int]] = [(start_url, 0)]
    products_by_url: dict[str, dict] = {}
    pages_visited = 0

    while frontier and pages_visited < max_pages and len(products_by_url) < max_products:
        remaining = max_pages - pages_visited
        batch = []
        for url, depth in frontier:
            if url in visited or not policy.can_fetch(agent, url):
                continue
            visited.add(url)
            batch.append((url, depth))
            if len(batch) >= remaining:
                break
        frontier = []
        if not batch:
            break

        if crawl_delay > 0:
            html_pages = []
            for url, _ in batch:
                page_html = await fetch_all(
                    [url],
                    max_concurrency=1,
                    user_agent=user_agent,
                    use_rendered_browser=use_rendered_browser,
                    wait_seconds=wait_seconds,
                )
                html_pages.append(page_html[0] if page_html else "")
                await asyncio.sleep(crawl_delay)
        else:
            html_pages = await fetch_all(
                [url for url, _ in batch],
                max_concurrency=max_concurrency,
                user_agent=user_agent,
                use_rendered_browser=use_rendered_browser,
                wait_seconds=wait_seconds,
            )

        pages_visited += len(batch)
        next_links: set[str] = set()
        for (page_url, depth), page_html in zip(batch, html_pages):
            if not page_html:
                continue
            product = parse_product_html(page_html, page_url)
            if (
                product
                and product["title"] != "N/A"
                and product["price"] > 0
            ):
                products_by_url[page_url] = product

            if depth < max_depth:
                next_links.update(extract_internal_links(page_html, page_url))

        if len(products_by_url) >= max_products:
            break
        frontier.extend(
            (link, depth + 1)
            for link in sorted(next_links)
            if link not in visited
        )

    return CrawlResult(
        products=list(products_by_url.values())[:max_products],
        pages_visited=pages_visited,
    )
