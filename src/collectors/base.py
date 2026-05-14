import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ProductResult:
    external_id: str
    source: str
    name: str
    category: str = "other"
    set_name: str | None = None
    console_or_platform: str | None = None
    image_url: str | None = None
    product_url: str | None = None
    current_price: float | None = None


@dataclass
class PricePoint:
    date: datetime
    price: float
    volume: int | None = None


@dataclass
class PriceHistoryResult:
    external_id: str
    source: str
    prices: list[PricePoint] = field(default_factory=list)


class BaseCollector(ABC):
    source_name: str = "unknown"

    def __init__(self):
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/131.0.0.0 Safari/537.36"
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _rate_limited_get(self, url: str, **kwargs) -> httpx.Response:
        from src.utils.rate_limiter import rate_limiter

        async with self._semaphore:
            await rate_limiter.wait(url)
            client = await self.get_client()
            try:
                response = await client.get(url, **kwargs)
                if response.status_code in (429, 403):
                    rate_limiter.report_error(url, response.status_code)
                else:
                    rate_limiter.report_success(url)
                return response
            except Exception as e:
                rate_limiter.report_error(url)
                raise

    @abstractmethod
    async def search(self, query: str) -> list[ProductResult]:
        ...

    @abstractmethod
    async def get_price_history(self, external_id: str) -> PriceHistoryResult:
        ...
