import asyncio
import heapq
import itertools
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import aiohttp

from core.fetcher import DEFAULT_USER_AGENT, build_headers, fetch_all, make_ssl_context
from core.parser import extract_links, parse_product_html

logger = logging.getLogger("scraper.crawler")

# robots.txt statuses that mean "do not crawl" (stdlib treats 401/403 the same way).
# Other 4xx (404, 410) mean "no robots file" -> allow all, per RFC 9309.
ROBOTS_BLOCKING_STATUS = {401, 403, 429}

PRIORITY_PRODUCT = 0
PRIORITY_PAGINATION = 1
PRIORITY_OTHER = 2


@dataclass
class CrawlResult:
    products: list[dict]
    pages_visited: int
    pages_failed: int = 0
    pages_without_product: int = 0


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
        connector = aiohttp.TCPConnector(ssl=make_ssl_context())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                robots_url,
                headers=build_headers(user_agent),
                timeout=timeout,
            ) as response:
                status = response.status
                body = await response.text(errors="replace") if status < 400 else ""
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
        logger.warning("Cannot read robots.txt for %s; refusing to crawl: %s", start_url, exc)
        return None, 0.0

    if status >= 500 or status in ROBOTS_BLOCKING_STATUS:
        logger.warning(
            "robots.txt returned HTTP %s; refusing to crawl %s "
            "(the site is blocking or unavailable; consider an official API).",
            status,
            start_url,
        )
        return None, 0.0

    parser.parse(body.splitlines())  # empty body (404/410) == allow all
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
    """Crawl one host within explicit page/depth limits and robots rules.

    Pages are fetched best-first: product links, then pagination ("next page") links,
    then everything else. Pagination stays at the same depth, so max_depth=1 means
    "listing pages and the products they link to", however many listing pages there are.
    Products need depth >= 1 from a listing URL, so max_depth=0 can never find any.
    """
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
        logger.warning("robots.txt disallows the starting URL: %s", start_url)
        return CrawlResult(products=[], pages_visited=0)

    counter = itertools.count()  # FIFO tie-breaker within a priority level
    queue: list[tuple[int, int, str, int]] = [(PRIORITY_OTHER, next(counter), start_url, 0)]
    queued: set[str] = {start_url}
    product_urls: set[str] = set()  # URLs a parent page presented as product links
    products_by_url: dict[str, dict] = {}
    pages_visited = 0
    pages_failed = 0
    pages_without_product = 0
    batch_cap = max_concurrency * 2

    while queue and pages_visited < max_pages and len(products_by_url) < max_products:
        batch_size = min(batch_cap, max_pages - pages_visited)
        batch: list[tuple[str, int]] = []
        while queue and len(batch) < batch_size:
            _, _, url, depth = heapq.heappop(queue)
            if policy.can_fetch(agent, url):
                batch.append((url, depth))
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

        for (page_url, depth), page_html in zip(batch, html_pages):
            if not page_html:
                pages_failed += 1
                continue

            # Pages linked as products are trusted; anything else (start page, listings,
            # info pages) must declare itself a product, or it is not recorded.
            product = parse_product_html(
                page_html,
                page_url,
                require_product_signal=page_url not in product_urls,
            )
            if product and product["title"] != "N/A" and product["price"] > 0:
                products_by_url[page_url] = product
            else:
                pages_without_product += 1

            if depth >= max_depth:
                continue

            found_products, found_pagination, found_others = extract_links(page_html, page_url)
            discovered = (
                (found_products, PRIORITY_PRODUCT, depth + 1),
                (found_pagination, PRIORITY_PAGINATION, depth),
                (found_others, PRIORITY_OTHER, depth + 1),
            )
            for links, priority, child_depth in discovered:
                for link in links:
                    if priority == PRIORITY_PRODUCT:
                        product_urls.add(link)
                    if link in queued:
                        continue
                    queued.add(link)
                    heapq.heappush(queue, (priority, next(counter), link, child_depth))

    if pages_visited and pages_failed == pages_visited:
        logger.warning(
            "Every request failed (%d pages). Check the fetcher warnings above: "
            "SSL, HTTP 403/429 (bot blocking), or network.",
            pages_visited,
        )
    logger.info(
        "Crawl finished: %d pages (%d failed, %d not products), %d products",
        pages_visited,
        pages_failed,
        pages_without_product,
        len(products_by_url),
    )

    return CrawlResult(
        products=list(products_by_url.values())[:max_products],
        pages_visited=pages_visited,
        pages_failed=pages_failed,
        pages_without_product=pages_without_product,
    )