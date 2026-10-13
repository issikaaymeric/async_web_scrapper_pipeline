import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def _clean_text(value: Optional[str]) -> str:
    if value is None:
        return "N/A"
    cleaned = " ".join(value.split())
    return cleaned if cleaned else "N/A"


def _extract_price(raw_value: str) -> float:
    if not raw_value:
        return 0.0
    matches = re.findall(r"\d+(?:\.\d+)?", raw_value)
    if not matches:
        return 0.0
    return float(matches[0])


def parse_product_html(html_content: str, url: str) -> Optional[Dict]:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")

    try:
        title_candidates = [
            soup.select_one("meta[property='og:title']"),
            soup.select_one("meta[name='twitter:title']"),
            soup.select_one("h1"),
            soup.select_one("[data-testid='product-title']"),
            soup.select_one(".product-title"),
            soup.select_one(".title"),
            soup.select_one("h2"),
        ]
        title = "N/A"
        for candidate in title_candidates:
            if candidate is None:
                continue
            title = _clean_text(candidate.get("content") or candidate.get_text(" ", strip=True))
            if title != "N/A":
                break

        price_candidates = [
            soup.select_one("meta[property='og:price:amount']"),
            soup.select_one("meta[name='twitter:data1']"),
            soup.select_one("[data-price]"),
            soup.select_one(".price_color"),
            soup.select_one(".price"),
            soup.select_one("[class*='price']"),
        ]
        price = 0.0
        for candidate in price_candidates:
            if candidate is None:
                continue
            raw = candidate.get("content") or candidate.get_text(" ", strip=True)
            parsed_price = _extract_price(str(raw))
            if parsed_price > 0:
                price = parsed_price
                break

        image_url = None
        image_tag = soup.select_one("meta[property='og:image']")
        if image_tag and image_tag.get("content"):
            image_url = image_tag.get("content")

        rating = None
        rating_tag = soup.select_one("meta[itemprop='ratingValue']") or soup.select_one("[data-asin-rating]")
        if rating_tag:
            raw_rating = rating_tag.get("content") or rating_tag.get("data-asin-rating") or rating_tag.get_text(strip=True)
            try:
                rating = float(raw_rating)
            except (TypeError, ValueError):
                rating = None

        return {
            "title": title,
            "price": price,
            "rating": rating,
            "image_url": image_url,
            "url": url,
        }
    except Exception as exc:
        print(f"Error parsing {url}: {exc}")
        return None


def parse_multiple_pages(html_list: List[str], urls: List[str]) -> List[Dict]:
    results = []
    for html, url in zip(html_list, urls):
        data = parse_product_html(html, url)
        if data and data["title"] != "N/A" and data["price"] > 0:
            results.append(data)
    return results


def extract_product_links(homepage_html: str, base_url: str) -> List[str]:
    """Find product links from either product-grid articles or generic product anchors."""
    if not homepage_html:
        return []

    soup = BeautifulSoup(homepage_html, "html.parser")
    links = set()

    for article in soup.find_all("article", class_="product_pod"):
        a_tag = article.find("h3")
        if not a_tag:
            continue
        anchor = a_tag.find("a")
        if anchor and anchor.get("href"):
            links.add(urljoin(base_url, anchor["href"]))

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        if not href or href.startswith("#"):
            continue
        full_url = urljoin(base_url, href)
        if any(token in full_url.lower() for token in ("/dp/", "/gp/product/", "/product/", "/p/")):
            links.add(full_url)

    return sorted(links)
