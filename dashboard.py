import logging
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

import ipaddress
import math
import os
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import altair as alt
import pandas as pd
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))  # so `core.*` imports work wherever Streamlit is launched

from core.currency import CURRENCY_INFO, convert, fetch_rates, format_money, price_format
from core.regions import REGIONS

DB_PATH = ROOT_DIR / "data" / "database.db"
CSV_PATH = ROOT_DIR / "data" / "products.csv"
MAIN_SCRIPT = ROOT_DIR / "main.py"
EXPECTED_COLUMNS = ["id", "title", "price", "currency", "rating", "image_url", "url", "created_at"]

# Overwritten from the sidebar on every rerun; read by money() and product_columns().
DISPLAY_CURRENCY = "USD"

# Material Symbols (https://fonts.google.com/icons). Centralised so they are easy to swap.
ICON = {
    "app": ":material/travel_explore:",
    "refresh": ":material/refresh:",
    "filters": ":material/filter_alt:",
    "reset": ":material/restart_alt:",
    "alerts": ":material/notifications_active:",
    "overview": ":material/bar_chart:",
    "trends": ":material/show_chart:",
    "deals": ":material/local_offer:",
    "catalog": ":material/storefront:",
    "converter": ":material/currency_exchange:",
    "swap": ":material/swap_horiz:",
    "image_missing": ":material/hide_image:",
    "rating": ":material/star:",
    "open": ":material/open_in_new:",
    "download": ":material/download:",
    "source": ":material/database:",
    "clock": ":material/schedule:",
    "scrape": ":material/travel_explore:",
    "run": ":material/play_arrow:",
    "site": ":material/language:",
}

# Scraper UI settings
SCRAPE_PRESETS = {
    "Custom URL": "",
    "Books to Scrape (practice site)": "https://books.toscrape.com",
    "Web Scraper test site: laptops (practice)": "https://webscraper.io/test-sites/e-commerce/static/computers/laptops",
}
DEFAULT_PRESET = "Books to Scrape (practice site)"
MAX_UI_PAGES = 300
SCRAPE_TIMEOUT_SECONDS = 15 * 60
LOG_TAIL_LINES = 300
RATES_RETRY_SECONDS = 60

st.set_page_config(page_title="Scrape Dashboard", page_icon=ICON["app"], layout="wide")

# --- Streamlit version compatibility (use_container_width -> width="stretch") ---
try:
    _ver = tuple(int(p) for p in st.__version__.split(".")[:2])
except ValueError:
    _ver = (0, 0)
STRETCH = {"width": "stretch"} if _ver >= (1, 50) else {"use_container_width": True}

# --- Light styling that works in both light and dark themes ---
st.markdown(
    """
    <style>
        div[data-testid="stMetric"] {
            background: rgba(128, 128, 128, 0.08);
            border: 1px solid rgba(128, 128, 128, 0.25);
            border-radius: 14px;
            padding: 12px 16px;
        }
        div[data-testid="stDataFrame"] {
            border-radius: 12px;
            overflow: hidden;
        }
        .stButton > button, .stDownloadButton > button {
            border-radius: 10px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def load_products():
    """Return (dataframe, source_label, error_message)."""
    error = None
    if DB_PATH.exists():
        try:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                df = pd.read_sql_query("SELECT * FROM products", conn)
            if not df.empty:
                return df, "SQLite database", None
        except Exception as exc:
            error = f"Could not read database: {exc}"

    if CSV_PATH.exists():
        try:
            return pd.read_csv(CSV_PATH), "CSV export", error
        except Exception as exc:
            error = f"Could not read CSV export: {exc}"

    return pd.DataFrame(columns=EXPECTED_COLUMNS), "none", error


@st.cache_data(ttl=60, show_spinner=False)
def load_history() -> pd.DataFrame:
    empty = pd.DataFrame(columns=["title", "price", "currency", "captured_at"])
    if not DB_PATH.exists():
        return empty
    base = "FROM product_price_history h JOIN products p ON p.id = h.product_id"
    queries = (
        f"SELECT p.title, h.price, COALESCE(h.currency, p.currency, 'USD') AS currency, h.captured_at {base}",
        f"SELECT p.title, h.price, 'USD' AS currency, h.captured_at {base}",  # pre-migration database
    )
    df = None
    with closing(sqlite3.connect(DB_PATH)) as conn:
        for query in queries:
            try:
                df = pd.read_sql_query(query, conn)
                break
            except Exception:
                continue
    if df is None:
        return empty
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["currency"] = df["currency"].fillna("USD").astype(str).str.strip().str.upper()
    df["captured_at"] = pd.to_datetime(df["captured_at"], errors="coerce")
    return df.dropna(subset=["price", "captured_at"])


def db_product_count() -> int:
    """Row count of the products table; 0 when the database or table does not exist yet."""
    if not DB_PATH.exists():
        return 0
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    except sqlite3.Error:
        return 0


# ----------------------------------------------------------------------------
# Exchange rates
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_rates():
    rates, updated = fetch_rates()
    if rates is None:
        raise RuntimeError("exchange rates unavailable")  # exceptions are never cached
    return rates, updated


def load_rates():
    """Return (rates, updated_label). Failures are not cached, but retried at most once a minute."""
    if time.time() - st.session_state.get("rates_failed_at", 0.0) < RATES_RETRY_SECONDS:
        return None, ""
    try:
        return _cached_rates()
    except Exception:
        st.session_state["rates_failed_at"] = time.time()
        return None, ""


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def extract_brand(title: str) -> str:
    """Heuristic: first word of the title, normalised (JBL stays JBL, sony -> Sony)."""
    tokens = re.findall(r"[A-Za-z0-9&'+]+", title or "")
    if len(tokens) < 2:
        return "Unknown"
    first = tokens[0]
    return first.upper() if len(first) <= 3 else first.capitalize()


def site_of(url: str) -> str:
    """Host without a leading www., used to tell scraped websites apart."""
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


def clean_products(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["currency"] = df["currency"].fillna("USD").astype(str).str.strip().str.upper().replace("", "USD")
    df["title"] = df["title"].fillna("Unknown Product").astype(str).str.strip()
    df["image_url"] = df["image_url"].fillna("").astype(str)
    df["url"] = df["url"].fillna("").astype(str)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["brand"] = df["title"].map(extract_brand)
    df["site"] = df["url"].map(site_of)
    return df


def money(value) -> str:
    return format_money(value, DISPLAY_CURRENCY)


def to_display(prices: pd.Series, currencies: pd.Series, target: str, rates) -> pd.Series:
    """Convert each price from its own currency to `target` (NaN if it cannot be converted)."""
    if rates and target in rates:
        return prices / currencies.map(rates) * rates[target]
    return prices.where(currencies == target)


def apply_currency(df: pd.DataFrame, target: str, rates) -> pd.DataFrame:
    df = df.copy()
    df["price_native"] = df["price"]
    df["price"] = to_display(df["price_native"], df["currency"], target, rates)
    df["original"] = [format_money(p, c) for p, c in zip(df["price_native"], df["currency"])]
    return df


def currency_label(code: str) -> str:
    return f"{code} · {CURRENCY_INFO[code][0]}" if code in CURRENCY_INFO else code


def reset_filters():
    for key in [k for k in st.session_state if k.startswith("f_")]:
        del st.session_state[key]


def refresh_data():
    st.session_state.pop("rates_failed_at", None)
    st.cache_data.clear()


SORTS = {
    "Newest first": (["created_at"], [False]),
    "Price: high to low": (["price", "rating"], [False, False]),
    "Price: low to high": (["price", "rating"], [True, False]),
    "Rating: high to low": (["rating", "price"], [False, True]),
}


def product_columns() -> dict:
    return {
        "image_url": st.column_config.ImageColumn("Preview"),
        "site": st.column_config.TextColumn("Site"),
        "brand": st.column_config.TextColumn("Brand"),
        "title": st.column_config.TextColumn("Title", width="large"),
        "price": st.column_config.NumberColumn(f"Price ({DISPLAY_CURRENCY})", format=price_format(DISPLAY_CURRENCY)),
        "original": st.column_config.TextColumn("Original price"),
        "rating": st.column_config.ProgressColumn("Rating", min_value=0, max_value=5, format="%.1f"),
        "url": st.column_config.LinkColumn("Link", display_text="Open"),
        "created_at": st.column_config.DatetimeColumn("Scraped", format="YYYY-MM-DD HH:mm"),
    }


# ----------------------------------------------------------------------------
# Scraper control (runs main.py in a subprocess and streams its log)
# ----------------------------------------------------------------------------
def validate_url_syntax(url: str) -> str | None:
    """Cheap check used while typing; returns an error message or None."""
    if not url:
        return "Enter a website URL."
    try:
        parts = urlsplit(url.replace("{query}", "x"))
        _ = parts.port  # raises ValueError for an invalid port
    except ValueError:
        return "That URL is not valid."
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "Use a full URL starting with http:// or https://"
    return None


def check_public_host(url: str) -> str | None:
    """DNS check at run time: refuse hosts that resolve to private/local addresses."""
    parts = urlsplit(url.replace("{query}", "x"))
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"Cannot resolve host: {parts.hostname}"
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global:
            return "Private, local and reserved addresses are not allowed."
    return None


def run_scraper(args: list[str], log_box) -> tuple[int, str]:
    """Run main.py, streaming output into log_box. Returns (exit_code, log_tail)."""
    command = [sys.executable, "-u", str(MAIN_SCRIPT), *args]  # list form: no shell, no injection
    process = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line.rstrip())
        lines.put(None)  # end-of-output marker

    threading.Thread(target=pump, daemon=True).start()

    tail: deque = deque(maxlen=LOG_TAIL_LINES)
    started = last_draw = time.monotonic()
    timed_out = False
    try:
        finished = False
        while not finished:
            try:
                item = lines.get(timeout=0.5)
            except queue.Empty:
                item = ""
            if item is None:
                finished = True
            elif item:
                tail.append(item)

            now = time.monotonic()
            if now - started > SCRAPE_TIMEOUT_SECONDS:
                timed_out = True
                tail.append(f"Stopped: exceeded {SCRAPE_TIMEOUT_SECONDS // 60} minutes.")
                break
            if finished or now - last_draw > 0.3:
                log_box.code("\n".join(tail) or "Starting...", language="text")
                last_draw = now
    finally:
        # Also runs when Streamlit interrupts the script (e.g. a widget was touched mid-run).
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    code = -1 if timed_out else process.wait()
    return code, "\n".join(tail)


def apply_scrape_preset() -> None:
    url = SCRAPE_PRESETS.get(st.session_state.get("scrape_preset", ""), "")
    if url:
        st.session_state["scrape_url"] = url


def start_scrape() -> None:
    st.session_state["scraping"] = True


def finish_scrape(level: str, summary: str, log: str = "") -> None:
    st.session_state["last_scrape"] = {"level": level, "summary": summary, "log": log}
    st.cache_data.clear()
    st.rerun()


def execute_scrape(url: str, query: str, options: dict) -> None:
    host = site_of(url.replace("{query}", "x"))
    problem = check_public_host(url)
    if problem:
        finish_scrape("error", problem)

    args = [
        "--base-url", url,
        "--max-products", str(options["max_products"]),
        "--max-pages", str(options["max_pages"]),
        "--crawl-depth", str(options["depth"]),
        "--max-concurrency", str(options["concurrency"]),
        "--output", "db",
    ]
    if options.get("region"):
        args += ["--region", options["region"]]
    if query:
        args += ["--query", query]
    if options["render_js"]:
        args += ["--render-js", "--wait-seconds", str(options["wait_seconds"])]

    st.info(f"Scraping {host}. This can take a few minutes; leave the page open.")
    log_box = st.empty()
    before = db_product_count()
    code, log = run_scraper(args, log_box)
    after = db_product_count()

    match = re.search(r"Saving (\d+) products", log)
    saved = int(match.group(1)) if match else 0
    currencies = re.search(r"Detected currencies: (.+)", log)
    currency_note = f" Currency: {currencies.group(1).strip()}." if currencies else ""

    if code != 0:
        finish_scrape("error", f"The scraper stopped with exit code {code}. See the log.", log)
    elif saved > 0:
        finish_scrape(
            "success",
            f"Saved {saved} product(s) from {host} ({after - before} new).{currency_note} "
            f"The database now holds {after}.",
            log,
        )
    else:
        finish_scrape(
            "warning",
            f"No products were found on {host}. The site may block crawlers, need JavaScript "
            "rendering, or have no product pages under this URL. See the log.",
            log,
        )


def render_scrape_panel(expanded: bool) -> None:
    running = st.session_state.get("scraping", False)

    last = st.session_state.get("last_scrape")
    if last:
        {"success": st.success, "warning": st.warning, "error": st.error}[last["level"]](last["summary"])
        if last["log"]:
            with st.expander("Last run log"):
                st.code(last["log"], language="text")

    st.session_state.setdefault("scrape_preset", DEFAULT_PRESET)
    st.session_state.setdefault("scrape_url", SCRAPE_PRESETS[DEFAULT_PRESET])

    with st.expander(f"{ICON['scrape']} Scrape a website", expanded=expanded or running):
        pick_col, url_col = st.columns([2, 5])
        pick_col.selectbox(
            "Quick pick",
            list(SCRAPE_PRESETS),
            key="scrape_preset",
            on_change=apply_scrape_preset,
            disabled=running,
        )
        url_col.text_input(
            "Website URL",
            key="scrape_url",
            placeholder="https://shop.example/category/cameras",
            help="A listing, category, search-results or product URL. Use {query} where a search term goes.",
            disabled=running,
        )
        url = st.session_state["scrape_url"].strip()
        url_error = validate_url_syntax(url)
        if url and url_error:
            st.caption(f":red[{url_error}]")

        query = ""
        needs_query = "{query}" in url
        if needs_query:
            query = st.text_input("Search term", key="scrape_query", disabled=running).strip()

        c1, c2, c3, c4 = st.columns(4)
        options = {
            "max_products": c1.number_input("Max products", 1, 500, 25, key="scrape_max_products", disabled=running),
            "max_pages": c2.number_input("Max pages", 1, MAX_UI_PAGES, 60, key="scrape_max_pages", disabled=running),
            "depth": c3.number_input("Crawl depth", 1, 4, 2, key="scrape_depth", disabled=running),
            "concurrency": c4.number_input("Concurrency", 1, 10, 5, key="scrape_concurrency", disabled=running),
        }
        region_col, js_col, wait_col = st.columns(3)
        options["region"] = region_col.selectbox(
            "Region (language & currency hint)",
            ["", *REGIONS],
            key="scrape_region",
            format_func=lambda c: "Auto (site default)" if not c else f"{REGIONS[c].name} · {REGIONS[c].currency}",
            help="Sends regional language headers. The currency the site actually returns is what gets stored.",
            disabled=running,
        )
        options["render_js"] = js_col.toggle(
            "Render JavaScript (needs Playwright)", key="scrape_render", disabled=running
        )
        options["wait_seconds"] = wait_col.number_input(
            "Wait after load (seconds)",
            0.0,
            15.0,
            2.0,
            0.5,
            key="scrape_wait",
            disabled=running or not options["render_js"],
        )

        allowed = st.checkbox(
            "I am allowed to scrape this site (its terms of service and robots.txt permit it)",
            key="scrape_allowed",
            disabled=running,
        )
        st.button(
            "Start scraping",
            icon=ICON["run"],
            type="primary",
            on_click=start_scrape,
            disabled=running or not allowed or bool(url_error) or (needs_query and not query),
        )
        st.caption("The crawler obeys robots.txt and stays on the site you enter.")

        if running:
            try:
                execute_scrape(url, query, options)
            finally:
                st.session_state["scraping"] = False


# ----------------------------------------------------------------------------
# Header + load
# ----------------------------------------------------------------------------
head_left, head_right = st.columns([5, 1], vertical_alignment="center")
head_left.title(f"{ICON['app']} Scrape Dashboard")
head_left.caption("Monitor product inventory gathered by the async scraper")
if head_right.button("Refresh data", icon=ICON["refresh"], **STRETCH):
    refresh_data()
    st.rerun()

raw_df, source, load_error = load_products()
if load_error:
    st.warning(load_error)

render_scrape_panel(expanded=raw_df.empty)

if raw_df.empty:
    st.info("No scraped data yet. Enter a website above and click Start scraping.")
    st.stop()

all_df = clean_products(raw_df)

# ----------------------------------------------------------------------------
# Sidebar: currency + filters
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header(f"{ICON['filters']} Filters")

    rates, rates_updated = load_rates()
    currency_options = sorted(rates) if rates else sorted(all_df["currency"].unique())
    scrape_region = REGIONS.get(st.session_state.get("scrape_region", ""))
    preferred = scrape_region.currency if scrape_region else all_df["currency"].mode().iat[0]
    if preferred not in currency_options:
        preferred = currency_options[0]
    DISPLAY_CURRENCY = st.selectbox(
        "Display currency",
        currency_options,
        index=currency_options.index(preferred),
        key="f_currency",
        format_func=currency_label,
    )
    all_df = apply_currency(all_df, DISPLAY_CURRENCY, rates)
    if rates:
        st.caption(f"{ICON['clock']} Rates: {rates_updated[:16] or 'live'}")
    else:
        st.warning("Live exchange rates unavailable. Only prices already in this currency are shown.")
    unconverted = int(all_df["price"].isna().sum() - all_df["price_native"].isna().sum())
    if unconverted > 0:
        st.caption(f":orange[{unconverted} product(s) use a currency without a known rate.]")

    search = st.text_input("Search title", key="f_search")
    selected_sites = st.multiselect("Sites", sorted(all_df["site"].unique()), key="f_sites")
    selected_brands = st.multiselect("Brands", sorted(all_df["brand"].unique()), key="f_brands")

    prices = all_df["price"].dropna()
    price_range = None
    if not prices.empty:
        lo, hi = float(math.floor(prices.min())), float(math.ceil(prices.max()))
        if lo < hi:
            price_range = st.slider(
                f"Price range ({DISPLAY_CURRENCY})",
                lo,
                hi,
                (lo, hi),
                step=1.0,
                key=f"f_price_{DISPLAY_CURRENCY}_{lo}_{hi}",
            )

    min_rating = st.slider("Minimum rating", 0.0, 5.0, 0.0, 0.1, key="f_rating")
    latest_only = st.toggle(
        "Latest snapshot per product",
        value=True,
        key="f_latest",
        help="Hide older duplicate rows when the same product was scraped several times.",
    )
    sort_label = st.selectbox("Sort by", list(SORTS), key="f_sort")

    with st.expander(f"{ICON['alerts']} Deal alert settings"):
        ceiling = convert(75, "USD", DISPLAY_CURRENCY, rates) if rates else 75.0
        ceiling = 75.0 if math.isnan(ceiling) else round(ceiling, -1 if ceiling >= 100 else 0)
        alert_price_limit = st.number_input(
            f"Price ceiling ({DISPLAY_CURRENCY})",
            min_value=0.0,
            value=float(ceiling),
            step=float(max(1.0, round(ceiling / 15))),
            key=f"f_alert_price_{DISPLAY_CURRENCY}",
        )
        min_alert_rating = st.slider("Minimum rating", 0.0, 5.0, 4.0, 0.1, key="f_alert_rating")

    st.button("Reset filters", icon=ICON["reset"], on_click=reset_filters, **STRETCH)

    st.markdown("---")
    st.caption(f"{ICON['source']} Source: {source}")
    last_scrape = all_df["created_at"].max()
    if pd.notna(last_scrape):
        st.caption(f"{ICON['clock']} Last scrape: {last_scrape:%Y-%m-%d %H:%M}")

# ----------------------------------------------------------------------------
# Apply filters
# ----------------------------------------------------------------------------
df = all_df
if latest_only:
    key = df["url"].where(df["url"] != "", df["title"])
    df = df.assign(_key=key).sort_values("created_at").drop_duplicates("_key", keep="last").drop(columns="_key")

if search:
    df = df[df["title"].str.contains(search, case=False, na=False, regex=False)]
if selected_sites:
    df = df[df["site"].isin(selected_sites)]
if selected_brands:
    df = df[df["brand"].isin(selected_brands)]
if price_range:
    df = df[df["price"].between(price_range[0], price_range[1])]
if min_rating > 0:
    df = df[df["rating"].fillna(0) >= min_rating]

sort_cols, sort_asc = SORTS[sort_label]
filtered = df.sort_values(sort_cols, ascending=sort_asc, kind="mergesort", na_position="last")

if filtered.empty:
    st.info("No records match the current filters.")
    st.stop()

# ----------------------------------------------------------------------------
# KPIs
# ----------------------------------------------------------------------------
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Products", f"{len(filtered):,}")
k2.metric("Average price", money(filtered["price"].mean()))
k3.metric("Median price", money(filtered["price"].median()))
k4.metric("Lowest price", money(filtered["price"].min()))
k5.metric("Average rating", f"{filtered['rating'].mean():.2f}" if filtered["rating"].notna().any() else "N/A")

tab_overview, tab_trends, tab_deals, tab_catalog, tab_converter = st.tabs(
    [
        f"{ICON['overview']} Overview",
        f"{ICON['trends']} Price trends",
        f"{ICON['deals']} Deals",
        f"{ICON['catalog']} Catalog",
        f"{ICON['converter']} Converter",
    ]
)

# ----------------------------------------------------------------------------
# Overview
# ----------------------------------------------------------------------------
with tab_overview:
    left, right = st.columns(2)

    with left:
        st.subheader("Price distribution")
        priced = filtered[["price"]].dropna()
        if priced.empty:
            st.info("No price data available.")
        else:
            chart = (
                alt.Chart(priced)
                .mark_bar()
                .encode(
                    x=alt.X("price:Q", bin=alt.Bin(maxbins=20), title=f"Price ({DISPLAY_CURRENCY})"),
                    y=alt.Y("count()", title="Products"),
                    tooltip=[alt.Tooltip("count()", title="Products")],
                )
            )
            st.altair_chart(chart, **STRETCH)

    with right:
        st.subheader("Top brands")
        brand_stats = (
            filtered.groupby("brand")
            .agg(products=("title", "count"), avg_price=("price", "mean"))
            .sort_values("products", ascending=False)
            .head(10)
            .reset_index()
        )
        chart = (
            alt.Chart(brand_stats)
            .mark_bar()
            .encode(
                x=alt.X("products:Q", title="Products"),
                y=alt.Y("brand:N", sort="-x", title=None),
                tooltip=[
                    "brand",
                    "products",
                    alt.Tooltip("avg_price:Q", title=f"Avg price ({DISPLAY_CURRENCY})", format=",.2f"),
                ],
            )
        )
        st.altair_chart(chart, **STRETCH)

    st.subheader("Price vs. rating")
    rated = filtered.dropna(subset=["price", "rating"])[["title", "brand", "price", "rating"]]
    if rated.empty:
        st.info("No products with both price and rating.")
    else:
        scatter = (
            alt.Chart(rated)
            .mark_circle(size=80, opacity=0.7)
            .encode(
                x=alt.X("price:Q", title=f"Price ({DISPLAY_CURRENCY})"),
                y=alt.Y("rating:Q", title="Rating", scale=alt.Scale(domain=[0, 5])),
                color=alt.Color("brand:N", legend=None),
                tooltip=["title", "brand", alt.Tooltip("price:Q", format=",.2f"), "rating"],
            )
            .interactive()
        )
        st.altair_chart(scatter, **STRETCH)

# ----------------------------------------------------------------------------
# Price trends
# ----------------------------------------------------------------------------
with tab_trends:
    history = load_history()
    history = history[history["title"].isin(filtered["title"])].copy()
    history["price"] = to_display(history["price"], history["currency"], DISPLAY_CURRENCY, rates)
    history = history.dropna(subset=["price"])

    if history.empty:
        st.info("No price history recorded yet. Run the scraper again to start tracking price changes over time.")
        collected = filtered.dropna(subset=["created_at"])
        if not collected.empty:
            st.subheader("Products collected per day")
            per_day = (
                collected.assign(day=collected["created_at"].dt.date)
                .groupby("day")
                .size()
                .rename("products")
                .reset_index()
            )
            per_day["day"] = pd.to_datetime(per_day["day"])
            chart = alt.Chart(per_day).mark_bar().encode(
                x=alt.X("day:T", title="Date"), y=alt.Y("products:Q", title="Products")
            )
            st.altair_chart(chart, **STRETCH)
    else:
        st.subheader("Price history")
        picks = st.multiselect(
            "Compare specific products (leave empty for the average of all filtered products)",
            sorted(history["title"].unique()),
            max_selections=8,
        )
        if picks:
            series = history[history["title"].isin(picks)][["title", "price", "captured_at"]]
        else:
            series = history.groupby("captured_at", as_index=False)["price"].mean()
            series["title"] = "Average (filtered products)"

        line = (
            alt.Chart(series)
            .mark_line(point=True)
            .encode(
                x=alt.X("captured_at:T", title="Captured"),
                y=alt.Y("price:Q", title=f"Price ({DISPLAY_CURRENCY})", scale=alt.Scale(zero=False)),
                color=alt.Color("title:N", title=None, legend=alt.Legend(orient="bottom")),
                tooltip=[
                    "title",
                    alt.Tooltip("price:Q", format=",.2f"),
                    alt.Tooltip("captured_at:T", title="Captured"),
                ],
            )
        )
        st.altair_chart(line, **STRETCH)

        st.subheader("Biggest price moves")
        ordered = history.sort_values("captured_at")
        moves = (
            ordered.groupby("title")
            .agg(
                first_price=("price", "first"),
                last_price=("price", "last"),
                checks=("price", "count"),
                last_seen=("captured_at", "max"),
            )
            .reset_index()
        )
        moves = moves[moves["checks"] >= 2].copy()
        if moves.empty:
            st.info("Each product needs at least two price checks to show a change.")
        else:
            moves["change_pct"] = (
                (moves["last_price"] - moves["first_price"]) / moves["first_price"] * 100
            ).where(moves["first_price"] > 0)
            moves = moves.sort_values("change_pct", key=lambda s: s.abs(), ascending=False)
            st.dataframe(
                moves.head(25),
                hide_index=True,
                column_config={
                    "title": st.column_config.TextColumn("Title", width="large"),
                    "first_price": st.column_config.NumberColumn("First price", format=price_format(DISPLAY_CURRENCY)),
                    "last_price": st.column_config.NumberColumn("Current price", format=price_format(DISPLAY_CURRENCY)),
                    "change_pct": st.column_config.NumberColumn("Change", format="%+.1f%%"),
                    "checks": st.column_config.NumberColumn("Checks"),
                    "last_seen": st.column_config.DatetimeColumn("Last checked", format="YYYY-MM-DD HH:mm"),
                },
                **STRETCH,
            )

# ----------------------------------------------------------------------------
# Deals
# ----------------------------------------------------------------------------
with tab_deals:
    deals = filtered[(filtered["price"] <= alert_price_limit) & (filtered["rating"] >= min_alert_rating)]
    deals = deals.sort_values(["price", "rating"], ascending=[True, False])
    st.subheader("Deal alerts")
    st.caption(
        f"{len(deals)} product(s) at or under {money(alert_price_limit)} "
        f"with a rating of {min_alert_rating:.1f}+ (adjust in the sidebar)."
    )
    if deals.empty:
        st.info("No deal alerts match your current thresholds.")
    else:
        st.dataframe(
            deals[["image_url", "site", "brand", "title", "price", "original", "rating", "url"]].head(25),
            hide_index=True,
            column_config=product_columns(),
            **STRETCH,
        )

# ----------------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------------
with tab_catalog:
    view = st.radio("View", ["Table", "Cards"], horizontal=True, label_visibility="collapsed")

    if view == "Cards":
        c1, c2, _ = st.columns([1, 1, 4])
        per_page = c1.selectbox("Cards per page", [8, 12, 24, 48], index=1)
        pages = max(1, math.ceil(len(filtered) / per_page))
        page = c2.number_input("Page", min_value=1, max_value=pages, value=1) if pages > 1 else 1
        chunk = filtered.iloc[(page - 1) * per_page : page * per_page]

        for start in range(0, len(chunk), 4):
            cols = st.columns(4)
            for col, (_, row) in zip(cols, chunk.iloc[start : start + 4].iterrows()):
                with col, st.container(border=True):
                    if row["image_url"]:
                        try:
                            st.image(row["image_url"], **STRETCH)
                        except Exception:
                            st.caption(f"{ICON['image_missing']} Image unavailable")
                    else:
                        st.caption(f"{ICON['image_missing']} No image")
                    st.markdown(f"**{row['title'][:60]}**")
                    rating_text = f"{row['rating']:.1f}" if pd.notna(row["rating"]) else "N/A"
                    st.write(f"{row['brand']} · **{money(row['price'])}** · {ICON['rating']} {rating_text}")
                    if row["currency"] != DISPLAY_CURRENCY:
                        st.caption(f"Original: {row['original']}")
                    st.caption(f"{ICON['site']} {row['site']}")
                    if pd.notna(row["created_at"]):
                        st.caption(row["created_at"].strftime("%Y-%m-%d %H:%M"))
                    if row["url"]:
                        st.link_button("Open product", row["url"], icon=ICON["open"])
        if pages > 1:
            st.caption(f"Page {page} of {pages}")
    else:
        st.dataframe(
            filtered[["image_url", "site", "brand", "title", "price", "original", "rating", "url", "created_at"]],
            hide_index=True,
            column_config=product_columns(),
            **STRETCH,
        )

    export_df = filtered[
        ["site", "brand", "title", "price", "price_native", "currency", "rating", "url", "created_at"]
    ].rename(columns={"price": f"price_{DISPLAY_CURRENCY}", "price_native": "price_original"})
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button(
        "Download CSV",
        export_df.to_csv(index=False).encode("utf-8"),
        file_name="scraped_products.csv",
        mime="text/csv",
        icon=ICON["download"],
        **STRETCH,
    )
    try:
        buffer = BytesIO()
        export_df.to_excel(buffer, index=False)
        d2.download_button(
            "Download Excel",
            buffer.getvalue(),
            file_name="scraped_products.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=ICON["download"],
            **STRETCH,
        )
    except Exception:
        d2.caption("Excel export needs openpyxl (`pip install openpyxl`).")

# ----------------------------------------------------------------------------
# Currency converter
# ----------------------------------------------------------------------------
def _swap_currencies() -> None:
    st.session_state["cv_from"], st.session_state["cv_to"] = (
        st.session_state["cv_to"],
        st.session_state["cv_from"],
    )


with tab_converter:
    st.subheader("Currency converter")
    if not rates:
        st.info("Live exchange rates are unavailable right now. Click Refresh data in a moment.")
    else:
        codes = sorted(rates)
        st.session_state.setdefault("cv_from", "EUR" if "EUR" in rates else codes[0])
        st.session_state.setdefault("cv_to", DISPLAY_CURRENCY if DISPLAY_CURRENCY in rates else codes[0])

        a, b, swap, c = st.columns([2, 2, 1, 2], vertical_alignment="bottom")
        amount = a.number_input("Amount", min_value=0.0, value=100.0, step=10.0, key="cv_amount")
        src = b.selectbox("From", codes, key="cv_from", format_func=currency_label)
        swap.button("Swap", icon=ICON["swap"], on_click=_swap_currencies, help="Swap currencies", **STRETCH)
        dst = c.selectbox("To", codes, key="cv_to", format_func=currency_label)

        result = convert(amount, src, dst, rates)
        st.metric(f"{format_money(amount, src)} =", format_money(result, dst))
        st.caption(f"1 {src} = {convert(1, src, dst, rates):,.4f} {dst}  ·  rates: {rates_updated[:16] or 'live'}")

        popular = [
            x
            for x in ["USD", "EUR", "GBP", "XOF", "NGN", "GHS", "MAD", "ZAR", "CAD", "JPY"]
            if x in rates and x != src
        ]
        st.dataframe(
            pd.DataFrame(
                {
                    "Currency": popular,
                    f"{format_money(amount, src)} in…": [
                        format_money(convert(amount, src, x, rates), x) for x in popular
                    ],
                }
            ),
            hide_index=True,
            **STRETCH,
        )

st.markdown("---")
st.caption("Tip: use the sidebar filters to narrow the data, then export exactly what you see. Prices are converted with live rates and are indicative only.")