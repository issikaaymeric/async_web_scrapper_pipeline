from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    currency: str
    locale: str            # used for the headless browser
    accept_language: str   # used for HTTP requests


_ALL = [
    Region("CI", "Côte d'Ivoire", "XOF", "fr-CI", "fr-CI,fr;q=0.9,en;q=0.5"),
    Region("SN", "Senegal", "XOF", "fr-SN", "fr-SN,fr;q=0.9,en;q=0.5"),
    Region("CM", "Cameroon", "XAF", "fr-CM", "fr-CM,fr;q=0.9,en;q=0.5"),
    Region("NG", "Nigeria", "NGN", "en-NG", "en-NG,en;q=0.9"),
    Region("GH", "Ghana", "GHS", "en-GH", "en-GH,en;q=0.9"),
    Region("KE", "Kenya", "KES", "en-KE", "en-KE,en;q=0.9"),
    Region("ZA", "South Africa", "ZAR", "en-ZA", "en-ZA,en;q=0.9"),
    Region("MA", "Morocco", "MAD", "fr-MA", "fr-MA,fr;q=0.9,en;q=0.5"),
    Region("FR", "France", "EUR", "fr-FR", "fr-FR,fr;q=0.9,en;q=0.5"),
    Region("DE", "Germany", "EUR", "de-DE", "de-DE,de;q=0.9,en;q=0.5"),
    Region("GB", "United Kingdom", "GBP", "en-GB", "en-GB,en;q=0.9"),
    Region("US", "United States", "USD", "en-US", "en-US,en;q=0.9"),
    Region("CA", "Canada", "CAD", "en-CA", "en-CA,en;q=0.9,fr;q=0.5"),
    Region("AU", "Australia", "AUD", "en-AU", "en-AU,en;q=0.9"),
    Region("IN", "India", "INR", "en-IN", "en-IN,en;q=0.9"),
    Region("BR", "Brazil", "BRL", "pt-BR", "pt-BR,pt;q=0.9,en;q=0.5"),
    Region("JP", "Japan", "JPY", "ja-JP", "ja-JP,ja;q=0.9,en;q=0.5"),
]

REGIONS: dict[str, Region] = {r.code: r for r in _ALL}


def get_region(code: str | None) -> Region | None:
    """Return the Region for a country code (case-insensitive), or None for 'auto'."""
    return REGIONS.get((code or "").strip().upper())