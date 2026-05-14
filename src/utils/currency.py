import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Cache exchange rates for 6 hours
_rates_cache: dict[str, float] = {}
_cache_timestamp: float = 0
_CACHE_TTL = 6 * 3600


async def get_exchange_rates() -> dict[str, float]:
    """Fetch exchange rates from free API. Base: USD."""
    global _rates_cache, _cache_timestamp

    if _rates_cache and (time.time() - _cache_timestamp) < _CACHE_TTL:
        return _rates_cache

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Free API, no key needed
            r = await client.get("https://open.er-api.com/v6/latest/USD")
            if r.status_code == 200:
                data = r.json()
                _rates_cache = data.get("rates", {})
                _cache_timestamp = time.time()
                logger.info(f"Exchange rates updated: EUR={_rates_cache.get('EUR', '?')}")
                return _rates_cache
    except Exception as e:
        logger.warning(f"Failed to fetch exchange rates: {e}")

    # Fallback rates
    if not _rates_cache:
        _rates_cache = {"EUR": 0.92, "GBP": 0.79, "JPY": 155.0, "CHF": 0.88}
    return _rates_cache


def usd_to_eur(usd: float, rates: dict[str, float] | None = None) -> float:
    if rates and "EUR" in rates:
        return usd * rates["EUR"]
    return usd * 0.92


def eur_to_usd(eur: float, rates: dict[str, float] | None = None) -> float:
    if rates and "EUR" in rates:
        return eur / rates["EUR"]
    return eur / 0.92


def format_price(usd: float, rates: dict[str, float] | None = None) -> str:
    """Format price showing both USD and EUR."""
    eur = usd_to_eur(usd, rates)
    return f"${usd:.2f} (~€{eur:.2f})"
