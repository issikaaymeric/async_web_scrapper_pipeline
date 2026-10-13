import os
from typing import Dict, List

import pandas as pd


def _normalise_title(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    text = str(value).strip()
    if not text or text.lower() == "n/a":
        return "N/A"
    return " ".join(text.split())


def calculate_price_change(current_price: float, previous_price: float | None) -> float:
    if previous_price in (None, 0) or pd.isna(current_price) or pd.isna(previous_price):
        return 0.0
    return ((float(current_price) - float(previous_price)) / float(previous_price)) * 100.0


def clean_scraped_data(raw_data: List[Dict]) -> List[Dict]:
    """Clean scraped products and keep only valid, deduplicated records."""
    filtered = [item for item in raw_data if item is not None]
    if not filtered:
        return []

    df = pd.DataFrame(filtered)
    if df.empty:
        return []

    if "title" in df.columns:
        df["title"] = df["title"].map(_normalise_title)
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")

    df = df[df["title"] != "N/A"]
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["url"], keep="last")

    if "title" in df.columns:
        df["title"] = df["title"].str.replace(r"\s+", " ", regex=True)

    df = df.sort_values(by=["price", "title"], ascending=[True, True], kind="mergesort")
    return df.to_dict(orient="records")


def export_scraped_data(records: List[Dict], output_format: str = "csv", output_dir: str = "data") -> str:
    """Export cleaned records to CSV or JSON for downstream workflows."""
    if not records:
        return ""

    os.makedirs(output_dir, exist_ok=True)
    if output_format not in {"csv", "json"}:
        raise ValueError(f"Unsupported format: {output_format}")

    output_path = os.path.join(output_dir, f"products.{output_format}")
    dataframe = pd.DataFrame(records)
    if output_format == "csv":
        dataframe.to_csv(output_path, index=False)
    else:
        dataframe.to_json(output_path, orient="records", indent=2)
    return output_path
