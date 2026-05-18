"""
Minimal PriceCharting scraper — search products and get current prices.
"""
import asyncio
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
BASE_URL = "https://www.pricecharting.com"
_client = None


async def _get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
    return _client


async def search(query: str, max_results: int = 10) -> list[dict]:
    """Search PriceCharting. Returns list of {name, external_id, price, url, category}."""
    client = await _get_client()
    try:
        r = await client.get(f"{BASE_URL}/search-products", params={"q": query, "type": "prices"})
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    rows = soup.select("table#games_table tbody tr")
    results = []

    for row in rows[:max_results]:
        link = row.select_one("td.title a") or row.select_one("a")
        if not link:
            continue
        name = link.get_text(strip=True)
        href = link.get("href", "")
        if not name or not href:
            continue

        product_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        clean_href = href.replace(BASE_URL, "").strip("/")
        external_id = clean_href.replace("game/", "")

        price_cell = row.select_one("td.price") or row.select_one("td.used_price")
        price = None
        if price_cell:
            match = re.search(r'[\$€£]?([\d,]+\.?\d*)', price_cell.get_text(strip=True))
            if match:
                price = float(match.group(1).replace(",", ""))

        results.append({
            "name": name, "external_id": external_id,
            "price": price, "url": product_url,
        })

    return results


async def get_current_price(external_id: str) -> float | None:
    """Get current loose/used price for a product."""
    client = await _get_client()
    try:
        r = await client.get(f"{BASE_URL}/game/{external_id}")
        r.raise_for_status()
    except Exception:
        return None

    match = re.search(r'VGPC\.chart_data\s*=\s*(\{.*?\});', r.text, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    for key in ("loose", "used", "cib", "new"):
        if key in data and data[key]:
            prices = [p[1] for p in data[key] if isinstance(p, list) and len(p) >= 2 and p[1] > 0]
            if prices:
                return prices[-1] / 100.0  # Cents to dollars

    return None
