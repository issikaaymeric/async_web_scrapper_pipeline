import sqlite3
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "data" / "database.db"
CSV_PATH = ROOT_DIR / "data" / "products.csv"


st.set_page_config(page_title="Scrape Dashboard", page_icon="📊", layout="wide")

st.markdown(
    """
    <style>
        .stApp {
            background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
            color: #0f172a;
        }
        .stApp * {
            color: #0f172a;
        }
        .stMetric {
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(148, 163, 184, 0.35);
            border-radius: 14px;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
        }
        div[data-testid="stDataFrame"] {
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid rgba(148, 163, 184, 0.35);
        }
        .stDownloadButton > button, .stButton > button {
            border-radius: 12px;
            background: linear-gradient(90deg, #2563eb, #3b82f6);
            color: white;
            border: none;
            box-shadow: 0 6px 16px rgba(37, 99, 235, 0.2);
        }
        [data-testid="stSidebar"] {
            background: rgba(248, 250, 252, 0.96);
            border-right: 1px solid rgba(148, 163, 184, 0.35);
        }
        [data-testid="stSidebar"] * {
            color: #0f172a !important;
        }
        [data-testid="stNotification"] {
            background: #fff7ed;
            border: 1px solid #fdba74;
            color: #9a5a00 !important;
        }
        [data-testid="stNotification"] * {
            color: #9a5a00 !important;
        }
        .stAlert {
            background: #fff7ed;
            border: 1px solid #fdba74;
        }
        .stAlert * {
            color: #9a5a00 !important;
        }
        a { color: #1d4ed8; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def load_products() -> pd.DataFrame:
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                df = pd.read_sql_query("SELECT * FROM products ORDER BY created_at DESC", conn)
                if not df.empty:
                    return df
        except Exception as exc:  # pragma: no cover - runtime path
            st.warning(f"Could not read database: {exc}")

    if CSV_PATH.exists():
        try:
            return pd.read_csv(CSV_PATH)
        except Exception as exc:  # pragma: no cover - runtime path
            st.warning(f"Could not read CSV export: {exc}")

    return pd.DataFrame(columns=["id", "title", "price", "rating", "image_url", "url", "created_at"])


def format_currency(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def build_price_distribution(df: pd.DataFrame) -> pd.DataFrame:
    bins = [0, 25, 50, 75, 100, 150, 200, 300, 500, float("inf")]
    labels = ["< $25", "$25-$49", "$50-$74", "$75-$99", "$100-$149", "$150-$199", "$200-$299", "$300-$499", "$500+"]
    return pd.cut(df["price"].dropna(), bins=bins, labels=labels, right=False).value_counts().sort_index()


def extract_brand(title: str) -> str:
    if not title:
        return "Unknown"
    tokens = title.split()
    if not tokens:
        return "Unknown"
    if len(tokens) > 1:
        return tokens[0]
    return title


st.title("🕷️ Scrape Dashboard")
st.caption("Monitor product inventory gathered by the async scraper")

if st.button("Refresh data"):
    st.cache_data.clear()
    st.rerun()

products_df = load_products()

if products_df.empty:
    st.info("No scraped data found yet. Run the scraper first, then refresh this page.")
    st.code("python main.py --query headphones --max-products 10 --output db")
    st.stop()

products_df = products_df.copy()
products_df["price"] = pd.to_numeric(products_df.get("price", pd.Series(dtype=float)), errors="coerce")
products_df["rating"] = pd.to_numeric(products_df.get("rating", pd.Series(dtype=float)), errors="coerce")
products_df["title"] = products_df.get("title", "").fillna("Unknown Product")
products_df["created_at"] = pd.to_datetime(products_df.get("created_at", pd.NaT), errors="coerce")
products_df["image_url"] = products_df.get("image_url", "").fillna("")
products_df["brand"] = products_df["title"].map(extract_brand)

with st.sidebar:
    st.subheader("Filters")
    selected_title = st.text_input("Search title")
    selected_brand = st.selectbox("Brand", ["All"] + sorted(products_df["brand"].dropna().unique().tolist()))
    min_price = st.number_input("Minimum price", min_value=0.0, value=0.0, step=5.0)
    max_price = st.number_input(
        "Maximum price",
        min_value=0.0,
        value=float(products_df["price"].max()) if not products_df["price"].empty else 0.0,
        step=5.0,
    )
    if max_price < min_price:
        max_price = min_price

    selected_category = st.selectbox("Category", ["All", "Under $50", "$50-$150", "$150-$300", "$300+"])
    alert_price_limit = st.number_input("Deal alert price ceiling", min_value=0.0, value=75.0, step=5.0)
    min_alert_rating = st.slider("Minimum alert rating", min_value=0.0, max_value=5.0, value=4.0, step=0.1)
    sort_key = st.selectbox(
        "Sort by",
        ["price_high_to_low", "price_low_to_high", "rating_high_to_low", "newest_first"],
        index=0,
    )

    st.markdown("---")
    st.caption("Data source")
    source = "SQLite database" if DB_PATH.exists() else "CSV export"
    st.write(source)

filtered_df = products_df.copy()
if selected_title:
    filtered_df = filtered_df[filtered_df["title"].str.contains(selected_title, case=False, na=False)]
if selected_brand != "All":
    filtered_df = filtered_df[filtered_df["brand"].str.lower() == selected_brand.lower()]
filtered_df = filtered_df[(filtered_df["price"] >= min_price) & (filtered_df["price"] <= max_price)]

if selected_category != "All":
    if selected_category == "Under $50":
        filtered_df = filtered_df[filtered_df["price"] < 50]
    elif selected_category == "$50-$150":
        filtered_df = filtered_df[(filtered_df["price"] >= 50) & (filtered_df["price"] <= 150)]
    elif selected_category == "$150-$300":
        filtered_df = filtered_df[(filtered_df["price"] > 150) & (filtered_df["price"] <= 300)]
    else:
        filtered_df = filtered_df[filtered_df["price"] > 300]

if sort_key == "price_high_to_low":
    filtered_df = filtered_df.sort_values(["price", "rating"], ascending=[False, False], kind="mergesort")
elif sort_key == "price_low_to_high":
    filtered_df = filtered_df.sort_values(["price", "rating"], ascending=[True, True], kind="mergesort")
elif sort_key == "rating_high_to_low":
    filtered_df = filtered_df.sort_values(["rating", "price"], ascending=[False, True], kind="mergesort")
else:
    filtered_df = filtered_df.sort_values("created_at", ascending=False, kind="mergesort")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Products", len(filtered_df))
col2.metric("Average price", format_currency(filtered_df["price"].mean()) if not filtered_df.empty else "N/A")
col3.metric("Lowest price", format_currency(filtered_df["price"].min()) if not filtered_df.empty else "N/A")
col4.metric("Top rating", f"{filtered_df['rating'].max():.1f}" if not filtered_df.empty and filtered_df['rating'].notna().any() else "N/A")

st.subheader("Price overview")
chart_df = filtered_df[["title", "price"]].copy().dropna(subset=["price"])
if chart_df.empty:
    st.info("No records match the current filters.")
else:
    st.bar_chart(chart_df.set_index("title")["price"].sort_values(ascending=False).head(15))

trend_df = filtered_df.dropna(subset=["created_at"]).copy()
if not trend_df.empty:
    trend_df["date"] = trend_df["created_at"].dt.strftime("%Y-%m-%d")
    trend_df = trend_df.groupby("date", as_index=False).agg(avg_price=("price", "mean"), product_count=("title", "count"))
    st.subheader("Price trend analysis")
    st.line_chart(trend_df.set_index("date")["avg_price"])

st.subheader("Price distribution")
if filtered_df["price"].notna().any():
    st.bar_chart(build_price_distribution(filtered_df))
else:
    st.info("No price data available for distribution.")

st.subheader("Price history tracker")
try:
    with sqlite3.connect(DB_PATH) as conn:
        history_df = pd.read_sql_query(
            """
            SELECT p.title, h.price, h.captured_at
            FROM product_price_history h
            JOIN products p ON p.id = h.product_id
            ORDER BY h.captured_at DESC
            """,
            conn,
        )
except Exception:
    history_df = pd.DataFrame(columns=["title", "price", "captured_at"])

if history_df.empty:
    st.info("No price history has been recorded yet. Run the scraper again to start tracking price changes over time.")
else:
    history_df = history_df.copy()
    history_df["price"] = pd.to_numeric(history_df.get("price", pd.Series(dtype=float)), errors="coerce")
    history_df["captured_at"] = pd.to_datetime(history_df.get("captured_at", pd.NaT), errors="coerce")
    history_titles = ["All"] + sorted(history_df["title"].dropna().unique().tolist())
    selected_history_title = st.selectbox("Track product price history", history_titles)

    if selected_history_title == "All":
        selected_history = history_df.sort_values("captured_at").dropna(subset=["price"])
    else:
        selected_history = history_df[history_df["title"] == selected_history_title].sort_values("captured_at")

    if selected_history.empty:
        st.info("No history is available for the selected product yet.")
    else:
        chart_data = selected_history.groupby("captured_at", as_index=False)["price"].mean()
        start_price = chart_data["price"].iloc[0] if not chart_data.empty else None
        current_price = chart_data["price"].iloc[-1] if not chart_data.empty else None
        if start_price and current_price:
            percent_change = ((current_price - start_price) / start_price) * 100 if start_price else 0.0
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Current price", format_currency(current_price))
            col_b.metric("Starting price", format_currency(start_price))
            col_c.metric("Change", f"{percent_change:+.1f}%")
        st.line_chart(chart_data.set_index("captured_at")["price"])

st.subheader("Top brands")
brand_counts = filtered_df["brand"].value_counts().head(8)
if brand_counts.empty:
    st.info("No brand information available yet.")
else:
    st.bar_chart(brand_counts)

st.subheader("Deal alerts")
alerts_df = filtered_df.copy()
alerts_df = alerts_df[(alerts_df["price"] <= alert_price_limit) & (alerts_df["rating"] >= min_alert_rating)]
alerts_df = alerts_df.sort_values(["price", "rating"], ascending=[True, False])
if alerts_df.empty:
    st.info("No deal alerts match your current thresholds.")
else:
    alert_preview = alerts_df[["brand", "title", "price", "rating", "url"]].head(10).copy()
    alert_preview["price"] = alert_preview["price"].map(format_currency)
    alert_preview["rating"] = alert_preview["rating"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "N/A")
    st.dataframe(alert_preview, use_container_width=True, hide_index=True)

st.subheader("Featured products")
if filtered_df.empty:
    st.info("No matching products were found for the selected filters.")
else:
    cards = filtered_df.head(12).copy()
    for index in range(0, len(cards), 4):
        columns = st.columns(4)
        for idx, (_, row) in enumerate(cards.iloc[index:index + 4].iterrows()):
            with columns[idx]:
                image_url = row.get("image_url") or ""
                if image_url:
                    try:
                        st.image(image_url, use_container_width=True)
                    except Exception:
                        st.markdown("🖼️ Image unavailable")
                else:
                    st.markdown("🖼️ No image")

                st.markdown(f"### {row['title'][:60]}")
                st.markdown(f"**Brand:** {row['brand']}")
                st.markdown(f"**Price:** {format_currency(row['price'])}")
                rating_value = row.get("rating")
                rating_text = f"{float(rating_value):.1f}" if pd.notna(rating_value) else "N/A"
                st.markdown(f"**Rating:** {rating_text}")
                if pd.notna(row.get("created_at")):
                    st.caption(row["created_at"].strftime("%Y-%m-%d %H:%M"))
                st.markdown(f"[Open product]({row['url']})")
                st.markdown("---")

st.subheader("Products table")
if filtered_df.empty:
    st.info("No matching products were found for the selected filters.")
else:
    display_df = filtered_df[["brand", "title", "price", "rating", "url", "created_at"]].copy()
    display_df["price"] = display_df["price"].map(format_currency)
    display_df["rating"] = display_df["rating"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "N/A")
    display_df["created_at"] = display_df["created_at"].dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    csv_download = display_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered CSV", csv_download, file_name="scraped_products.csv", mime="text/csv")

    try:
        excel_buffer = BytesIO()
        display_df.to_excel(excel_buffer, index=False)
        excel_data = excel_buffer.getvalue()
        st.download_button(
            "Download filtered Excel",
            excel_data,
            file_name="scraped_products.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception:
        st.caption("Excel export is unavailable until openpyxl/xlsxwriter is installed.")

st.markdown("---")
st.caption("Tip: use the sidebar filters to quickly search a subset of the products or export data for analysis.")
