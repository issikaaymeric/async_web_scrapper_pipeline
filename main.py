import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote_plus

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.crawler import CrawlResult, crawl_site
from data.models.database import AsyncSessionLocal, Product, ProductPriceHistory, engine, init_db
from utils.processor import clean_scraped_data, export_scraped_data


# books.toscrape.com is a sandbox built for scraping practice. Large retailers (Amazon etc.)
# block automated clients and restrict them in their terms, so they are a poor default.
DEFAULT_BASE_URL = "https://books.toscrape.com"
REFRESHABLE_COLUMNS = ("title", "price", "rating", "image_url")
INSERT_CHUNK_SIZE = 100  # keeps each INSERT under SQLite's bound-variable limit


def utc_now() -> datetime:
    """Naive UTC timestamp (what SQLite/pandas expect), without the deprecated utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def record_price_history(product_rows: list[dict]) -> int:
    if not product_rows:
        return 0

    inserted = 0
    async with AsyncSessionLocal() as session:
        for row in product_rows:
            url = row.get("url")
            price = row.get("price")
            if not url or price is None:
                continue

            product = await session.scalar(select(Product).where(Product.url == url))
            if product is None:
                continue

            latest_snapshot = await session.scalar(
                select(ProductPriceHistory)
                .where(ProductPriceHistory.product_id == product.id)
                .order_by(ProductPriceHistory.captured_at.desc())
            )
            if latest_snapshot is not None and abs(float(latest_snapshot.price) - float(price)) <= 0.01:
                continue

            session.add(
                ProductPriceHistory(
                    product_id=product.id,
                    price=float(price),
                    captured_at=utc_now(),
                )
            )
            inserted += 1

        await session.commit()

    return inserted


async def save_products(rows: list[dict]) -> None:
    """Insert new products; refresh title/price/rating/image of ones already stored."""
    refresh = [column for column in REFRESHABLE_COLUMNS if column in rows[0]]
    async with AsyncSessionLocal() as session:
        for start in range(0, len(rows), INSERT_CHUNK_SIZE):
            stmt = sqlite_upsert(Product).values(rows[start : start + INSERT_CHUNK_SIZE])
            if refresh:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["url"],
                    set_={column: stmt.excluded[column] for column in refresh},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
            await session.execute(stmt)
        await session.commit()


def explain_empty_crawl(result: CrawlResult) -> str:
    if result.pages_visited == 0:
        return "Nothing was fetched: robots.txt refused the crawl or could not be read (see warnings above)."
    if result.pages_failed == result.pages_visited:
        return "Every request failed: network/SSL problem or the site is blocking bots (see warnings above)."
    return (
        "Pages loaded but none looked like a product page. If the site renders prices with "
        "JavaScript, retry with --render-js; otherwise it needs site-specific selectors."
    )


def build_search_url(query: str, base_url: str = DEFAULT_BASE_URL) -> str:
    if "{query}" in base_url:
        return base_url.format(query=quote_plus(query))
    return base_url


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return parsed


async def run_pipeline(
    base_url: str,
    max_products: int = 0,
    max_concurrency: int = 10,
    output_format: str = "db",
    output_dir: str = "data",
    user_agent: str | None = None,
    use_rendered_browser: bool = False,
    wait_seconds: float = 2.0,
    crawl_depth: int = 2,
    max_pages: int = 100,
):
    await init_db()

    print(f"Step 1: Crawling {base_url}...")
    print("__" * 30)
    product_limit = max_products if max_products > 0 else max_pages
    crawl_result = await crawl_site(
        base_url,
        max_products=product_limit,
        max_pages=max_pages,
        max_depth=crawl_depth,
        max_concurrency=max_concurrency,
        user_agent=user_agent,
        use_rendered_browser=use_rendered_browser,
        wait_seconds=wait_seconds,
    )
    print(
        f"Visited {crawl_result.pages_visited} pages "
        f"({crawl_result.pages_failed} failed, {crawl_result.pages_without_product} not products) "
        f"and found {len(crawl_result.products)} product candidates."
    )
    clean_results = clean_scraped_data(crawl_result.products)
    if not clean_results:
        print("No products with both a title and a positive price were found.")
        print(explain_empty_crawl(crawl_result))
        return

    if output_format in {"csv", "json"}:
        exported_path = export_scraped_data(clean_results, output_format=output_format, output_dir=output_dir)
        print(f"Exported {len(clean_results)} products to {exported_path}")

    print(f"Saving {len(clean_results)} products to database...")
    await save_products(clean_results)

    tracked = await record_price_history(clean_results)
    print(f"Recorded {tracked} price change snapshots in the history table.")

    print("--- Pipeline Complete! Check your database.db or export file ---")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Async product scraper and data pipeline")
    parser.add_argument("--query", default="headphones", help="Search term; only used when --base-url contains {query}.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Listing/category URL to start from. Supports {query}; use a site's product listing URL.",
    )
    parser.add_argument("--max-products", type=nonnegative_int, default=10, help="Maximum products to save; 0 uses the page limit.")
    parser.add_argument("--max-concurrency", type=positive_int, default=10, help="Maximum concurrent fetches.")
    parser.add_argument("--crawl-depth", type=nonnegative_int, default=2, help="Maximum link depth; products need at least 1.")
    parser.add_argument("--max-pages", type=positive_int, default=100, help="Maximum same-site pages to visit per run.")
    parser.add_argument("--output", choices=["db", "csv", "json"], default="db", help="Also export to csv/json (the database is always written).")
    parser.add_argument("--output-dir", default="data", help="Directory used for CSV/JSON export.")
    parser.add_argument("--user-agent", default=None, help="Custom browser string for requests.")
    parser.add_argument("--render-js", action="store_true", help="Use Playwright headless browser for JavaScript-heavy pages.")
    parser.add_argument("--wait-seconds", type=float, default=2.0, help="Seconds to wait after page load for JS-rendered content.")
    return parser.parse_args()


async def main(args: argparse.Namespace) -> None:
    try:
        await run_pipeline(
            base_url=build_search_url(args.query, args.base_url),
            max_products=args.max_products,
            max_concurrency=args.max_concurrency,
            output_format=args.output,
            output_dir=args.output_dir,
            user_agent=args.user_agent,
            use_rendered_browser=args.render_js,
            wait_seconds=args.wait_seconds,
            crawl_depth=args.crawl_depth,
            max_pages=args.max_pages,
        )
    finally:
        await engine.dispose()  # close aiosqlite worker threads before the loop shuts down


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main(parse_arguments()))