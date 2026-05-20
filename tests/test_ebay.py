import pytest
from unittest.mock import patch
from src.collectors.ebay import EbayCollector


class TestEbayCollector:
    def test_not_configured_without_creds(self):
        with patch("src.collectors.ebay.settings") as mock_settings:
            mock_settings.ebay_app_id = ""
            mock_settings.ebay_cert_id = ""
            collector = EbayCollector()
            assert not collector.is_configured

    def test_configured_with_creds(self):
        with patch("src.collectors.ebay.settings") as mock_settings:
            mock_settings.ebay_app_id = "test-id"
            mock_settings.ebay_cert_id = "test-secret"
            collector = EbayCollector()
            assert collector.is_configured

    @pytest.mark.asyncio
    async def test_search_returns_empty_without_credentials(self):
        with patch("src.collectors.ebay.settings") as mock_settings:
            mock_settings.ebay_app_id = ""
            mock_settings.ebay_cert_id = ""
            collector = EbayCollector()
            results = await collector.search_sold("metroid fusion")
            assert results == []

    @pytest.mark.asyncio
    async def test_get_sold_prices_empty_without_credentials(self):
        with patch("src.collectors.ebay.settings") as mock_settings:
            mock_settings.ebay_app_id = ""
            mock_settings.ebay_cert_id = ""
            collector = EbayCollector()
            data = await collector.get_sold_prices("test")
            assert data["avg"] is None
            assert data["count"] == 0
