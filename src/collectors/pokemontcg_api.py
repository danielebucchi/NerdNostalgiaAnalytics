"""
Pokemon TCG API collector.
Free API, no auth required. Provides:
- TCGPlayer prices (USA market)
- Cardmarket prices (EU market) including trend price and avg sell price
"""
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.pokemontcg.io/v2"


@dataclass
class CardPrices:
    name: str
    set_name: str
    number: str
    # TCGPlayer (USA)
    tcg_low: float | None = None
    tcg_mid: float | None = None
    tcg_market: float | None = None
    tcg_high: float | None = None
    # Cardmarket (EU)
    cm_trend: float | None = None
    cm_avg_sell: float | None = None
    cm_low: float | None = None
    # Variant
    variant: str = "holofoil"
    # URLs
    tcg_url: str | None = None
    cm_url: str | None = None


async def search_card_prices(query: str, max_results: int = 5) -> list[CardPrices]:
    """
    Search Pokemon TCG API for card prices.
    Returns both TCGPlayer and Cardmarket prices.
    """
    # Build query - the API uses Lucene syntax
    # Handle common search patterns
    parts = query.lower().split()
    name_parts = [p for p in parts if not p.startswith("set:")]

    api_query = f'name:"{" ".join(name_parts)}"' if name_parts else f'name:{query}'

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API_BASE}/cards", params={
                "q": api_query,
                "select": "name,set,number,tcgplayer,cardmarket",
                "pageSize": str(max_results),
                "orderBy": "-set.releaseDate",
            })
            if r.status_code != 200:
                logger.warning(f"PokemonTCG API returned {r.status_code}")
                return []

            data = r.json()
    except Exception as e:
        logger.error(f"PokemonTCG API failed: {e}")
        return []

    results = []
    for card in data.get("data", []):
        tcg_data = card.get("tcgplayer", {})
        cm_data = card.get("cardmarket", {})

        tcg_prices = tcg_data.get("prices", {})
        cm_prices = cm_data.get("prices", {})

        # Get the best TCGPlayer variant prices
        tcg_low = tcg_mid = tcg_market = tcg_high = None
        variant = "normal"
        for var_name in ("holofoil", "reverseHolofoil", "normal", "1stEditionHolofoil"):
            if var_name in tcg_prices:
                vp = tcg_prices[var_name]
                tcg_low = vp.get("low")
                tcg_mid = vp.get("mid")
                tcg_market = vp.get("market")
                tcg_high = vp.get("high")
                variant = var_name
                break

        results.append(CardPrices(
            name=card.get("name", ""),
            set_name=card.get("set", {}).get("name", ""),
            number=card.get("number", ""),
            tcg_low=tcg_low,
            tcg_mid=tcg_mid,
            tcg_market=tcg_market,
            tcg_high=tcg_high,
            cm_trend=cm_prices.get("trendPrice"),
            cm_avg_sell=cm_prices.get("averageSellPrice"),
            cm_low=cm_prices.get("lowPrice"),
            variant=variant,
            tcg_url=tcg_data.get("url"),
            cm_url=cm_data.get("url"),
        ))

    return results


async def get_card_prices(name: str, set_name: str | None = None) -> CardPrices | None:
    """Get prices for a specific card, optionally filtered by set."""
    query = name
    if set_name:
        query = f'{name} {set_name}'
    results = await search_card_prices(query, max_results=1)
    return results[0] if results else None


def format_multi_source_prices(card: CardPrices) -> str:
    """Format prices from all sources for display."""
    lines = [f"📊 *{card.name}* #{card.number} ({card.set_name})\n"]

    if card.tcg_market or card.tcg_low:
        lines.append("*🇺🇸 TCGPlayer (USA):*")
        if card.tcg_market:
            lines.append(f"  Market: ${card.tcg_market:.2f}")
        if card.tcg_low and card.tcg_high:
            lines.append(f"  Range: ${card.tcg_low:.2f} - ${card.tcg_high:.2f}")

    if card.cm_trend or card.cm_avg_sell:
        lines.append("*🇪🇺 Cardmarket (EU):*")
        if card.cm_trend:
            lines.append(f"  Trend: €{card.cm_trend:.2f}")
        if card.cm_avg_sell:
            lines.append(f"  Media vendita: €{card.cm_avg_sell:.2f}")
        if card.cm_low:
            lines.append(f"  Minimo: €{card.cm_low:.2f}")

    if card.variant != "normal":
        lines.append(f"\n_Variante: {card.variant}_")

    return "\n".join(lines)
