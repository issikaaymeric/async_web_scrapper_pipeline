import json
import logging
import math
import urllib.request

logger = logging.getLogger("scraper.currency")

# code -> (symbol, decimals, symbol_goes_after_number)
CURRENCY_INFO: dict[str, tuple[str, int, bool]] = {
    "USD": ("$", 2, False), "EUR": ("€", 2, False), "GBP": ("£", 2, False),
    "XOF": ("FCFA", 0, True), "XAF": ("FCFA", 0, True),
    "NGN": ("₦", 2, False), "GHS": ("GH₵", 2, False), "KES": ("KSh", 2, False),
    "ZAR": ("R", 2, False), "MAD": ("MAD", 2, True), "EGP": ("EGP", 2, True),
    "CAD": ("CA$", 2, False), "AUD": ("A$", 2, False), "NZD": ("NZ$", 2, False),
    "JPY": ("¥", 0, False), "CNY": ("CN¥", 2, False), "INR": ("₹", 2, False),
    "BRL": ("R$", 2, False), "CHF": ("CHF", 2, True), "SEK": ("kr", 2, True),
    "NOK": ("kr", 2, True), "DKK": ("kr", 2, True), "PLN": ("zł", 2, True),
    "TRY": ("₺", 2, False), "AED": ("AED", 2, True), "MXN": ("MX$", 2, False),
}

# Pegged currencies: fixed rate to the euro (used when the rate API lacks them).
EURO_PEGS = {"XOF": 655.957, "XAF": 655.957}

RATE_SOURCES = (
    "https://open.er-api.com/v6/latest/USD",
    "https://api.frankfurter.dev/v1/latest?base=USD",
)


def _download(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "scrape-dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URLs)
        return json.loads(response.read().decode("utf-8"))


def fetch_rates(timeout: float = 8.0) -> tuple[dict[str, float] | None, str]:
    """Return ({code: units per 1 USD}, updated_label), or (None, '') if every source fails."""
    for url in RATE_SOURCES:
        try:
            payload = _download(url, timeout)
            rates = {k.upper(): float(v) for k, v in payload.get("rates", {}).items() if v}
        except Exception as exc:  # network, JSON, bad values
            logger.warning("Rate source failed (%s): %s", url, exc)
            continue
        if not rates:
            continue
        rates["USD"] = 1.0
        if "EUR" in rates:
            for code, per_euro in EURO_PEGS.items():
                rates.setdefault(code, rates["EUR"] * per_euro)
        updated = payload.get("time_last_update_utc") or payload.get("date") or ""
        return rates, str(updated)
    return None, ""


def convert(amount: float, source: str, target: str, rates: dict[str, float] | None) -> float:
    """Convert via USD. Returns NaN when a rate is missing."""
    if source == target:
        return float(amount)
    if not rates or source not in rates or target not in rates:
        return math.nan
    return float(amount) / rates[source] * rates[target]


def format_money(value, code: str) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"
    symbol, decimals, after = CURRENCY_INFO.get(code, (code, 2, True))
    number = f"{value:,.{decimals}f}"
    return f"{number} {symbol}" if after else f"{symbol}{number}"


def price_format(code: str) -> str:
    """printf-style format for Streamlit column_config.NumberColumn."""
    symbol, decimals, after = CURRENCY_INFO.get(code, (code, 2, True))
    return f"%.{decimals}f {symbol}" if after else f"{symbol}%.{decimals}f"