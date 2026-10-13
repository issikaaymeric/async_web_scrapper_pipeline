# async_web_scrapper_pipeline

# 🕷️ Async Web Scraper & Data Pipeline

This project includes a configurable product scraper that can be pointed at many storefronts, with async fetching, browser rendering, structured-data extraction, export support, and a dashboard.

---

## What was improved

- Added resilient async fetching with retries and concurrency limits
- Added CLI options for query, max products, concurrency, and export format
- Improved HTML parsing to read Schema.org Product JSON-LD, Open Graph, microdata, and common storefront markup
- Added same-site product-link discovery from product URL patterns and product-card markup
- Added support for scraping a direct product page when the page has no product links
- Added bounded, same-site recursive crawling with depth, page, and concurrency controls
- Added robots.txt checks, including crawl-delay handling
- Added data cleaning utilities that strip invalid rows and deduplicate URLs
- Added CSV/JSON export support alongside database persistence
- Added historical price tracking so the project records price changes over time
- Added a price history dashboard section to monitor product-level trends
- Added unit tests for parsing and cleaning logic

---

## Architecture

```text
main.py
├── fetch_all() -> async HTTP fetch with retry + concurrency control
├── extract_product_links() -> discover storefront/product URLs
├── parse_product_html() -> metadata-aware parsing with fallback selectors
├── clean_scraped_data() -> pandas validation + deduplication
├── export_scraped_data() -> optional CSV/JSON export
└── upsert to SQLite -> avoids duplicates using url uniqueness
```

---

## Running the pipeline

Install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run a search with defaults:

```bash
python main.py --query headphones --max-products 10
```

To try another storefront, provide its product category, search-results, or product URL. The crawler follows internal links from that starting point:

```bash
python main.py --base-url "https://shop.example/category/cameras" --max-products 20 --max-pages 100 --crawl-depth 2 --max-concurrency 5 --output csv
```

For sites whose search URL accepts a query, use the `{query}` placeholder:

```bash
python main.py --query cameras --base-url "https://shop.example/search?q={query}" --max-products 20
```

For JavaScript-heavy storefronts, use a headless browser:

```bash
pip install playwright
python -m playwright install chromium
python main.py --query headphones --max-products 10 --render-js --wait-seconds 3
```

Open the Streamlit dashboard:

```bash
streamlit run dashboard.py
```

Advanced usage:

```bash
python main.py --query headphones --max-products 20 --max-concurrency 15 --output csv --output-dir data --render-js --wait-seconds 3
```

Available options:

- `--query`: search term used to build the listing URL
- `--base-url`: custom product listing URL; supports `{query}` placeholder
- `--max-products`: maximum products to save; `0` uses the page limit
- `--max-pages`: cap the number of same-site pages visited per run
- `--crawl-depth`: number of internal-link levels to follow from the start URL
- `--max-concurrency`: maximum concurrent requests
- `--output`: `db`, `csv`, or `json`
- `--output-dir`: folder used for exported data
- `--user-agent`: custom browser user-agent string

The crawler stays on the starting hostname, obeys its `robots.txt`, and stops at the configured page, depth, and product limits. The generic extractor works best on storefronts that publish Schema.org Product JSON-LD, Open Graph/product metadata, microdata, or recognizable product-card markup. No generic scraper can reliably extract every site: some sites use custom markup, require login, block automated access, or load data through site-specific APIs. Use site-specific selectors/adapters for those cases. It does not bypass authentication, paywalls, CAPTCHAs, or access controls.

---

## Database setup

The default SQLite connection is configured in `data/models/database.py` and uses the `products` table with a unique `url` constraint. Re-running the pipeline safely ignores duplicates.

The scraper now also records a `product_price_history` table with one row per price snapshot, which lets the dashboard chart historical price changes and show price deltas for each product over time.

---

## Example export

CSV export is saved to `data/products.csv` or JSON to `data/products.json` when `--output` is set to `csv` or `json`.

---

## Testing

```bash
python -m unittest discover -s tests -v
```

---

## Notes

This project is designed as a solid foundation for e-commerce scraping and can be extended with JavaScript-rendered pages, scheduling, persistence to Postgres, or a dashboard layer.
