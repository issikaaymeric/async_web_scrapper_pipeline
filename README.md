# async_web_scrapper_pipeline

# 🕷️ Async Web Scraper & Data Pipeline

This project now includes a more robust and configurable scraping pipeline with better error handling, flexible CLI arguments, export support, and stronger parsing and validation logic.

---

## What was improved

- Added resilient async fetching with retries and concurrency limits
- Added CLI options for query, max products, concurrency, and export format
- Improved HTML parsing to support metadata-based extraction and generic fallbacks
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
- `--max-products`: cap the number of product URLs to scrape
- `--max-concurrency`: concurrency for fetching requests
- `--output`: `db`, `csv`, or `json`
- `--output-dir`: folder used for exported data
- `--user-agent`: custom browser user-agent string

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
