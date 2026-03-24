# async_web_scrapper_pipeline

# 🕷️ Async Web Scraper & Data Pipeline

A production-grade, asynchronous web scraping pipeline built with Python. Concurrently fetches product pages, parses structured data using multiprocessing, cleans it with Pandas, and persists results to a database via SQLAlchemy async  with upsert logic to avoid duplicates.

---

## 🏗️ Architecture

```
main.py (Orchestrator)
│
├── 1. fetch_all()        → aiohttp async I/O — fetches pages concurrently
├── 2. extract_links()    → BeautifulSoup — finds product URLs on listing page
├── 3. fetch_all()        → concurrent fetch of all product pages
├── 4. parse_product()    → ProcessPoolExecutor — CPU-bound HTML parsing in parallel
├── 5. clean_scraped_data() → Pandas — cleans, deduplicates, normalises
└── 6. SQLAlchemy upsert  → persists to DB, skips existing URLs
```

---

## ⚙️ Tech Stack

| Layer | Technology |
|---|---|
| Async I/O | `aiohttp`, `asyncio` |
| HTML Parsing | `BeautifulSoup4` |
| CPU-bound parsing | `ProcessPoolExecutor` |
| Data cleaning | `Pandas` |
| ORM / Database | `SQLAlchemy` async + `aiosqlite` |
| Config | `python-dotenv` |

---

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/issikaaymeric/async_web_scrapper_pipeline.git
cd async_web_scrapper_pipeline
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file at the root of the project:

```env
DATABASE_URL=sqlite+aiosqlite:///./data/database.db
USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```

### 5. Run the pipeline

```bash
python main.py
```

---

## 📁 Project Structure

```
async_web_scrapper_pipeline/
│
├── core/
│   ├── fetcher.py          # Async HTTP fetching with aiohttp
│   └── parser.py           # HTML parsing with BeautifulSoup
│
├── data/
│   ├── models/
│   │   └── database.py     # SQLAlchemy async models & engine
│   └── database.db         # SQLite database (auto-created)
│
├── utils/
│   └── processor.py        # Pandas data cleaning pipeline
│
├── main.py                 # Pipeline orchestrator
├── .env                    # Environment variables (not committed)
├── .env.example            # Example env file
├── requirements.txt
└── README.md
```

---

## 🗄️ Database Schema

```sql
CREATE TABLE products (
    id         INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL,
    price      REAL    NOT NULL,
    rating     REAL,
    image_url  TEXT,
    url        TEXT    UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

- `url` is the unique key — re-running the pipeline will skip already-saved products (upsert with `ON CONFLICT DO NOTHING`)
- `created_at` is auto-set by the database on insert

---

## 🔑 Key Design Decisions

**Async I/O + Multiprocessing**
Network fetching is I/O-bound, so `aiohttp` + `asyncio` handles concurrency efficiently. HTML parsing is CPU-bound, so a `ProcessPoolExecutor` runs it in parallel across cores — combining the best of both concurrency models.

**Upsert over Insert**
Using SQLite's `INSERT OR IGNORE` (via SQLAlchemy's `on_conflict_do_nothing`) means the pipeline is safely re-runnable without creating duplicate records.

**Pandas cleaning layer**
A dedicated cleaning step between parsing and saving ensures only valid, normalised data reaches the database — separating concerns cleanly.

---

## 📋 Requirements

```
aiohttp
beautifulsoup4
pandas
sqlalchemy
aiosqlite
python-dotenv
```

Generate with:
```bash
pip freeze > requirements.txt
```

---

## 🧪 Running Tests

```bash
pytest tests/ -v
```

> Tests cover: fetcher error handling, parser edge cases, Pandas cleaning logic, and DB upsert behaviour.

---

## 📈 Performance

- Concurrent page fetching with configurable semaphore (`asyncio.Semaphore`)
- CPU-bound parsing offloaded to `ProcessPoolExecutor`
- Single DB session commit for all records per run
- Duplicate-safe via upsert — safe to schedule as a cron job

---

## 🔮 Roadmap

- [ ] Add Playwright support for JavaScript-rendered pages
- [ ] Export cleaned data to CSV / JSON
- [ ] Add price history tracking (store each run's prices)
- [ ] Dockerize the pipeline
- [ ] Add a Streamlit dashboard for visualising scraped data

---

## 👤 Author

**Issika Aymeric**
Computer Science Student
[LinkedIn](https://linkedin.com/in/issikaaymeric) · [GitHub](https://github.com/issikaaymeric)

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
