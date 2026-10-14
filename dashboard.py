import logging
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

import math
import re
import sqlite3
from io import BytesIO
from pathlib import Path

import altair as alt # type: ignore
import pandas as pd
import streamlit as st # type: ignore

ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "data" / "database.db"
CSV_PATH = ROOT_DIR / "data" / "products.csv"
EXPECTED_COLUMNS = ["id", "title", "price", "rating", "image_url", "url", "created_at"]

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
    "image_missing": ":material/hide_image:",
    "rating": ":material/star:",
    "open": ":material/open_in_new:",
    "download": ":material/download:",
    "source": ":material/database:",
    "clock": ":material/schedule:",
}

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
            with sqlite3.connect(DB_PATH) as conn:
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
    empty = pd.DataFrame(columns=["title", "price", "captured_at"])
    if not DB_PATH.exists():
        return empty
    try:
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql_query(
                """
                SELECT p.title, h.price, h.captured_at
                FROM product_price_history h
                JOIN products p ON p.id = h.product_id
                """,
                conn,
            )
    except Exception:
        return empty
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["captured_at"] = pd.to_datetime(df["captured_at"], errors="coerce")
    return df.dropna(subset=["price", "captured_at"])


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


def clean_products(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["title"] = df["title"].fillna("Unknown Product").astype(str).str.strip()
    df["image_url"] = df["image_url"].fillna("").astype(str)
    df["url"] = df["url"].fillna("").astype(str)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["brand"] = df["title"].map(extract_brand)
    return df


def money(value) -> str:
    return "N/A" if pd.isna(value) else f"${float(value):,.2f}"


def reset_filters():
    for key in [k for k in st.session_state if k.startswith("f_")]:
        del st.session_state[key]


SORTS = {
    "Newest first": (["created_at"], [False]),
    "Price: high to low": (["price", "rating"], [False, False]),
    "Price: low to high": (["price", "rating"], [True, False]),
    "Rating: high to low": (["rating", "price"], [False, True]),
}

PRODUCT_COLUMNS = {
    "image_url": st.column_config.ImageColumn("Preview"),
    "brand": st.column_config.TextColumn("Brand"),
    "title": st.column_config.TextColumn("Title", width="large"),
    "price": st.column_config.NumberColumn("Price", format="$%.2f"),
    "rating": st.column_config.ProgressColumn("Rating", min_value=0, max_value=5, format="%.1f"),
    "url": st.column_config.LinkColumn("Link", display_text="Open"),
    "created_at": st.column_config.DatetimeColumn("Scraped", format="YYYY-MM-DD HH:mm"),
}

# ----------------------------------------------------------------------------
# Header + load
# ----------------------------------------------------------------------------
head_left, head_right = st.columns([5, 1], vertical_alignment="center")
head_left.title(f"{ICON['app']} Scrape Dashboard")
head_left.caption("Monitor product inventory gathered by the async scraper")
if head_right.button("Refresh data", icon=ICON["refresh"], **STRETCH):
    st.cache_data.clear()
    st.rerun()

raw_df, source, load_error = load_products()
if load_error:
    st.warning(load_error)

if raw_df.empty:
    st.info("No scraped data found yet. Run the scraper first, then refresh this page.")
    st.code("python main.py --query headphones --max-products 10 --output db")
    st.stop()

all_df = clean_products(raw_df)

# ----------------------------------------------------------------------------
# Sidebar filters
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header(f"{ICON['filters']} Filters")
    search = st.text_input("Search title", key="f_search")
    selected_brands = st.multiselect("Brands", sorted(all_df["brand"].unique()), key="f_brands")

    prices = all_df["price"].dropna()
    price_range = None
    if not prices.empty:
        lo, hi = float(math.floor(prices.min())), float(math.ceil(prices.max()))
        if lo < hi:
            price_range = st.slider(
                "Price range ($)", lo, hi, (lo, hi), step=1.0, key=f"f_price_{lo}_{hi}"
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
        alert_price_limit = st.number_input("Price ceiling ($)", min_value=0.0, value=75.0, step=5.0, key="f_alert_price")
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

tab_overview, tab_trends, tab_deals, tab_catalog = st.tabs(
    [
        f"{ICON['overview']} Overview",
        f"{ICON['trends']} Price trends",
        f"{ICON['deals']} Deals",
        f"{ICON['catalog']} Catalog",
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
                    x=alt.X("price:Q", bin=alt.Bin(maxbins=20), title="Price ($)"),
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
                tooltip=["brand", "products", alt.Tooltip("avg_price:Q", title="Avg price", format="$.2f")],
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
                x=alt.X("price:Q", title="Price ($)"),
                y=alt.Y("rating:Q", title="Rating", scale=alt.Scale(domain=[0, 5])),
                color=alt.Color("brand:N", legend=None),
                tooltip=["title", "brand", alt.Tooltip("price:Q", format="$.2f"), "rating"],
            )
            .interactive()
        )
        st.altair_chart(scatter, **STRETCH)

# ----------------------------------------------------------------------------
# Price trends
# ----------------------------------------------------------------------------
with tab_trends:
    history = load_history()
    history = history[history["title"].isin(filtered["title"])]

    if history.empty:
        st.info("No price history recorded yet. Run the scraper again to start tracking price changes over time.")
        collected = filtered.dropna(subset=["created_at"])
        if not collected.empty:
            st.subheader("Products collected per day")
            per_day = collected.assign(day=collected["created_at"].dt.date).groupby("day").size().rename("products").reset_index()
            per_day["day"] = pd.to_datetime(per_day["day"])
            chart = alt.Chart(per_day).mark_bar().encode(x=alt.X("day:T", title="Date"), y=alt.Y("products:Q", title="Products"))
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
                y=alt.Y("price:Q", title="Price ($)", scale=alt.Scale(zero=False)),
                color=alt.Color("title:N", title=None, legend=alt.Legend(orient="bottom")),
                tooltip=["title", alt.Tooltip("price:Q", format="$.2f"), alt.Tooltip("captured_at:T", title="Captured")],
            )
        )
        st.altair_chart(line, **STRETCH)

        st.subheader("Biggest price moves")
        ordered = history.sort_values("captured_at")
        moves = (
            ordered.groupby("title")
            .agg(first_price=("price", "first"), last_price=("price", "last"), checks=("price", "count"), last_seen=("captured_at", "max"))
            .reset_index()
        )
        moves = moves[moves["checks"] >= 2].copy()
        if moves.empty:
            st.info("Each product needs at least two price checks to show a change.")
        else:
            moves["change_pct"] = ((moves["last_price"] - moves["first_price"]) / moves["first_price"] * 100).where(moves["first_price"] > 0)
            moves = moves.sort_values("change_pct", key=lambda s: s.abs(), ascending=False)
            st.dataframe(
                moves.head(25),
                hide_index=True,
                column_config={
                    "title": st.column_config.TextColumn("Title", width="large"),
                    "first_price": st.column_config.NumberColumn("First price", format="$%.2f"),
                    "last_price": st.column_config.NumberColumn("Current price", format="$%.2f"),
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
    st.caption(f"{len(deals)} product(s) at or under {money(alert_price_limit)} with a rating of {min_alert_rating:.1f}+ (adjust in the sidebar).")
    if deals.empty:
        st.info("No deal alerts match your current thresholds.")
    else:
        st.dataframe(
            deals[["image_url", "brand", "title", "price", "rating", "url"]].head(25),
            hide_index=True,
            column_config=PRODUCT_COLUMNS,
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
                    if pd.notna(row["created_at"]):
                        st.caption(row["created_at"].strftime("%Y-%m-%d %H:%M"))
                    if row["url"]:
                        st.link_button("Open product", row["url"], icon=ICON["open"])
        if pages > 1:
            st.caption(f"Page {page} of {pages}")
    else:
        st.dataframe(
            filtered[["image_url", "brand", "title", "price", "rating", "url", "created_at"]],
            hide_index=True,
            column_config=PRODUCT_COLUMNS,
            **STRETCH,
        )

    export_df = filtered[["brand", "title", "price", "rating", "url", "created_at"]]
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

st.markdown("---")
st.caption("Tip: use the sidebar filters to narrow the data, then export exactly what you see.")