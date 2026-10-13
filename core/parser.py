import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag


PRODUCT_PATH_PATTERN = re.compile(
    r"/(?:dp|gp/product|product|products|item|items|p|shop)/", re.IGNORECASE
)
PRODUCT_CONTEXT_PATTERN = re.compile(
    r"product|item|listing|result|catalog|card|tile|offer", re.IGNORECASE
)
NON_PRODUCT_PATH_PATTERN = re.compile(
    r"/(?:account|cart|checkout|login|register|search|help|support|"
    r"privacy|terms|contact|about|blog|category|categories|collections?)(?:/|$)",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(r"\d[\d\s.,]*")


def _clean_text(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item), None)
    if not isinstance(value, str):
        value = str(value)
    cleaned = " ".join(value.split())
    return cleaned if cleaned else "N/A"


def _price_number(raw_value: Any) -> float:
    """Parse common decimal/thousands separators without treating currency as digits."""
    if raw_value is None:
        return 0.0
    if isinstance(raw_value, (int, float)):
        return float(raw_value) if raw_value > 0 else 0.0

    match = PRICE_PATTERN.search(str(raw_value).replace("\u00a0", " "))
    if not match:
        return 0.0
    numeric = match.group().replace(" ", "")
    if "," in numeric and "." in numeric:
        decimal_separator = "," if numeric.rfind(",") > numeric.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        numeric = numeric.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in numeric:
        tail = numeric.rsplit(",", 1)[-1]
        numeric = numeric.replace(",", "." if len(tail) in (1, 2) else "")
    elif numeric.count(".") > 1:
        parts = numeric.split(".")
        numeric = "".join(parts[:-1]) + "." + parts[-1]

    try:
        result = float(numeric)
    except ValueError:
        return 0.0
    return result if result > 0 else 0.0


def _iter_json_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_objects(child)


def _is_product_schema(value: Dict[str, Any]) -> bool:
    schema_type = value.get("@type", [])
    if isinstance(schema_type, str):
        schema_type = [schema_type]
    return any(str(item).lower().endswith("product") for item in schema_type)


def _first_value(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "", [], {})), None)


def _image_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if isinstance(value, list):
        value = next((item for item in value if item), None)
        return _image_value(value)
    return value if isinstance(value, str) and value.strip() else None


def _product_from_json_ld(soup: BeautifulSoup) -> Dict[str, Any]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _iter_json_objects(parsed):
            if not _is_product_schema(item):
                continue
            offers = item.get("offers")
            if isinstance(offers, list):
                offers = next((offer for offer in offers if isinstance(offer, dict)), {})
            if not isinstance(offers, dict):
                offers = {}
            aggregate_rating = item.get("aggregateRating")
            if not isinstance(aggregate_rating, dict):
                aggregate_rating = {}
            price = _first_value(
                offers.get("price"),
                offers.get("lowPrice"),
                item.get("price"),
            )
            return {
                "title": _clean_text(item.get("name")),
                "price": _price_number(price),
                "rating": _price_number(aggregate_rating.get("ratingValue")) or None,
                "image_url": _image_value(item.get("image")),
            }
    return {}


def _meta_content(soup: BeautifulSoup, *selectors: str) -> Any:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            content = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
            if content:
                return content
    return None


def parse_product_html(html_content: str, url: str) -> Optional[Dict]:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")
    structured = _product_from_json_ld(soup)

    title = _first_value(
        structured.get("title") if structured.get("title") != "N/A" else None,
        _meta_content(
            soup,
            'meta[property="og:title"]',
            'meta[name="twitter:title"]',
            'meta[name="title"]',
            '[itemprop="name"]',
            "[data-testid='product-title']",
            ".product-title",
            "h1",
        ),
    )
    price = _price_number(structured.get("price"))
    if not price:
        price = _price_number(
            _meta_content(
                soup,
                'meta[property="product:price:amount"]',
                'meta[property="og:price:amount"]',
                'meta[itemprop="price"]',
                '[itemprop="price"]',
                'meta[name="twitter:data1"]',
                "[data-price]",
                "[class*='price']",
            )
        )

    image_url = structured.get("image_url") or _meta_content(
        soup,
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[itemprop="image"]',
    )
    if image_url and not image_url.startswith(("http://", "https://", "data:")):
        image_url = urljoin(url, image_url)

    rating = structured.get("rating")
    if rating is None:
        raw_rating = _meta_content(
            soup,
            'meta[itemprop="ratingValue"]',
            '[itemprop="ratingValue"]',
            "[data-rating]",
            "[data-asin-rating]",
        )
        parsed_rating = _price_number(raw_rating)
        rating = parsed_rating or None

    return {
        "title": _clean_text(title),
        "price": price,
        "rating": rating,
        "image_url": image_url,
        "url": url,
    }


def parse_multiple_pages(html_list: List[str], urls: List[str]) -> List[Dict]:
    results = []
    for html, url in zip(html_list, urls):
        data = parse_product_html(html, url)
        if data and data["title"] != "N/A" and data["price"] > 0:
            results.append(data)
    return results


def _same_site(candidate_url: str, base_url: str) -> bool:
    candidate = urlsplit(candidate_url)
    base = urlsplit(base_url)
    return (
        candidate.scheme in ("http", "https")
        and bool(candidate.netloc)
        and candidate.hostname == base.hostname
    )


def _link_looks_like_product(anchor: Tag, full_url: str) -> bool:
    path = urlsplit(full_url).path
    if NON_PRODUCT_PATH_PATTERN.search(path):
        return False
    if PRODUCT_PATH_PATTERN.search(path):
        return True

    context = anchor
    for depth in range(3):
        if context.parent and isinstance(context.parent, Tag):
            context = context.parent
            descriptor = " ".join(
                [
                    str(context.get("class", "")),
                    str(context.get("id", "")),
                    str(context.get("itemtype", "")),
                ]
            )
            if PRODUCT_CONTEXT_PATTERN.search(descriptor):
                return True
            if depth < 2 and context.name not in {"body", "html", "main", "nav", "header", "footer"}:
                if context.select_one("[itemprop='price'], [class*='price'], [data-price]"):
                    return True
    return False


def extract_product_links(homepage_html: str, base_url: str) -> List[str]:
    """Discover likely product URLs without collecting unrelated navigation links."""
    if not homepage_html:
        return []

    soup = BeautifulSoup(homepage_html, "html.parser")
    links = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        if _same_site(full_url, base_url) and _link_looks_like_product(anchor, full_url):
            links.add(full_url)
    return sorted(links)


def extract_internal_links(page_html: str, base_url: str) -> List[str]:
    """Return crawlable same-host HTML links, excluding assets and opt-out links."""
    if not page_html:
        return []

    soup = BeautifulSoup(page_html, "html.parser")
    links = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        rel = anchor.get("rel", [])
        if isinstance(rel, str):
            rel = rel.split()
        if "nofollow" in {str(value).lower() for value in rel}:
            continue

        full_url, _ = urldefrag(urljoin(base_url, href))
        if not _same_site(full_url, base_url):
            continue
        path = urlsplit(full_url).path.lower()
        if re.search(
            r"\.(?:7z|avi|css|csv|docx?|eot|gif|gz|ico|jpe?g|js|json|"
            r"m4a|mp[34]|pdf|png|pptx?|rar|svg|tar|webm|webp|woff2?|"
            r"xlsx?|xml|zip)$",
            path,
        ):
            continue
        links.add(full_url)
    return sorted(links)
