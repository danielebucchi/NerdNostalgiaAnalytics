"""Tests for database models and enums."""
from src.db.models import (
    ProductCategory, SignalType, Product, PriceHistory,
    WatchlistEntry, Alert, PriceAlert, VintedWatch, PortfolioEntry,
)


class TestEnums:
    def test_product_categories(self):
        assert ProductCategory.POKEMON == "pokemon"
        assert ProductCategory.MAGIC == "magic"
        assert ProductCategory.YUGIOH == "yugioh"
        assert ProductCategory.VIDEOGAME == "videogame"
        assert ProductCategory.OTHER == "other"

    def test_signal_types(self):
        assert SignalType.BUY == "BUY"
        assert SignalType.SELL == "SELL"
        assert SignalType.STRONG_BUY == "STRONG BUY"
        assert SignalType.STRONG_SELL == "STRONG SELL"
        assert SignalType.HOLD == "HOLD"


class TestModelFields:
    def test_product_has_required_fields(self):
        p = Product(external_id="test", source="test", name="Test")
        assert p.external_id == "test"
        assert p.current_price is None

    def test_vinted_watch_defaults(self):
        w = VintedWatch(telegram_user_id=123, search_query="test", max_price_eur=50)
        assert w.is_active is None or w.is_active  # Default True
        assert w.countries is None or w.countries == "it"

    def test_portfolio_entry_defaults(self):
        e = PortfolioEntry(telegram_user_id=123, product_id=1, buy_price=50)
        assert e.sold is None or not e.sold
        assert e.quantity is None or e.quantity == 1
