import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag


from core.currency import CURRENCY_INFO

# Longest symbols first so "US$" wins over "$" and "FCFA" over "CFA".
_SYMBOLS = sorted(
    {
        "FCFA": "XOF", "F CFA": "XOF", "CFA": "XOF", "US$": "USD", "CA$": "CAD",
        "A$": "AUD", "R$": "BRL", "GH₵": "GHS", "KSh": "KES", "€": "EUR", "£": "GBP",
        "₦": "NGN", "₵": "GHS", "₹": "INR", "₺": "TRY", "zł": "PLN", "¥": "JPY", "$": "USD",
    }.items(),
    key=lambda item: -len(item[0]),
)
_ISO_CODE = re.compile(r"\b[A-Z]{3}\b")
_DOLLAR_LIKE = {"USD", "CAD", "AUD", "NZD", "MXN"}


def _normalise_code(value: Any) -> Optional[str]:
    if isinstance(value, str):
        code = value.strip().upper()
        if len(code) == 3 and code.isalpha():
            return code
    return None


def _currency_from_text(raw: Any, default: Optional[str] = None) -> Optional[str]:
    """Find an ISO code or symbol in price text such as '12 500 FCFA' or '£12.99'."""
    if not raw:
        return None
    text = str(raw)
    for match in _ISO_CODE.findall(text):
        if match in CURRENCY_INFO:
            return match
    for symbol, code in _SYMBOLS:
        if symbol in text:
            if symbol == "$" and default in _DOLLAR_LIKE:   # bare "$" is ambiguous
                return default
            if code == "XOF" and default == "XAF":          # FCFA is shared by both zones
                return "XAF"
            return code
    return None


def _offer_currency(offers: Any, item: Dict[str, Any]) -> Optional[str]:
    if isinstance(offers, list):
        offers = next((o for o in offers if isinstance(o, dict)), {})
    if not isinstance(offers, dict):
        offers = {}
    spec = offers.get("priceSpecification")
    if isinstance(spec, list):
        spec = next((s for s in spec if isinstance(s, dict)), {})
    spec_currency = spec.get("priceCurrency") if isinstance(spec, dict) else None
    return _normalise_code(
        _first_value(offers.get("priceCurrency"), spec_currency, item.get("priceCurrency"))
    )

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
ASSET_PATH_PATTERN = re.compile(
    r"\.(?:7z|avi|css|csv|docx?|eot|gif|gz|ico|jpe?g|js|json|"
    r"m4a|mp[34]|pdf|png|pptx?|rar|svg|tar|webm|webp|woff2?|"
    r"xlsx?|xml|zip)$",
    re.IGNORECASE,
)
TRACKING_PARAM_PATTERN = re.compile(r"^(?:utm_.+|gclid|fbclid|msclkid)$", re.IGNORECASE)
PAGINATION_PATH_PATTERN = re.compile(r"(?:[?&](?:page|paged|pg)=\d+|/page[-/_]?\d+)", re.IGNORECASE)
PAGINATION_LABELS = {"next", "next page", "suivant", "page suivante", ">", "\u203a", "\u00bb", "\u2192"}
STAR_WORDS = {"one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0}

# Digits with optional separators. Whitespace only counts as a thousands separator when it
# is followed by exactly three digits, so "12.99 19.99" is NOT merged into one number.
PRICE_PATTERN = re.compile(r"\d(?:[\d.,]|\s(?=\d{3}(?!\d)))*")
EXOTIC_SPACES = re.compile(r"[\u00a0\u202f\u2009]")


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

    match = PRICE_PATTERN.search(EXOTIC_SPACES.sub(" ", str(raw_value)))
    if not match:
        return 0.0
    numeric = re.sub(r"\s", "", match.group()).rstrip(".,")
    if not numeric:
        return 0.0

    if "," in numeric and "." in numeric:
        decimal_separator = "," if numeric.rfind(",") > numeric.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        numeric = numeric.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in numeric:
        tail = numeric.rsplit(",", 1)[-1]
        numeric = numeric.replace(",", "." if len(tail) in (1, 2) else "")
    elif numeric.count(".") > 1:
        parts = numeric.split(".")
        if all(len(part) == 3 for part in parts[1:]):  # 1.234.567 -> thousands separators
            numeric = "".join(parts)
        else:
            numeric = "".join(parts[:-1]) + "." + parts[-1]

    try:
        result = float(numeric)
    except ValueError:
        return 0.0
    return result if result > 0 else 0.0


def _iter_json_objects(value: Any) -> Iterator[Dict[str, Any]]:
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


def _offer_price(offers: Any, item: Dict[str, Any]) -> Any:
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), {})
    if not isinstance(offers, dict):
        offers = {}
    spec = offers.get("priceSpecification")
    if isinstance(spec, list):
        spec = next((entry for entry in spec if isinstance(entry, dict)), {})
    spec_price = spec.get("price") if isinstance(spec, dict) else None
    return _first_value(
        offers.get("price"),
        offers.get("lowPrice"),
        spec_price,
        item.get("price"),
    )


def _product_from_json_ld(soup: BeautifulSoup) -> Dict[str, Any]:
    """Return the first Product with a usable price, else the first Product found."""
    fallback: Dict[str, Any] = {}
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw.strip(), strict=False)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _iter_json_objects(parsed):
            if not _is_product_schema(item):
                continue
            aggregate_rating = item.get("aggregateRating")
            if not isinstance(aggregate_rating, dict):
                aggregate_rating = {}
            candidate = {
                "title": _clean_text(item.get("name")),
                "price": _price_number(_offer_price(item.get("offers"), item)),
                "currency": _normalise_code(_offer_currency(item.get("offers"), item)),
                "rating": _price_number(aggregate_rating.get("ratingValue")) or None,
                "image_url": _image_value(item.get("image")),
            }
            if candidate["price"] > 0:
                return candidate
            fallback = fallback or candidate
    return fallback


def _meta_content(soup: BeautifulSoup, *selectors: str) -> Any:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            content = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
            if content:
                return content
    return None


def _first_image_src(soup: BeautifulSoup, *selectors: str) -> Optional[str]:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            src = tag.get("src") or tag.get("data-src") or tag.get("content")
            if isinstance(src, str) and src.strip():
                return src.strip()
    return None


def _star_class_rating(soup: BeautifulSoup) -> Optional[float]:
    """Ratings encoded as CSS classes, e.g. <p class="star-rating Four">."""
    tag = soup.select_one("[class*='star-rating']")
    if not tag:
        return None
    for css_class in tag.get("class", []):
        if css_class.lower() in STAR_WORDS:
            return STAR_WORDS[css_class.lower()]
    return None


def _has_product_signal(soup: BeautifulSoup, structured: Dict[str, Any], url: str) -> bool:
    """True only for pages that explicitly declare themselves as a product."""
    if structured:
        return True
    if "product" in str(_meta_content(soup, 'meta[property="og:type"]') or "").lower():
        return True
    if soup.select_one(
        'meta[property="product:price:amount"], meta[property="og:price:amount"], [itemprop="price"]'
    ):
        return True
    path = urlsplit(url).path
    return bool(PRODUCT_PATH_PATTERN.search(path)) and not NON_PRODUCT_PATH_PATTERN.search(path)


def parse_product_html(
    html_content: str,
    url: str,
    *,
    require_product_signal: bool = False,
    default_currency: Optional[str] = None,
) -> Optional[Dict]:
    """Parse a product page. With require_product_signal, listing/info pages return None."""
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")
    structured = _product_from_json_ld(soup)
    if require_product_signal and not _has_product_signal(soup, structured, url):
        return None

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
    raw_price = None
    price = _price_number(structured.get("price"))
    if not price:
        raw_price = _meta_content(
            soup,
            'meta[property="product:price:amount"]',
            'meta[property="og:price:amount"]',
            'meta[itemprop="price"]',
            '[itemprop="price"]',
            'meta[name="twitter:data1"]',
            "[data-price]",
            "[class*='price']",
        )
        price = _price_number(raw_price)

    currency = structured.get("currency") or _normalise_code(
        _meta_content(
            soup,
            'meta[property="product:price:currency"]',
            'meta[property="og:price:currency"]',
            'meta[itemprop="priceCurrency"]',
            '[itemprop="priceCurrency"]',
        )
    )
    if not currency:
        currency = _currency_from_text(raw_price, default_currency)
    currency = currency or default_currency or "USD"

    image_url = (
        structured.get("image_url")
        or _meta_content(
            soup,
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
            'meta[itemprop="image"]',
        )
        or _first_image_src(
            soup,
            'img[itemprop="image"]',
            "[class*='gallery'] img",
            "[class*='product'] img",
        )
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
        rating = _price_number(raw_rating) or _star_class_rating(soup) or None

    return {
        "title": _clean_text(title),
        "price": price,
        "currency": currency,
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


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(candidate_url: str, base_url: str) -> bool:
    candidate = urlsplit(candidate_url)
    return (
        candidate.scheme in ("http", "https")
        and bool(candidate.netloc)
        and _host(candidate_url) == _host(base_url)  # example.com == www.example.com
    )


def _normalize_url(url: str) -> str:
    """Drop the fragment and tracking parameters so equivalent URLs dedupe."""
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    if parts.query:
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if not TRACKING_PARAM_PATTERN.match(key)
            ]
        )
        parts = parts._replace(query=query)
    return urlunsplit(parts)


def _is_pagination_link(anchor: Tag, full_url: str) -> bool:
    rel = anchor.get("rel", [])
    if isinstance(rel, str):
        rel = rel.split()
    if "next" in {str(value).lower() for value in rel}:
        return True

    label = " ".join(anchor.get_text(" ", strip=True).split()).lower()
    if label in PAGINATION_LABELS:
        return True

    parent = anchor.parent
    if isinstance(parent, Tag) and "next" in {str(value).lower() for value in parent.get("class", [])}:
        return True
    return bool(PAGINATION_PATH_PATTERN.search(full_url))


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


def _candidate_links(page_html: str, base_url: str) -> Iterator[Tuple[Tag, str]]:
    """Yield (anchor, normalized_url) for crawlable same-site HTML links."""
    if not page_html:
        return

    soup = BeautifulSoup(page_html, "html.parser")
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        rel = anchor.get("rel", [])
        if isinstance(rel, str):
            rel = rel.split()
        if "nofollow" in {str(value).lower() for value in rel}:
            continue

        full_url = _normalize_url(urljoin(base_url, href))
        if not _same_site(full_url, base_url):
            continue
        if ASSET_PATH_PATTERN.search(urlsplit(full_url).path):
            continue
        yield anchor, full_url


def extract_links(page_html: str, base_url: str) -> Tuple[List[str], List[str], List[str]]:
    """Single-parse split of a page's links into (product, pagination, other) lists.

    The lists are disjoint. Precedence: pagination > product > other.
    """
    products: set = set()
    pagination: set = set()
    others: set = set()
    for anchor, url in _candidate_links(page_html, base_url):
        if _is_pagination_link(anchor, url):
            pagination.add(url)
        elif _link_looks_like_product(anchor, url):
            products.add(url)
        else:
            others.add(url)

    products -= pagination
    others -= products | pagination
    return sorted(products), sorted(pagination), sorted(others)


def extract_product_links(homepage_html: str, base_url: str) -> List[str]:
    """Discover likely product URLs without collecting unrelated navigation links."""
    return extract_links(homepage_html, base_url)[0]


def extract_internal_links(page_html: str, base_url: str) -> List[str]:
    """Return crawlable same-site HTML links, excluding assets and opt-out links."""
    products, pagination, others = extract_links(page_html, base_url)
    return sorted(products + pagination + others)