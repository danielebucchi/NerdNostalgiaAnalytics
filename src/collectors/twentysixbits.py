"""
26bits.it collector — Italian retrogaming store.
Uses their internal JSON API: /api/products (returns full catalog).
"""
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://www.26bits.it/api/products"
BASE_URL = "https://www.26bits.it"


@dataclass
class TwentySixBitsListing:
    name: str
    price_eur: float
    original_price_eur: float
    condition: str
    platform: str
    availability: str
    url: str
    on_sale: bool = False


# Cache the full catalog (changes infrequently)
_catalog_cache: list[dict] = []
_cache_time: float = 0


async def _get_catalog() -> list[dict]:
    """Fetch the full 26bits catalog. Cached for 1 hour."""
    global _catalog_cache, _cache_time
    import time

    if _catalog_cache and (time.time() - _cache_time) < 3600:
        return _catalog_cache

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(API_URL)
            if r.status_code == 200:
                _catalog_cache = r.json()
                _cache_time = time.time()
                logger.info(f"26bits catalog loaded: {len(_catalog_cache)} products")
                return _catalog_cache
    except Exception as e:
        logger.error(f"26bits API failed: {e}")

    return _catalog_cache


async def search_26bits(query: str, max_results: int = 10) -> list[TwentySixBitsListing]:
    """Search 26bits.it catalog for products matching the query."""
    catalog = await _get_catalog()
    if not catalog:
        return []

    query_lower = query.lower()
    query_words = query_lower.split()

    results = []
    for product in catalog:
        if product.get("availability") != "available":
            continue

        name = product.get("name", "")
        name_lower = name.lower()
        platform = product.get("platform", "").lower()
        description = product.get("description", "").lower()

        # Match: all query words must appear in name, platform, or description
        searchable = f"{name_lower} {platform} {description}"
        if all(word in searchable for word in query_words):
            price = product.get("price", 0)
            original = product.get("originalPrice", price)

            results.append(TwentySixBitsListing(
                name=name,
                price_eur=float(price),
                original_price_eur=float(original) if original else float(price),
                condition=product.get("condition", ""),
                platform=product.get("platform", ""),
                availability=product.get("availabilityLabel", ""),
                url=f"{BASE_URL}/catalogo/{product.get('slug', '')}",
                on_sale=product.get("onSale", False),
            ))

    # Sort by price
    results.sort(key=lambda x: x.price_eur)
    return results[:max_results]


async def get_26bits_price(query: str) -> float | None:
    """Get the average price from 26bits for a product. Returns EUR or None."""
    results = await search_26bits(query, max_results=5)
    if not results:
        return None
    prices = [r.price_eur for r in results]
    return sum(prices) / len(prices)


def twentysixbits_search_url(query: str) -> str:
    from urllib.parse import quote_plus
    return f"{BASE_URL}/?s={quote_plus(query)}&post_type=product"
