from src.utils.buy_links import get_buy_links
from src.db.models import ProductCategory


class TestBuyLinks:
    def test_pokemon_card_has_cardmarket(self):
        links = get_buy_links("Charizard", ProductCategory.POKEMON)
        assert "Cardmarket" in links
        assert "Vinted" in links
        assert "eBay" in links
        assert "Subito" in links
        assert "Wallapop" in links

    def test_videogame_no_cardmarket(self):
        links = get_buy_links("Super Mario 64", ProductCategory.VIDEOGAME)
        assert "Cardmarket" not in links
        assert "Vinted" in links
        assert "eBay" in links

    def test_pricecharting_link(self):
        links = get_buy_links("Test", "other", product_url="https://pricecharting.com/game/test")
        assert "PriceCharting" in links

    def test_no_pricecharting_without_url(self):
        links = get_buy_links("Test", "other")
        assert "PriceCharting" not in links

    def test_magic_has_cardmarket(self):
        links = get_buy_links("Black Lotus", ProductCategory.MAGIC)
        assert "MagicTheGathering" in links
