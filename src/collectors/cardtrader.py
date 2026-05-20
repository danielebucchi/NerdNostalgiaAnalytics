"""
CardTrader API collector (requires seller JWT token).
Provides real EU prices for Pokemon/Magic/YuGiOh cards.

Workflow:
1. Search blueprint (product template) by name + game
2. Get marketplace offers for that blueprint
3. Compute median price (mint/near mint) and minimum
"""
import logging
import time
from dataclasses import dataclass

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.cardtrader.com/api/v2"

GAME_IDS = {
    "pokemon": 5,
    "magic": 1,
    "yugioh": 4,
}

# Quality ranking: Mint > Near Mint > Excellent > Good > Light Played > Played > Poor
CONDITION_ORDER = {
    "Mint": 7, "Near Mint": 6, "Excellent": 5,
    "Good": 4, "Light Played": 3, "Played": 2, "Poor": 1,
}


@dataclass
class CardTraderOffer:
    price_eur: float
    condition: str
    quantity: int
    description: str


@dataclass
class CardTraderPrices:
    blueprint_id: int
    name: str
    expansion: str
    total_offers: int
    min_price_eur: float | None
    median_price_eur: float | None
    near_mint_min_eur: float | None
    offers: list[CardTraderOffer]


class CardTraderCollector:
    def __init__(self):
        # Cache blueprints to reduce API calls
        self._blueprint_cache: dict[str, list] = {}
        self._expansion_cache: dict[int, list] = {}

    @property
    def is_configured(self) -> bool:
        return bool(settings.cardtrader_token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.cardtrader_token}",
            "Content-Type": "application/json",
        }

    async def _get_expansions(self, game_id: int) -> list[dict]:
        """Get expansions for a game (cached)."""
        if game_id in self._expansion_cache:
            return self._expansion_cache[game_id]
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{API_BASE}/expansions", params={"game_id": game_id},
                                headers=self._headers())
                if r.status_code == 200:
                    data = r.json()
                    exps = data if isinstance(data, list) else data.get("array", [])
                    self._expansion_cache[game_id] = exps
                    return exps
        except Exception as e:
            logger.error(f"CardTrader expansions failed: {e}")
        return []

    async def _find_blueprint(self, name: str, game: str = "pokemon",
                              set_name: str | None = None) -> dict | None:
        """Find a blueprint matching name (+ optional set)."""
        game_id = GAME_IDS.get(game, 5)
        cache_key = f"{game}:{name.lower()}:{(set_name or '').lower()}"
        if cache_key in self._blueprint_cache:
            return self._blueprint_cache[cache_key]

        name_lower = name.lower()
        expansions = await self._get_expansions(game_id)

        # If set_name provided, narrow to matching expansions
        if set_name:
            set_lower = set_name.lower()
            expansions = [e for e in expansions if set_lower in e.get("name", "").lower()]

        # Search blueprints in each expansion (limit to first few to avoid too many API calls)
        for exp in expansions[:5]:
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(f"{API_BASE}/blueprints/export",
                                    params={"expansion_id": exp["id"]},
                                    headers=self._headers())
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    bps = data if isinstance(data, list) else data.get("array", [])
                    for bp in bps:
                        bp_name = (bp.get("name", "") + " " + bp.get("name_en", "")).lower()
                        if name_lower in bp_name:
                            bp["expansion_name"] = exp.get("name", "")
                            self._blueprint_cache[cache_key] = bp
                            return bp
            except Exception:
                continue

        self._blueprint_cache[cache_key] = None
        return None

    async def get_prices(self, name: str, game: str = "pokemon",
                         set_name: str | None = None) -> CardTraderPrices | None:
        """Get marketplace prices for a card."""
        if not self.is_configured:
            return None

        bp = await self._find_blueprint(name, game, set_name)
        if not bp:
            return None

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{API_BASE}/marketplace/products",
                                params={"blueprint_id": bp["id"]},
                                headers=self._headers())
                if r.status_code != 200:
                    return None
                data = r.json()
        except Exception as e:
            logger.error(f"CardTrader marketplace failed: {e}")
            return None

        # data is {blueprint_id: [products]}
        products = []
        for k, v in data.items():
            if isinstance(v, list):
                products.extend(v)

        offers = []
        for p in products:
            try:
                price = p.get("price_cents", 0) / 100.0
                if price <= 0:
                    continue
                cond = p.get("properties_hash", {}).get("condition", "Unknown")
                offers.append(CardTraderOffer(
                    price_eur=price,
                    condition=cond,
                    quantity=p.get("quantity", 1),
                    description=p.get("description", ""),
                ))
            except Exception:
                continue

        if not offers:
            return CardTraderPrices(
                blueprint_id=bp["id"], name=bp.get("name", name),
                expansion=bp.get("expansion_name", ""),
                total_offers=0, min_price_eur=None, median_price_eur=None,
                near_mint_min_eur=None, offers=[],
            )

        prices = [o.price_eur for o in offers]
        prices_sorted = sorted(prices)
        n = len(prices_sorted)
        median = prices_sorted[n // 2] if n % 2 else (prices_sorted[n // 2 - 1] + prices_sorted[n // 2]) / 2

        # Find min Near Mint or better
        nm_offers = [o for o in offers if CONDITION_ORDER.get(o.condition, 0) >= 6]
        nm_min = min(o.price_eur for o in nm_offers) if nm_offers else None

        return CardTraderPrices(
            blueprint_id=bp["id"],
            name=bp.get("name", name),
            expansion=bp.get("expansion_name", ""),
            total_offers=len(offers),
            min_price_eur=round(prices_sorted[0], 2),
            median_price_eur=round(median, 2),
            near_mint_min_eur=round(nm_min, 2) if nm_min else None,
            offers=offers,
        )


cardtrader = CardTraderCollector()
