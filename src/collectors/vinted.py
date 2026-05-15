import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx

from src.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# Vinted domains per country
VINTED_DOMAINS = {
    "it": "https://www.vinted.it",
    "fr": "https://www.vinted.fr",
    "de": "https://www.vinted.de",
    "es": "https://www.vinted.es",
    "nl": "https://www.vinted.nl",
    "be": "https://www.vinted.be",
    "pt": "https://www.vinted.pt",
    "pl": "https://www.vinted.pl",
}

# Card name translations for multi-lang search
POKEMON_TRANSLATIONS = {
    "charizard": ["dracaufeu", "glurak", "charizard", "リザードン"],
    "pikachu": ["pikachu", "ピカチュウ"],
    "mewtwo": ["mewtu", "mewtwo", "ミュウツー"],
    "blastoise": ["tortank", "turtok", "blastoise"],
    "venusaur": ["florizarre", "bisaflor", "venusaur"],
    "rayquaza": ["rayquaza", "レックウザ"],
    "lugia": ["lugia", "ルギア"],
    "gengar": ["ectoplasma", "gengar", "ゲンガー"],
    "umbreon": ["noctali", "nachtara", "umbreon"],
    "espeon": ["mentali", "psiana", "espeon"],
    "gyarados": ["léviator", "garados", "gyarados"],
    "dragonite": ["dracolosse", "dragoran", "dragonite"],
    "mew": ["mew", "ミュウ"],
    "eevee": ["évoli", "evoli", "eevee"],
    "snorlax": ["ronflex", "relaxo", "snorlax"],
}

# Anti-fake: minimum credible prices by category
MIN_CREDIBLE_PRICES = {
    "pokemon_card": 0.50,
    "videogame": 2.00,
    "graded": 10.00,
}


@dataclass
class VintedListing:
    title: str
    price_eur: float
    url: str
    image_url: str | None
    seller: str
    size: str | None
    country: str = "it"


class VintedCollector(BaseCollector):
    source_name = "vinted"
    _sessions: dict[str, bool] = {}

    async def _ensure_session(self, domain: str):
        if self._sessions.get(domain):
            return
        client = await self.get_client()
        try:
            await client.get(domain, headers={"Accept": "text/html"})
            self._sessions[domain] = True
        except Exception as e:
            logger.warning(f"Failed to init session for {domain}: {e}")

    async def _vinted_get(self, domain: str, query: str, params: dict) -> httpx.Response:
        await self._ensure_session(domain)
        client = await self.get_client()
        api_url = f"{domain}/api/v2/catalog/items"
        return await client.get(api_url, params=params, headers={
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{domain}/catalog?search_text={quote_plus(query)}",
        })

    async def search_listings(
        self, query: str, max_results: int = 20, order: str = "price_low_to_high",
        country: str = "it",
    ) -> list[VintedListing]:
        domain = VINTED_DOMAINS.get(country, VINTED_DOMAINS["it"])
        params = {
            "search_text": query,
            "per_page": str(min(max_results, 96)),
            "order": order,
        }

        try:
            response = await self._vinted_get(domain, query, params)
            if response.status_code != 200:
                self._sessions[domain] = False
                await self._ensure_session(domain)
                response = await self._vinted_get(domain, query, params)
                if response.status_code != 200:
                    return []
            data = response.json()
        except Exception as e:
            logger.error(f"Vinted {country} search failed for '{query}': {e}")
            return []

        return self._parse_items(data.get("items", [])[:max_results], country)

    async def search_multi_country(
        self, query: str, countries: list[str], max_per_country: int = 20,
        order: str = "price_low_to_high",
    ) -> list[VintedListing]:
        """Search across multiple Vinted countries."""
        all_listings = []
        for country in countries:
            # Get translated query for this country
            translated = self._translate_query(query, country)
            listings = await self.search_listings(
                translated, max_results=max_per_country, order=order, country=country,
            )
            all_listings.extend(listings)

        # Sort by price
        if "low" in order:
            all_listings.sort(key=lambda l: l.price_eur)
        elif "high" in order:
            all_listings.sort(key=lambda l: l.price_eur, reverse=True)

        return all_listings

    def _translate_query(self, query: str, country: str) -> str:
        """Translate Pokemon names for local Vinted search."""
        if country == "it":
            return query  # No translation needed

        query_lower = query.lower()
        lang_index = {"fr": 0, "de": 1, "es": 2}.get(country)
        if lang_index is None:
            return query

        for english, translations in POKEMON_TRANSLATIONS.items():
            if english in query_lower and lang_index < len(translations):
                return query_lower.replace(english, translations[lang_index])

        return query

    def _parse_items(self, items: list, country: str) -> list[VintedListing]:
        listings = []
        for item in items:
            try:
                price_data = item.get("price", {})
                price = float(price_data.get("amount", 0))
                if price <= 0:
                    continue

                photos = item.get("photos", []) or item.get("photo", {})
                image_url = None
                if isinstance(photos, list) and photos:
                    image_url = photos[0].get("url") or photos[0].get("full_size_url")
                elif isinstance(photos, dict):
                    image_url = photos.get("url") or photos.get("full_size_url")

                listings.append(VintedListing(
                    title=item.get("title", ""),
                    price_eur=price,
                    url=item.get("url", ""),
                    image_url=image_url,
                    seller=item.get("user", {}).get("login", ""),
                    size=item.get("size_title"),
                    country=country,
                ))
            except Exception:
                continue
        return listings

    @staticmethod
    def _title_matches(title: str, query: str) -> bool:
        title_lower = title.lower()
        query_words = query.lower().split()
        main_keyword = query_words[0]
        # Also check translations
        keywords_to_check = [main_keyword]
        for english, translations in POKEMON_TRANSLATIONS.items():
            if main_keyword == english:
                keywords_to_check.extend(translations)

        if not any(kw in title_lower for kw in keywords_to_check):
            return False
        if len(query_words) > 1:
            matches = sum(1 for w in query_words if w in title_lower)
            return matches >= len(query_words) / 2
        return True

    @staticmethod
    def is_suspicious(listing: VintedListing, min_price: float = 0.50) -> bool:
        """Detect potentially fake/misleading listings.
        Catches: scam/trade posts, catalog listings (€1 placeholder),
        and "don't buy" price-list posts.
        """
        if listing.price_eur < min_price:
            return True

        title_lower = listing.title.lower()

        # Scam / trade / wanted posts
        scam_keywords = ["scambio", "trade", "cerco", "looking for", "échange", "tausch"]
        if any(kw in title_lower for kw in scam_keywords):
            return True

        # Catalog / price list posts ("non comprare", "prezzi in descrizione", etc.)
        # These are fake listings at €1 where the real prices are in the description
        catalog_keywords = [
            "non comprare", "non acquistare", "no acquistare",
            "prezzi in descrizione", "prezzo in descrizione",
            "prezzi in bio", "prezzo in bio",
            "prezzi singoli", "prezzo singolo",
            "chiedi prezzo", "chiedere prezzo", "chiedimi",
            "leggi descrizione", "leggere descrizione",
            "don't buy", "do not buy", "dont buy",
            "price in description", "prices in description",
            "prix en description", "prix dans description",
            "ne pas acheter", "nicht kaufen",
            "lista", "catalogo", "listino",
        ]
        if any(kw in title_lower for kw in catalog_keywords):
            return True

        # €1 listings are almost always catalog/placeholder posts
        if listing.price_eur <= 1.0:
            return True

        return False

    async def find_deals(
        self, query: str, market_price_usd: float, max_results: int = 10,
        countries: list[str] | None = None, min_price: float = 0.50,
    ) -> list[tuple[VintedListing, float]]:
        market_price_eur = market_price_usd * 0.92

        if countries and len(countries) > 1:
            listings = await self.search_multi_country(
                query, countries, max_per_country=30, order="price_low_to_high",
            )
        else:
            listings = await self.search_listings(
                query, max_results=50, order="price_low_to_high",
                country=(countries[0] if countries else "it"),
            )

        deals = []
        for listing in listings:
            if not self._title_matches(listing.title, query):
                continue
            if self.is_suspicious(listing, min_price):
                continue
            if listing.price_eur < market_price_eur:
                discount = ((market_price_eur - listing.price_eur) / market_price_eur) * 100
                deals.append((listing, discount))

        deals.sort(key=lambda x: x[1], reverse=True)
        return deals[:max_results]

    async def search(self, query: str) -> list:
        return []

    async def get_price_history(self, external_id: str):
        from src.collectors.base import PriceHistoryResult
        return PriceHistoryResult(external_id=external_id, source=self.source_name)
