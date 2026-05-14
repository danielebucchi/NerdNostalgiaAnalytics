import logging
import re

from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, ProductResult, PriceHistoryResult, PricePoint
from src.db.models import ProductCategory

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cardmarket.com"


def _detect_category(text: str) -> ProductCategory:
    lower = text.lower()
    if "pokemon" in lower or "pokémon" in lower:
        return ProductCategory.POKEMON
    if "magic" in lower or "mtg" in lower:
        return ProductCategory.MAGIC
    if "yu-gi-oh" in lower or "yugioh" in lower:
        return ProductCategory.YUGIOH
    return ProductCategory.OTHER


class CardmarketCollector(BaseCollector):
    source_name = "cardmarket"

    async def search(self, query: str, max_results: int = 20) -> list[ProductResult]:
        """
        Search Cardmarket for products.
        Note: Cardmarket has anti-bot protections, so this may need adjustment.
        """
        url = f"{BASE_URL}/en/Pokemon/Products/Search"
        params = {"searchString": query}

        try:
            response = await self._rate_limited_get(url, params=params)
            if response.status_code == 403:
                logger.warning("Cardmarket returned 403 - anti-bot protection active")
                return []
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Cardmarket search failed for '{query}': {e}")
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        # Cardmarket product table
        rows = soup.select(".table-body .row, table.table tbody tr")

        for row in rows[:max_results]:
            try:
                result = self._parse_search_row(row)
                if result:
                    results.append(result)
            except Exception as e:
                logger.debug(f"Failed to parse Cardmarket row: {e}")
                continue

        return results

    def _parse_search_row(self, row) -> ProductResult | None:
        link = row.select_one("a.name, a[href*='/Products/']")
        if not link:
            return None

        name = link.get_text(strip=True)
        href = link.get("href", "")
        if not name or not href:
            return None

        product_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        external_id = href.strip("/")

        # Get price (trend price or lowest)
        price_el = row.select_one(".price-container .text-end, .col-price .text-end")
        current_price = None
        if price_el:
            price_text = price_el.get_text(strip=True)
            price_match = re.search(r'([\d,.]+)\s*[€$]', price_text)
            if price_match:
                price_str = price_match.group(1).replace(".", "").replace(",", ".")
                current_price = float(price_str)

        # Get set/expansion
        set_el = row.select_one(".expansion-name, .col-expansion")
        set_name = set_el.get_text(strip=True) if set_el else None

        category = _detect_category(f"{name} {product_url}")

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
        """
        Cardmarket shows price history charts via JavaScript/API.
        The chart data is loaded dynamically, so we try to extract from the page.
        """
        result = PriceHistoryResult(external_id=external_id, source=self.source_name)

        url = f"{BASE_URL}/{external_id}"
        try:
            response = await self._rate_limited_get(url)
            if response.status_code == 403:
                logger.warning("Cardmarket 403 on price history")
                return result
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Cardmarket price history failed for '{external_id}': {e}")
            return result

        # Cardmarket embeds chart data in JavaScript
        # Look for chart data patterns
        import json
        text = response.text

        # Pattern: var defined_chart_data = [{...}, ...]
        match = re.search(r'var\s+(?:chart_data|priceGuideData)\s*=\s*(\[.*?\]);', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                for point in data:
                    if isinstance(point, dict) and "date" in point and "price" in point:
                        from datetime import datetime
                        dt = datetime.strptime(point["date"], "%Y-%m-%d")
                        result.prices.append(PricePoint(date=dt, price=float(point["price"])))
            except (json.JSONDecodeError, ValueError, KeyError):
                pass

        return result

    async def search_all(self, query: str, max_pages: int = 3) -> list[ProductResult]:
        """Search multiple pages."""
        all_results = []
        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}/en/Pokemon/Products/Search"
            params = {"searchString": query, "page": str(page)}
            try:
                response = await self._rate_limited_get(url, params=params)
                if response.status_code == 403:
                    break
                response.raise_for_status()
            except Exception:
                break

            soup = BeautifulSoup(response.text, "lxml")
            rows = soup.select(".table-body .row, table.table tbody tr")
            if not rows:
                break

            for row in rows:
                try:
                    result = self._parse_search_row(row)
                    if result:
                        all_results.append(result)
                except Exception:
                    continue

            if len(rows) < 20:
                break

        return all_results
