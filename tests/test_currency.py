import pytest

from src.utils.currency import usd_to_eur, eur_to_usd, format_price


class TestCurrency:
    def test_usd_to_eur_with_rates(self):
        rates = {"EUR": 0.90}
        assert usd_to_eur(100, rates) == 90.0

    def test_usd_to_eur_fallback(self):
        result = usd_to_eur(100, None)
        assert 80 < result < 100  # Reasonable EUR range

    def test_eur_to_usd_roundtrip(self):
        rates = {"EUR": 0.90}
        eur = usd_to_eur(100, rates)
        usd = eur_to_usd(eur, rates)
        assert abs(usd - 100) < 0.01

    def test_format_price(self):
        rates = {"EUR": 0.90}
        result = format_price(100, rates)
        assert "$100.00" in result
        assert "€90.00" in result

    def test_zero(self):
        assert usd_to_eur(0) == 0.0
