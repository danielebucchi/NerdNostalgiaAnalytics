import pytest
from datetime import datetime

from src.collectors.pricecharting import (
    PriceChartingCollector, _detect_category,
)
from src.collectors.vinted import VintedCollector, POKEMON_TRANSLATIONS
from src.db.models import ProductCategory


class TestCategoryDetection:
    def test_videogame_by_console(self):
        assert _detect_category("gameboy-advance/pokemon-emerald") == ProductCategory.VIDEOGAME
        assert _detect_category("nintendo-64/super-mario-64") == ProductCategory.VIDEOGAME
        assert _detect_category("playstation/crash-bandicoot") == ProductCategory.VIDEOGAME
        assert _detect_category("sega-genesis/sonic") == ProductCategory.VIDEOGAME

    def test_pokemon_card_by_set(self):
        assert _detect_category("pokemon-base-set/charizard-4") == ProductCategory.POKEMON
        assert _detect_category("pokemon-scarlet-violet-151/charizard-ex") == ProductCategory.POKEMON

    def test_card_by_number(self):
        assert _detect_category("pokemon something #123") == ProductCategory.POKEMON

    def test_magic(self):
        assert _detect_category("magic-alpha/black-lotus") == ProductCategory.MAGIC

    def test_yugioh(self):
        assert _detect_category("yu-gi-oh something") == ProductCategory.YUGIOH

    def test_other(self):
        assert _detect_category("random-thing") == ProductCategory.OTHER


class TestPriceChartingCollector:
    def test_parse_timestamp_array(self):
        collector = PriceChartingCollector()
        data = [
            [1609459200000, 10000],  # 2021-01-01, $100.00 (cents)
            [1612137600000, 15000],  # 2021-02-01, $150.00
            [0, -500],              # Invalid, should be skipped
        ]
        prices = collector._parse_timestamp_array(data)
        assert len(prices) == 2
        assert prices[0].price == 100.00  # Converted from cents
        assert prices[1].price == 150.00

    def test_parse_empty_array(self):
        collector = PriceChartingCollector()
        assert collector._parse_timestamp_array([]) == []
        assert collector._parse_timestamp_array([[0, 0]]) == []


class TestVintedCollector:
    def test_title_matches_basic(self):
        assert VintedCollector._title_matches("Charizard Base Set Holo", "charizard")
        assert VintedCollector._title_matches("Charizard EX Full Art", "charizard ex")
        assert not VintedCollector._title_matches("Pikachu EX", "charizard")

    def test_title_matches_multi_word(self):
        assert VintedCollector._title_matches("Pokemon Emerald GBA", "pokemon emerald")
        # "pokemon" matches (main keyword) and 1/2 words match (50%), so this passes
        # To truly not match, the main keyword must be absent
        assert not VintedCollector._title_matches("Zelda Emerald GBA", "pokemon emerald")

    def test_title_matches_translations(self):
        # Should match French name "Dracaufeu" when searching for "charizard"
        assert VintedCollector._title_matches("Dracaufeu EX carte", "charizard")
        assert VintedCollector._title_matches("Glurak Gold Star", "charizard")

    def test_is_suspicious(self):
        from src.collectors.vinted import VintedListing
        listing = VintedListing("Charizard", 0.10, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing, min_price=0.50)

        listing_ok = VintedListing("Charizard", 50.0, "", None, "user", None)
        assert not VintedCollector.is_suspicious(listing_ok, min_price=0.50)

    def test_is_suspicious_scam_words(self):
        from src.collectors.vinted import VintedListing
        listing = VintedListing("Scambio Charizard", 50.0, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing)

        listing2 = VintedListing("Cerco Charizard", 50.0, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing2)

    def test_translate_query(self):
        v = VintedCollector()
        assert "dracaufeu" in v._translate_query("charizard base set", "fr")
        assert "glurak" in v._translate_query("charizard base set", "de")
        assert v._translate_query("charizard", "it") == "charizard"  # No translation

    def test_pokemon_translations_exist(self):
        assert "charizard" in POKEMON_TRANSLATIONS
        assert len(POKEMON_TRANSLATIONS["charizard"]) >= 3
