from src.collectors.subito_wallapop import (
    subito_search_url, wallapop_search_url, mercatino_search_url,
)


class TestSearchUrls:
    def test_subito_url(self):
        url = subito_search_url("metroid fusion")
        assert "subito.it" in url
        assert "metroid" in url

    def test_subito_with_price(self):
        url = subito_search_url("metroid fusion", price_max=50.0)
        assert "pe=50" in url

    def test_wallapop_url(self):
        url = wallapop_search_url("zelda ocarina")
        assert "wallapop.com" in url
        assert "zelda" in url

    def test_wallapop_with_price(self):
        url = wallapop_search_url("zelda", price_max=100.0)
        assert "max_sale_price=100" in url

    def test_mercatino_url(self):
        url = mercatino_search_url("pokemon")
        assert "mercatinousato.com" in url
