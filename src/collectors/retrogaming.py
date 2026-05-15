"""
RetroGamingShop.it scraper — Italian retrogaming store with real EUR prices.
BackInGame.fr — French retrogaming store (link only, no scraping).
"""
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

RGS_BASE = "https://www.retrogamingshop.it"
BIG_BASE = "https://backingame.fr"


@dataclass
class RetroGameListing:
    name: str
    price_eur: float
    url: str
    source: str  # "retrogamingshop" or "backingame"


async def search_retrogamingshop(query: str, max_results: int = 10) -> list[RetroGameListing]:
    """Search RetroGamingShop.it (Shopify) for products."""
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True, timeout=15,
        ) as client:
            r = await client.get(f"{RGS_BASE}/search", params={"q": query})
            if r.status_code != 200:
                return []

            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".card-wrapper")

            results = []
            for card in cards[:max_results]:
                name_el = card.select_one("a")
                price_el = card.select_one(".price-item--sale, .price-item--regular")

                if not name_el:
                    continue

                name = name_el.get_text(strip=True)
                href = name_el.get("href", "")
                if not name or len(name) < 3:
                    continue

                url = href if href.startswith("http") else f"{RGS_BASE}{href}"

                price = 0.0
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    match = re.search(r'€([\d.,]+)', price_text)
                    if match:
                        price = float(match.group(1).replace(".", "").replace(",", "."))

                if price > 0:
                    results.append(RetroGameListing(
                        name=name, price_eur=price, url=url, source="retrogamingshop",
                    ))

            return results

    except Exception as e:
        logger.error(f"RetroGamingShop search failed: {e}")
        return []


def retrogamingshop_search_url(query: str) -> str:
    return f"{RGS_BASE}/search?q={quote_plus(query)}"


def backingame_search_url(query: str) -> str:
    return f"{BIG_BASE}/?s={quote_plus(query)}&post_type=product"
