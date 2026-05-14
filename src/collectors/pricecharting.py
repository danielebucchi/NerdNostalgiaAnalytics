import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from src.collectors.base import (
    BaseCollector, ProductResult, PriceHistoryResult, PricePoint,
)
from src.db.models import ProductCategory

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pricecharting.com"

CATEGORY_MAP = {
    "pokemon": ProductCategory.POKEMON,
    "magic-the-gathering": ProductCategory.MAGIC,
    "yu-gi-oh": ProductCategory.YUGIOH,
    "yugioh": ProductCategory.YUGIOH,
}


CONSOLE_KEYWORDS = [
    "nes", "snes", "super-nintendo", "nintendo-64", "nintendo-ds", "nintendo-3ds",
    "nintendo-switch", "gameboy", "game-boy", "gamecube", "wii", "wii-u",
    "playstation", "ps1", "ps2", "ps3", "ps4", "ps5", "psp", "ps-vita",
    "xbox", "sega", "genesis", "mega-drive", "dreamcast", "saturn", "game-gear",
    "atari", "turbografx", "neo-geo", "commodore", "intellivision", "colecovision",
    "3do", "jaguar", "virtual-boy", "master-system",
]

# TCG set patterns in URLs (cards, not video games)
TCG_URL_PATTERNS = [
    "pokemon-base", "pokemon-jungle", "pokemon-fossil", "pokemon-team-rocket",
    "pokemon-gym", "pokemon-neo", "pokemon-expedition", "pokemon-aquapolis",
    "pokemon-skyridge", "pokemon-ruby-sapphire", "pokemon-sandstorm",
    "pokemon-dragon", "pokemon-hidden-legends", "pokemon-fire-red-leaf-green",
    "pokemon-ex", "pokemon-diamond-pearl", "pokemon-black-white",
    "pokemon-xy", "pokemon-sun-moon", "pokemon-sword-shield",
    "pokemon-scarlet-violet", "pokemon-promo", "pokemon-151",
    "pokemon-paldea", "pokemon-obsidian", "pokemon-temporal",
    "pokemon-astral", "pokemon-brilliant", "pokemon-fusion",
    "pokemon-evolving", "pokemon-chilling", "pokemon-battle",
    "pokemon-vivid", "pokemon-darkness", "pokemon-rebel",
    "pokemon-cosmic", "pokemon-unified", "pokemon-unbroken",
    "pokemon-lost", "pokemon-celestial", "pokemon-forbidden",
    "pokemon-ultra", "pokemon-crimson", "pokemon-burning",
    "pokemon-guardians", "pokemon-steam", "pokemon-ancient",
    "pokemon-roaring", "pokemon-breakthrough", "pokemon-breakpoint",
    "pokemon-fates", "pokemon-generations", "pokemon-evolutions",
    "pokemon-phantom", "pokemon-primal", "pokemon-legendary",
    "pokemon-boundaries", "pokemon-plasma", "pokemon-next",
    "pokemon-dark-explorers", "pokemon-noble", "pokemon-emerging",
    "pokemon-triumphant", "pokemon-undaunted", "pokemon-unleashed",
    "pokemon-heartgold", "pokemon-arceus", "pokemon-supreme",
    "pokemon-rising", "pokemon-stormfront", "pokemon-legends",
    "pokemon-majestic", "pokemon-secret", "pokemon-great",
    "pokemon-mysterious", "pokemon-crystal", "pokemon-deoxys",
    "pokemon-unseen", "pokemon-holon", "pokemon-power",
    "pokemon-japanese", "pokemon-topps", "pokemon-surging",
    "pokemon-shrouded", "pokemon-twilight", "pokemon-stellar",
    "pokemon-paradox", "pokemon-paldean", "pokemon-obsidian",
    "pokemon-phantasmal",
]


def _detect_category(text: str) -> ProductCategory:
    """
    Detect category using URL patterns for accuracy.
    PriceCharting URLs clearly separate cards (/pokemon-base-set/) from games (/gameboy-advance/).
    """
    lower = text.lower()

    # 1. Check URL for console keywords first (most reliable for video games)
    for console in CONSOLE_KEYWORDS:
        if console in lower:
            return ProductCategory.VIDEOGAME

    # 2. Check for TCG card patterns in URL
    for pattern in TCG_URL_PATTERNS:
        if pattern in lower:
            return ProductCategory.POKEMON

    # 3. Check for card-specific indicators (# number = card number)
    if re.search(r'#\d+', text):
        # Has a card number - likely a trading card
        if "pokemon" in lower or "pokémon" in lower:
            return ProductCategory.POKEMON
        if "magic" in lower or "mtg" in lower:
            return ProductCategory.MAGIC
        if "yu-gi-oh" in lower or "yugioh" in lower:
            return ProductCategory.YUGIOH

    # 4. Fallback keyword checks
    if "magic" in lower or "mtg" in lower:
        return ProductCategory.MAGIC
    if "yu-gi-oh" in lower or "yugioh" in lower:
        return ProductCategory.YUGIOH
    if "pokemon" in lower or "pokémon" in lower:
        # If we get here without matching a console or TCG set, default to pokemon card
        return ProductCategory.POKEMON

    return ProductCategory.OTHER


class PriceChartingCollector(BaseCollector):
    source_name = "pricecharting"

    async def search(self, query: str, max_results: int = 20) -> list[ProductResult]:
        return await self._search_paginated(query, max_results=max_results)

    async def search_all(self, query: str, max_pages: int = 5) -> list[ProductResult]:
        """Search all pages for a query. Returns all matching products."""
        return await self._search_paginated(query, max_results=None, max_pages=max_pages)

    async def _search_paginated(
        self, query: str, max_results: int | None = 20, max_pages: int = 5,
    ) -> list[ProductResult]:
        url = f"{BASE_URL}/search-products"
        results = []

        for page in range(1, max_pages + 1):
            params = {"q": query, "type": "prices", "page": str(page)}
            try:
                response = await self._rate_limited_get(url, params=params)
                response.raise_for_status()
            except Exception as e:
                logger.error(f"Search failed for '{query}' page {page}: {e}")
                break

            soup = BeautifulSoup(response.text, "lxml")
            rows = soup.select("table#games_table tbody tr")
            if not rows:
                rows = soup.select(".offer")

            if not rows:
                break  # No more results

            for row in rows:
                try:
                    result = self._parse_search_row(row)
                    if result:
                        results.append(result)
                        if max_results and len(results) >= max_results:
                            return results
                except Exception as e:
                    logger.debug(f"Failed to parse row: {e}")
                    continue

            # If we got fewer than 100 rows, there are no more pages
            if len(rows) < 100:
                break

        return results

    def _parse_search_row(self, row) -> ProductResult | None:
        # Try to find the product link
        link = row.select_one("td.title a") or row.select_one("a.product_name") or row.select_one("a")
        if not link:
            return None

        name = link.get_text(strip=True)
        href = link.get("href", "")
        if not name or not href:
            return None

        product_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        # Extract ID from URL, handling both full URLs and relative paths
        # e.g. "https://www.pricecharting.com/pokemon-base-set/charizard-4" -> "pokemon-base-set/charizard-4"
        # e.g. "/game/pokemon-base-set/charizard-4" -> "pokemon-base-set/charizard-4"
        clean_href = href.replace(BASE_URL, "").strip("/")
        external_id = clean_href.replace("game/", "")

        # Try to get the set/console name
        set_cell = row.select_one("td.console-name") or row.select_one("td:nth-child(2)")
        set_name = set_cell.get_text(strip=True) if set_cell else None

        # Try to get price
        price_cell = row.select_one("td.price") or row.select_one("td.used_price")
        current_price = None
        if price_cell:
            price_text = price_cell.get_text(strip=True)
            price_match = re.search(r'[\$€£]?([\d,]+\.?\d*)', price_text)
            if price_match:
                current_price = float(price_match.group(1).replace(",", ""))

        category = _detect_category(f"{name} {set_name or ''} {product_url}")

        return ProductResult(
            external_id=external_id,
            source=self.source_name,
            name=name,
            category=category.value,
            set_name=set_name,
            product_url=product_url,
            current_price=current_price,
        )

    async def get_price_history(self, external_id: str) -> PriceHistoryResult:
        result = PriceHistoryResult(external_id=external_id, source=self.source_name)

        # PriceCharting embeds chart data as JavaScript in the product page
        url = f"{BASE_URL}/game/{external_id}"
        try:
            response = await self._rate_limited_get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch price history for '{external_id}': {e}")
            return result

        result.prices = self._extract_prices_from_page(response.text)
        return result

    def _extract_prices_from_page(self, html: str) -> list[PricePoint]:
        """
        Extract price history from VGPC.chart_data JSON embedded in the page.
        Format: VGPC.chart_data = {"loose":[[timestamp_ms, price], ...], "cib":[...], ...}
        We prefer "loose" (used price) as it's the most commonly traded condition.
        """
        import json

        # Extract VGPC.chart_data JSON
        match = re.search(r'VGPC\.chart_data\s*=\s*(\{.*?\});', html, re.DOTALL)
        if not match:
            return []

        try:
            chart_data = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.error("Failed to parse VGPC.chart_data JSON")
            return []

        # Priority order: loose (used), cib (complete in box), new, graded
        for key in ("loose", "used", "cib", "complete", "new", "graded", "boxonly"):
            if key in chart_data and chart_data[key]:
                return self._parse_timestamp_array(chart_data[key])

        # If no known key, try the first available
        for key, data in chart_data.items():
            if isinstance(data, list) and data:
                return self._parse_timestamp_array(data)

        return []

    def _parse_timestamp_array(self, data: list) -> list[PricePoint]:
        """Parse [[timestamp_ms, price], ...] arrays."""
        prices = []
        for point in data:
            if isinstance(point, list) and len(point) >= 2:
                try:
                    timestamp_ms = int(point[0])
                    price = float(point[1])
                    if price <= 0:
                        continue
                    dt = datetime.fromtimestamp(timestamp_ms / 1000)
                    prices.append(PricePoint(date=dt, price=price))
                except (ValueError, TypeError, OSError):
                    continue
        return prices

    async def get_all_conditions(self, external_id: str) -> dict[str, list[PricePoint]]:
        """
        Get price history for ALL conditions (used, graded, new, cib, etc.).
        Returns dict: {"used": [PricePoint, ...], "graded": [...], ...}
        """
        import json

        url = f"{BASE_URL}/game/{external_id}"
        try:
            response = await self._rate_limited_get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch conditions for '{external_id}': {e}")
            return {}

        match = re.search(r'VGPC\.chart_data\s*=\s*(\{.*?\});', response.text, re.DOTALL)
        if not match:
            return {}

        try:
            chart_data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

        result = {}
        # Friendly names mapping
        name_map = {
            "used": "Ungraded", "loose": "Ungraded",
            "graded": "Graded (PSA)", "cib": "Complete in Box",
            "new": "New/Sealed", "boxonly": "Box Only",
            "manualonly": "Manual Only",
        }
        for key, data in chart_data.items():
            if isinstance(data, list) and data:
                prices = self._parse_timestamp_array(data)
                if prices:
                    friendly = name_map.get(key, key.title())
                    result[friendly] = prices

        return result

    async def get_product_details(self, external_id: str) -> ProductResult | None:
        url = f"{BASE_URL}/game/{external_id}"
        try:
            response = await self._rate_limited_get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch product details for '{external_id}': {e}")
            return None

        soup = BeautifulSoup(response.text, "lxml")

        title_el = soup.select_one("h1#product_name") or soup.select_one("h1")
        name = title_el.get_text(strip=True) if title_el else external_id

        # Get image
        img_el = soup.select_one("#product_image img") or soup.select_one(".cover img")
        image_url = None
        if img_el:
            image_url = img_el.get("src", "")
            if image_url and not image_url.startswith("http"):
                image_url = f"{BASE_URL}{image_url}"

        # Get current price
        price_el = soup.select_one(".price.js-price") or soup.select_one("#used_price .price")
        current_price = None
        if price_el:
            price_match = re.search(r'[\$€£]?([\d,]+\.?\d*)', price_el.get_text(strip=True))
            if price_match:
                current_price = float(price_match.group(1).replace(",", ""))

        category = _detect_category(f"{name} {url}")

        return ProductResult(
            external_id=external_id,
            source=self.source_name,
            name=name,
            category=category.value,
            image_url=image_url,
            product_url=url,
            current_price=current_price,
        )
