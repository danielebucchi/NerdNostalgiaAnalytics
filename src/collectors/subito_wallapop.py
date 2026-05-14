"""
Subito.it and Wallapop search link generators.
Both platforms block API scraping, so we generate direct search URLs.
"""
from urllib.parse import quote_plus


def subito_search_url(query: str, price_max: float | None = None) -> str:
    """Generate a Subito.it search URL."""
    base = f"https://www.subito.it/annunci-italia/vendita/usato/?q={quote_plus(query)}"
    if price_max:
        base += f"&ps=0&pe={int(price_max)}"
    base += "&o=1"  # Sort by price ascending
    return base


def wallapop_search_url(query: str, price_max: float | None = None) -> str:
    """Generate a Wallapop search URL."""
    base = f"https://es.wallapop.com/app/search?keywords={quote_plus(query)}"
    base += "&order_by=price_low_to_high"
    if price_max:
        base += f"&max_sale_price={int(price_max)}"
    return base


def mercatino_search_url(query: str) -> str:
    """Generate a Il Mercatino search URL (Italian collectibles marketplace)."""
    return f"https://www.mercatinousato.com/ricerca/?q={quote_plus(query)}"
