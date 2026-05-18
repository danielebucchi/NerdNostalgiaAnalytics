"""Tests for the PriceCharting scraper."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

from src.scraper import search, get_current_price


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_parses_results(self):
        """Test that search returns structured results from HTML."""
        mock_html = """
        <table id="games_table"><tbody>
            <tr>
                <td class="title"><a href="/game/pokemon-base-set/charizard-4">Charizard #4</a></td>
                <td class="price">$435.46</td>
            </tr>
            <tr>
                <td class="title"><a href="/game/pokemon-base-set/mewtwo-10">Mewtwo #10</a></td>
                <td class="price">$78.00</td>
            </tr>
        </tbody></table>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()

        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            results = await search("charizard")
            assert len(results) == 2
            assert results[0]["name"] == "Charizard #4"
            assert results[0]["price"] == 435.46
            assert "charizard-4" in results[0]["external_id"]
            assert results[1]["name"] == "Mewtwo #10"

    @pytest.mark.asyncio
    async def test_search_empty(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<table id='games_table'><tbody></tbody></table>"
        mock_response.raise_for_status = MagicMock()

        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            results = await search("nonexistent")
            assert results == []

    @pytest.mark.asyncio
    async def test_search_error(self):
        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("Network error"))
            mock_client.return_value = client

            results = await search("test")
            assert results == []


class TestGetCurrentPrice:
    @pytest.mark.asyncio
    async def test_parses_chart_data(self):
        """Test cents-to-dollars conversion from VGPC.chart_data."""
        chart_data = {"used": [[1609459200000, 43546], [1612137600000, 50000]]}
        mock_html = f'VGPC.chart_data = {json.dumps(chart_data)};'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()

        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            price = await get_current_price("pokemon-base-set/charizard-4")
            assert price == 500.00  # 50000 cents = $500.00

    @pytest.mark.asyncio
    async def test_no_chart_data(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>no chart data</html>"
        mock_response.raise_for_status = MagicMock()

        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            price = await get_current_price("test")
            assert price is None

    @pytest.mark.asyncio
    async def test_error(self):
        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("fail"))
            mock_client.return_value = client

            price = await get_current_price("test")
            assert price is None

    @pytest.mark.asyncio
    async def test_fallback_conditions(self):
        """If 'used' not available, falls back to 'cib', 'new', etc."""
        chart_data = {"cib": [[1609459200000, 15000]]}
        mock_html = f'VGPC.chart_data = {json.dumps(chart_data)};'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()

        with patch("src.scraper._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            price = await get_current_price("test")
            assert price == 150.00
