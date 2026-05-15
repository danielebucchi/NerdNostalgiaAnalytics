import pytest
from unittest.mock import AsyncMock, patch

from src.collectors.twentysixbits import search_26bits, get_26bits_price, _catalog_cache


MOCK_CATALOG = [
    {
        "id": "metroid-fusion", "name": "METROID FUSION", "slug": "metroid-fusion",
        "platform": "GAME BOY ADVANCE", "category": "GIOCHI", "condition": "Usato testato",
        "availability": "available", "availabilityLabel": "Disponibile",
        "price": 45, "originalPrice": 45, "onSale": False, "description": "Cartuccia GBA",
    },
    {
        "id": "zelda-oot", "name": "ZELDA OCARINA OF TIME", "slug": "zelda-oot",
        "platform": "NINTENDO 64", "category": "GIOCHI", "condition": "Usato testato",
        "availability": "available", "availabilityLabel": "Disponibile",
        "price": 60, "originalPrice": 60, "onSale": False, "description": "Cartuccia N64",
    },
    {
        "id": "sold-out", "name": "POKEMON GOLD", "slug": "pokemon-gold",
        "platform": "GAME BOY COLOR", "category": "GIOCHI", "condition": "Usato testato",
        "availability": "sold", "availabilityLabel": "Venduto",
        "price": 30, "originalPrice": 30, "onSale": False, "description": "",
    },
]


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear catalog cache between tests."""
    import src.collectors.twentysixbits as mod
    mod._catalog_cache = []
    mod._cache_time = 0
    yield
    mod._catalog_cache = []
    mod._cache_time = 0


class TestSearch26bits:
    @pytest.mark.asyncio
    async def test_search_finds_product(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=MOCK_CATALOG):
            results = await search_26bits("metroid fusion")
            assert len(results) == 1
            assert results[0].name == "METROID FUSION"
            assert results[0].price_eur == 45.0
            assert results[0].platform == "GAME BOY ADVANCE"

    @pytest.mark.asyncio
    async def test_search_excludes_sold(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=MOCK_CATALOG):
            results = await search_26bits("pokemon gold")
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_by_platform(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=MOCK_CATALOG):
            results = await search_26bits("game boy")
            assert len(results) == 1
            assert "GAME BOY" in results[0].platform

    @pytest.mark.asyncio
    async def test_search_no_match(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=MOCK_CATALOG):
            results = await search_26bits("crash bandicoot")
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_empty_catalog(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=[]):
            results = await search_26bits("metroid")
            assert len(results) == 0


class TestGetPrice:
    @pytest.mark.asyncio
    async def test_get_price(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=MOCK_CATALOG):
            price = await get_26bits_price("metroid fusion")
            assert price == 45.0

    @pytest.mark.asyncio
    async def test_get_price_not_found(self):
        with patch("src.collectors.twentysixbits._get_catalog", return_value=MOCK_CATALOG):
            price = await get_26bits_price("kirby")
            assert price is None
