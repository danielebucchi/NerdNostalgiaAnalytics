from src.utils.condition import detect_condition, get_condition_price, CONDITION_EMOJI
from src.collectors.base import PricePoint
from datetime import datetime


class TestGetConditionPrice:
    def _make_conditions(self):
        now = datetime.now()
        return {
            "Ungraded": [PricePoint(date=now, price=50.0)],
            "Complete in Box": [PricePoint(date=now, price=150.0)],
            "New/Sealed": [PricePoint(date=now, price=400.0)],
            "Graded (PSA)": [PricePoint(date=now, price=600.0)],
        }

    def test_direct_match(self):
        conditions = self._make_conditions()
        price, used = get_condition_price(conditions, "Ungraded")
        assert price == 50.0
        assert used == "Ungraded"

    def test_cib_match(self):
        conditions = self._make_conditions()
        price, used = get_condition_price(conditions, "Complete in Box")
        assert price == 150.0

    def test_unknown_defaults_to_ungraded(self):
        conditions = self._make_conditions()
        price, used = get_condition_price(conditions, "Unknown")
        assert price == 50.0
        assert used == "Ungraded"

    def test_missing_condition_fallback(self):
        conditions = {
            "Complete in Box": [PricePoint(date=datetime.now(), price=100.0)],
        }
        price, used = get_condition_price(conditions, "Ungraded")
        # Ungraded not available, should fallback to CIB
        assert price == 100.0
        assert used == "Complete in Box"

    def test_empty_conditions(self):
        price, used = get_condition_price({}, "Ungraded")
        assert price is None
        assert used == "Unknown"


class TestConditionEmoji:
    def test_all_conditions_have_emoji(self):
        for condition in ["Ungraded", "Complete in Box", "New/Sealed", "Graded (PSA)", "Unknown"]:
            assert condition in CONDITION_EMOJI


class TestEdgeCases:
    def test_empty_string(self):
        assert detect_condition("") == "Unknown"

    def test_only_numbers(self):
        assert detect_condition("12345") == "Unknown"

    def test_mixed_signals_loose_wins(self):
        # "senza scatola" (loose) should override "scatola" being present
        result = detect_condition("gioco senza scatola originale")
        assert result == "Ungraded"

    def test_case_insensitive(self):
        assert detect_condition("SIGILLATO FACTORY SEALED") == "New/Sealed"
        assert detect_condition("PSA 10 GEM MINT") == "Graded (PSA)"
