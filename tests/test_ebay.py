import pytest
from src.collectors.ebay import EbayCollector


class TestEbayCollector:
    def test_not_configured_by_default(self):
        collector = EbayCollector()
        assert not collector.is_configured

    @pytest.mark.asyncio
    async def test_search_returns_empty_without_credentials(self):
        collector = EbayCollector()
        results = await collector.search_sold("metroid fusion")
        assert results == []

    @pytest.mark.asyncio
    async def test_get_sold_prices_empty(self):
        collector = EbayCollector()
        data = await collector.get_sold_prices("test")
        assert data["avg"] is None
        assert data["count"] == 0
        assert data["items"] == []
