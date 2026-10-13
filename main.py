import argparse
import asyncio
import os
import sys
from datetime import datetime
from urllib.parse import quote_plus

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.crawler import crawl_site
from data.models.database import AsyncSessionLocal, Product, ProductPriceHistory, init_db
from utils.processor import clean_scraped_data, export_scraped_data


DEFAULT_BASE_URL = "https://www.amazon.com/s?k={query}"


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
                    captured_at=datetime.utcnow(),
                )
            )
            inserted += 1

        await session.commit()

    return inserted


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
        f"Visited {crawl_result.pages_visited} pages and found "
        f"{len(crawl_result.products)} product candidates."
    )
    clean_results = clean_scraped_data(crawl_result.products)
    if not clean_results:
        print("No products with both a title and a positive price were found.")
        return

    if output_format in {"csv", "json"}:
        exported_path = export_scraped_data(clean_results, output_format=output_format, output_dir=output_dir)
        print(f"Exported {len(clean_results)} products to {exported_path}")

    print(f"Saving {len(clean_results)} products to database...")
    async with AsyncSessionLocal() as session:
        stmt = sqlite_upsert(Product).values(clean_results)
        stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
        await session.execute(stmt)
        await session.commit()

    tracked = await record_price_history(clean_results)
    print(f"Recorded {tracked} price change snapshots in the history table.")

    print("--- Pipeline Complete! Check your database.db or export file ---")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Async product scraper and data pipeline")
    parser.add_argument("--query", default="headphones", help="Search query used to build the listing URL.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Listing/category URL to scrape. Supports {query}; use a site's product listing URL.",
    )
    parser.add_argument("--max-products", type=nonnegative_int, default=10, help="Maximum products to save; 0 uses the page limit.")
    parser.add_argument("--max-concurrency", type=positive_int, default=10, help="Maximum concurrent fetches.")
    parser.add_argument("--crawl-depth", type=nonnegative_int, default=2, help="Maximum number of internal-link levels to follow.")
    parser.add_argument("--max-pages", type=positive_int, default=100, help="Maximum same-site pages to visit per run.")
    parser.add_argument("--output", choices=["db", "csv", "json"], default="db", help="Where to write extracted data.")
    parser.add_argument("--output-dir", default="data", help="Directory used for CSV/JSON export.")
    parser.add_argument("--user-agent", default=None, help="Custom browser string for requests.")
    parser.add_argument("--render-js", action="store_true", help="Use Playwright headless browser for JavaScript-heavy pages.")
    parser.add_argument("--wait-seconds", type=float, default=2.0, help="Seconds to wait after page load for JS-rendered content.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    url = build_search_url(args.query, args.base_url)
    asyncio.run(
        run_pipeline(
            base_url=url,
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
    )
