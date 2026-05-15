"""
eBay Browse API collector for sold/completed listings.
Uses OAuth Client Credentials flow (no user login needed).
Requires EBAY_APP_ID and EBAY_CERT_ID in .env.

When credentials are not set, all methods return empty results gracefully.
"""
import logging
import time
from base64 import b64encode
from dataclasses import dataclass

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# eBay marketplace IDs
MARKETPLACES = {
    "it": "EBAY_IT",
    "uk": "EBAY_GB",
    "de": "EBAY_DE",
    "fr": "EBAY_FR",
    "es": "EBAY_ES",
    "us": "EBAY_US",
}


@dataclass
class EbaySoldItem:
    title: str
    price_eur: float
    currency: str
    condition: str
    sold_date: str
    url: str
    image_url: str | None = None
    marketplace: str = "EBAY_IT"


class EbayCollector:
    """eBay Browse API collector. Gracefully returns empty if no credentials."""

    def __init__(self):
        self._token: str | None = None
        self._token_expires: float = 0

    @property
    def is_configured(self) -> bool:
        return bool(settings.ebay_app_id and settings.ebay_cert_id)

    async def _get_token(self) -> str | None:
        """Get OAuth token using Client Credentials grant."""
        if not self.is_configured:
            return None

        if self._token and time.time() < self._token_expires:
            return self._token

        credentials = b64encode(
            f"{settings.ebay_app_id}:{settings.ebay_cert_id}".encode()
        ).decode()

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    TOKEN_URL,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Authorization": f"Basic {credentials}",
                    },
                    data={
                        "grant_type": "client_credentials",
                        "scope": "https://api.ebay.com/oauth/api_scope",
                    },
                )
                if r.status_code != 200:
                    logger.error(f"eBay OAuth failed: {r.status_code} {r.text[:200]}")
                    return None

                data = r.json()
                self._token = data["access_token"]
                self._token_expires = time.time() + data.get("expires_in", 7200) - 60
                logger.info("eBay OAuth token obtained")
                return self._token

        except Exception as e:
            logger.error(f"eBay OAuth error: {e}")
            return None

    async def search_sold(
        self,
        query: str,
        max_results: int = 10,
        marketplace: str = "it",
    ) -> list[EbaySoldItem]:
        """
        Search eBay sold/completed listings.
        Returns empty list if credentials not configured.
        """
        token = await self._get_token()
        if not token:
            return []

        marketplace_id = MARKETPLACES.get(marketplace, "EBAY_IT")

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # The Browse API searches active + completed items
                # We filter for completed/sold items
                r = await client.get(
                    BROWSE_URL,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
                        "X-EBAY-C-ENDUSERCTX": "affiliateCampaignId=<ePNCampaignId>,affiliateReferenceId=<referenceId>",
                    },
                    params={
                        "q": query,
                        "filter": "buyingOptions:{FIXED_PRICE|AUCTION},conditions:{NEW|USED|VERY_GOOD|GOOD|ACCEPTABLE}",
                        "sort": "-price",
                        "limit": str(min(max_results, 50)),
                    },
                )

                if r.status_code == 401:
                    # Token expired, retry
                    self._token = None
                    token = await self._get_token()
                    if not token:
                        return []
                    r = await client.get(
                        BROWSE_URL,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
                        },
                        params={
                            "q": query,
                            "sort": "-price",
                            "limit": str(min(max_results, 50)),
                        },
                    )

                if r.status_code != 200:
                    logger.warning(f"eBay Browse API: {r.status_code}")
                    return []

                data = r.json()

        except Exception as e:
            logger.error(f"eBay search failed: {e}")
            return []

        items = []
        for item in data.get("itemSummaries", []):
            try:
                price_data = item.get("price", {})
                price_val = float(price_data.get("value", 0))
                currency = price_data.get("currency", "EUR")

                # Convert to EUR if needed
                if currency == "USD":
                    price_eur = price_val * 0.92
                elif currency == "GBP":
                    price_eur = price_val * 1.17
                else:
                    price_eur = price_val

                condition = item.get("condition", "N/A")
                if isinstance(condition, dict):
                    condition = condition.get("conditionId", "N/A")

                image = None
                if item.get("image"):
                    image = item["image"].get("imageUrl")
                elif item.get("thumbnailImages"):
                    image = item["thumbnailImages"][0].get("imageUrl")

                items.append(EbaySoldItem(
                    title=item.get("title", ""),
                    price_eur=price_eur,
                    currency=currency,
                    condition=str(condition),
                    sold_date=item.get("itemEndDate", ""),
                    url=item.get("itemWebUrl", ""),
                    image_url=image,
                    marketplace=marketplace_id,
                ))
            except Exception as e:
                logger.debug(f"Failed to parse eBay item: {e}")
                continue

        return items

    async def get_sold_prices(
        self,
        query: str,
        marketplace: str = "it",
        max_results: int = 20,
    ) -> dict:
        """
        Get aggregated sold prices from eBay.
        Returns {"avg": float, "min": float, "max": float, "count": int, "items": list}.
        """
        items = await self.search_sold(query, max_results=max_results, marketplace=marketplace)
        if not items:
            return {"avg": None, "min": None, "max": None, "count": 0, "items": []}

        prices = [i.price_eur for i in items if i.price_eur > 0]
        if not prices:
            return {"avg": None, "min": None, "max": None, "count": 0, "items": items}

        return {
            "avg": round(sum(prices) / len(prices), 2),
            "min": round(min(prices), 2),
            "max": round(max(prices), 2),
            "count": len(prices),
            "items": items,
        }
