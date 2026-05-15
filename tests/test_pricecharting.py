"""Tests for PriceCharting collector parsing logic."""
import pytest
from datetime import datetime

from src.collectors.pricecharting import PriceChartingCollector, _detect_category
from src.db.models import ProductCategory


class TestParsing:
    def test_parse_timestamp_cents_to_dollars(self):
        """Verify chart data is correctly converted from cents to dollars."""
        collector = PriceChartingCollector()
        data = [
            [1609459200000, 50000],  # $500.00 in cents
            [1612137600000, 10050],  # $100.50 in cents
        ]
        prices = collector._parse_timestamp_array(data)
        assert len(prices) == 2
        assert prices[0].price == 500.00
        assert prices[1].price == 100.50

    def test_parse_zero_price_skipped(self):
        collector = PriceChartingCollector()
        data = [[1609459200000, 0], [1609459200000, -100]]
        assert collector._parse_timestamp_array(data) == []

    def test_parse_invalid_data(self):
        collector = PriceChartingCollector()
        assert collector._parse_timestamp_array([]) == []
        assert collector._parse_timestamp_array([[1]]) == []
        assert collector._parse_timestamp_array("not a list") == []

    def test_parse_date_correct(self):
        collector = PriceChartingCollector()
        # 2021-01-01 00:00:00 UTC = 1609459200000 ms
        data = [[1609459200000, 10000]]
        prices = collector._parse_timestamp_array(data)
        assert prices[0].date.year == 2021


class TestCategoryDetection:
    def test_console_in_url(self):
        assert _detect_category("gameboy-advance/pokemon-emerald") == ProductCategory.VIDEOGAME
        assert _detect_category("nintendo-64/mario") == ProductCategory.VIDEOGAME
        assert _detect_category("playstation/crash") == ProductCategory.VIDEOGAME
        assert _detect_category("sega-genesis/sonic") == ProductCategory.VIDEOGAME
        assert _detect_category("nintendo-switch/zelda") == ProductCategory.VIDEOGAME
        assert _detect_category("xbox/halo") == ProductCategory.VIDEOGAME
        assert _detect_category("psp/god-of-war") == ProductCategory.VIDEOGAME

    def test_tcg_set_in_url(self):
        assert _detect_category("pokemon-base-set/charizard") == ProductCategory.POKEMON
        assert _detect_category("pokemon-scarlet-violet-151/mew") == ProductCategory.POKEMON
        assert _detect_category("pokemon-jungle/jolteon") == ProductCategory.POKEMON

    def test_card_number(self):
        assert _detect_category("pokemon card #123") == ProductCategory.POKEMON

    def test_magic(self):
        assert _detect_category("magic-alpha/lotus") == ProductCategory.MAGIC
        assert _detect_category("mtg modern masters") == ProductCategory.MAGIC

    def test_yugioh(self):
        assert _detect_category("yu-gi-oh/blue-eyes") == ProductCategory.YUGIOH

    def test_pokemon_game_not_card(self):
        """Pokemon in console URL = video game, not card."""
        assert _detect_category("gameboy-advance/pokemon-emerald") == ProductCategory.VIDEOGAME
        assert _detect_category("nintendo-ds/pokemon-platinum") == ProductCategory.VIDEOGAME

    def test_unknown(self):
        assert _detect_category("something/random") == ProductCategory.OTHER
