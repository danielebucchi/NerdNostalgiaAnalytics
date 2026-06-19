"""
Pokemon TCG API collector.
Free API, no auth required. Provides:
- TCGPlayer prices (USA market)
- Cardmarket prices (EU market) including trend price and avg sell price

Also opportunistically records the official ptcgo set code into the local
expansion registry — Cardmarket's URL slugs and PTCGO/Live both use this short
code, so caching it speeds up future cross-source lookups.
"""
import logging
import re
from dataclasses import dataclass

import httpx

from src.utils.expansions import get_registry

logger = logging.getLogger(__name__)

API_BASE = "https://api.pokemontcg.io/v2"

# Cardmarket URLs look like .../Pokemon/Products/Singles/<set-slug>/<card-slug>
_CM_SET_SLUG = re.compile(r"cardmarket\.com/[^/]+/Pokemon/Products/Singles/([^/]+)/", re.IGNORECASE)
# TCGPlayer product URLs look like .../product/<numeric-id>/...
_TCG_PRODUCT_ID = re.compile(r"tcgplayer\.com/product/(\d+)", re.IGNORECASE)


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
    registry = get_registry()
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

        set_obj = card.get("set", {}) or {}
        await _record_set_codes(registry, set_obj, tcg_data.get("url"), cm_data.get("url"))

        results.append(CardPrices(
            name=card.get("name", ""),
            set_name=set_obj.get("name", ""),
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


async def _record_set_codes(registry, set_obj: dict, tcg_url: str | None, cm_url: str | None) -> None:
    """Persist Pokemon TCG API set codes into our expansion registry.

    The Pokemon TCG API set.id (e.g. `sv3pt5`) is our canonical code, so we use
    it as the registry key. We then record:
      - `ptcgo_code` from set.ptcgoCode (the official Pokémon TCG abbreviation;
        Cardmarket and PTCGL use the same letters in their URL slugs)
      - `cardmarket_code` from the URL slug of any card's cardmarket.url
      - `tcgplayer_code` from the product-id segment of any card's tcgplayer.url
    Each call is idempotent and silently no-ops when the set isn't in the
    registry — so cards from new/unlisted sets won't break us."""
    set_id = set_obj.get("id")
    if not set_id:
        return
    ptcgo = set_obj.get("ptcgoCode")
    if ptcgo:
        await registry.record_external_code(set_id, "ptcgo_code", ptcgo)
    if cm_url:
        m = _CM_SET_SLUG.search(cm_url)
        if m:
            await registry.record_external_code(set_id, "cardmarket_code", m.group(1))
    if tcg_url:
        m = _TCG_PRODUCT_ID.search(tcg_url)
        if m:
            # Note: this is a product-level ID, not a set-level one. We store
            # the most recent product-id seen for the set — useful as a known
            # "anchor" product when browsing TCGPlayer for that set.
            await registry.record_external_code(set_id, "tcgplayer_code", m.group(1))


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
