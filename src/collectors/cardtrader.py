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
from src.utils.condition import CardCondition, card_condition_from_label
from src.utils.expansions import get_registry

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

    @property
    def condition_obj(self) -> CardCondition:
        """Parsed condition (canonical label first, fallback to freetext on description)."""
        cc = card_condition_from_label(self.condition)
        if cc.is_known:
            return cc
        return card_condition_from_label(self.description)


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

    def offers_matching(self, target: CardCondition) -> list[CardTraderOffer]:
        """Offers with quality score >= target. Empty target → all offers."""
        if not target.is_known:
            return list(self.offers)
        threshold = target.quality_score
        return [o for o in self.offers if o.condition_obj.quality_score >= threshold]

    def median_for_condition(self, target: CardCondition) -> float | None:
        """Median price among offers matching `target` or better. None if no match."""
        prices = sorted(o.price_eur for o in self.offers_matching(target))
        if not prices:
            return None
        n = len(prices)
        return prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2

    def min_for_condition(self, target: CardCondition) -> float | None:
        """Minimum price among offers matching `target` or better."""
        prices = [o.price_eur for o in self.offers_matching(target)]
        return min(prices) if prices else None


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
                              set_name: str | None = None,
                              expansion_code: str | None = None) -> dict | None:
        """Find a blueprint matching `name` (+ optional set hint or expansion code).

        Fast path: if `expansion_code` is provided and the registry already knows
        the CardTrader `cardtrader_id` for it, jump straight to blueprints/export
        for that single expansion ID. Otherwise fall back to the slower per-name
        search across the game's expansions, and persist the discovered ID for
        next time."""
        game_id = GAME_IDS.get(game, 5)
        cache_key = f"{game}:{name.lower()}:{(set_name or '').lower()}:{expansion_code or ''}"
        if cache_key in self._blueprint_cache:
            return self._blueprint_cache[cache_key]

        name_lower = name.lower()
        registry = get_registry()
        target_exp_record = registry.by_code(expansion_code) if expansion_code else None

        # Fast path: cached CardTrader ID → single targeted request
        if target_exp_record and target_exp_record.cardtrader_id:
            bp = await self._find_blueprint_in_expansion(
                target_exp_record.cardtrader_id,
                name_lower,
                target_exp_record.name_en,
            )
            if bp:
                self._blueprint_cache[cache_key] = bp
                return bp

        # Slow path: name-based search across the game's expansion catalog
        expansions = await self._get_expansions(game_id)
        if set_name:
            set_lower = set_name.lower()
            expansions = [e for e in expansions if set_lower in e.get("name", "").lower()]

        for exp in expansions[:5]:
            bp = await self._find_blueprint_in_expansion(
                exp["id"], name_lower, exp.get("name", ""),
            )
            if bp:
                # Persist the CardTrader expansion ID against our canonical code.
                # We prefer the user-supplied `expansion_code`; otherwise try to
                # reverse-match the CardTrader expansion name against the registry.
                code_to_persist = expansion_code
                if not code_to_persist:
                    match = registry.find(exp.get("name", ""), game=game)
                    if match and match.score >= 90:
                        code_to_persist = match.expansion.code
                if code_to_persist:
                    try:
                        await registry.record_external_code(
                            code_to_persist, "cardtrader_id", exp["id"],
                        )
                    except Exception as e:
                        logger.warning(f"Failed to persist cardtrader_id for {code_to_persist}: {e}")
                self._blueprint_cache[cache_key] = bp
                return bp

        self._blueprint_cache[cache_key] = None
        return None

    async def _find_blueprint_in_expansion(
        self, expansion_id: int, name_lower: str, expansion_name: str,
    ) -> dict | None:
        """Query blueprints for a single CardTrader expansion ID."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{API_BASE}/blueprints/export",
                                params={"expansion_id": expansion_id},
                                headers=self._headers())
                if r.status_code != 200:
                    return None
                data = r.json()
                bps = data if isinstance(data, list) else data.get("array", [])
                for bp in bps:
                    bp_name = (bp.get("name", "") + " " + bp.get("name_en", "")).lower()
                    if name_lower in bp_name:
                        bp["expansion_name"] = expansion_name
                        return bp
        except Exception as e:
            logger.debug(f"CardTrader blueprint lookup failed (exp {expansion_id}): {e}")
        return None

    async def get_prices(self, name: str, game: str = "pokemon",
                         set_name: str | None = None,
                         expansion_code: str | None = None) -> CardTraderPrices | None:
        """Get marketplace prices for a card.

        Pass `expansion_code` (TCG-API style: `ex1`, `sv3pt5`, ...) when known
        — the registry uses it to skip the expansion-list lookup."""
        if not self.is_configured:
            return None

        bp = await self._find_blueprint(name, game, set_name, expansion_code=expansion_code)
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
