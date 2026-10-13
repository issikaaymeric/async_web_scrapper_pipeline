import argparse
import asyncio
import os
import sys
from datetime import datetime
from urllib.parse import quote_plus

from concurrent.futures import ProcessPoolExecutor
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.fetcher import fetch_all
from core.parser import extract_product_links, parse_product_html
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


async def run_pipeline(
    base_url: str,
    max_products: int = 0,
    max_concurrency: int = 10,
    output_format: str = "db",
    output_dir: str = "data",
    user_agent: str | None = None,
    use_rendered_browser: bool = False,
    wait_seconds: float = 2.0,
):
    await init_db()

    print(f"Step 1: Finding links on {base_url}...")
    print("__" * 30)
    homepage_html = await fetch_all(
        [base_url],
        max_concurrency=max_concurrency,
        user_agent=user_agent,
        use_rendered_browser=use_rendered_browser,
        wait_seconds=wait_seconds,
    )
    if not homepage_html or not homepage_html[0]:
        print("Stopping: homepage request failed or returned no content.")
        return

    product_links = extract_product_links(homepage_html[0], base_url)
    if max_products and max_products > 0:
        product_links = product_links[:max_products]

    print(f"Step 2: Found {len(product_links)} products to scrape.")
    if not product_links:
        print("Stopping: No links were found. Check your selectors or headers.")
        return

    print("__" * 30)
    print(f"Step 3: Fetching {len(product_links)} product pages...")
    html_contents = await fetch_all(
        product_links,
        max_concurrency=max_concurrency,
        user_agent=user_agent,
        use_rendered_browser=use_rendered_browser,
        wait_seconds=wait_seconds,
    )
    print("__" * 30)

    print("Step 4: Extracting titles and prices...")
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=min(len(html_contents) or 1, 4)) as executor:
        parsed_tasks = [
            loop.run_in_executor(executor, parse_product_html, html, url)
            for html, url in zip(html_contents, product_links)
        ]
        raw_results = await asyncio.gather(*parsed_tasks)

    clean_results = clean_scraped_data(raw_results)
    if not clean_results:
        print("No new data to save.")
        return

    if output_format in {"csv", "json"}:
        exported_path = export_scraped_data(clean_results, output_format=output_format, output_dir=output_dir)
        print(f"Step 5: Exported {len(clean_results)} products to {exported_path}")

    print(f"Step 6: Saving {len(clean_results)} products to database...")
    async with AsyncSessionLocal() as session:
        stmt = sqlite_upsert(Product).values(clean_results)
        stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
        await session.execute(stmt)
        await session.commit()

    tracked = await record_price_history(clean_results)
    print(f"Step 7: Recorded {tracked} price change snapshots in the history table.")

    print("--- Pipeline Complete! Check your database.db or export file ---")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Async product scraper and data pipeline")
    parser.add_argument("--query", default="headphones", help="Search query used to build the listing URL.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base Amazon or storefront URL. Supports {query} placeholder.")
    parser.add_argument("--max-products", type=int, default=10, help="Limit how many product URLs to scrape.")
    parser.add_argument("--max-concurrency", type=int, default=10, help="Maximum concurrent fetches.")
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
        )
    )
