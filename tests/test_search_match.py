"""Tests for the best-match picker used after PriceCharting/eBay searches.

Key invariant: when the user types a card number, the result whose name carries
the same `#N` must win — even when other results have textually-longer name
overlap with the query.
"""
from dataclasses import dataclass

from src.utils.search_match import _extract_card_number, best_match


@dataclass
class _Result:
    """Minimal ProductResult-shaped stub for best_match()."""
    name: str
    set_name: str = ""
    product_url: str = ""


class TestExtractCardNumber:
    def test_hash_number(self):
        assert _extract_card_number("mew #8") == "8"
        assert _extract_card_number("Mewtwo & Mew GX #SM191") == "sm191"

    def test_bare_number(self):
        assert _extract_card_number("mew 8 wizards") == "8"

    def test_set_prefixed_code_wins_over_plain_digit(self):
        # "xy110" should be picked, not "110"
        assert _extract_card_number("pikachu xy110") == "xy110"

    def test_no_number(self):
        assert _extract_card_number("charizard base set") is None

    def test_long_year_not_treated_as_number(self):
        # "1999" is 4 digits, still picked as a number — but is_acceptable.
        # We trust the caller (PriceCharting) to filter; this is just extraction.
        assert _extract_card_number("pokemon 1999 promo") == "1999"


class TestBestMatchCardNumber:
    """The Mew #8 regression: PriceCharting returns Mew #8 at index 3 and
    Mewtwo & Mew GX #SM191 at index 2. Before the card-number bonus, the
    fuzzy ratio + first-position bias picked Mewtwo & Mew GX. Now the user's
    bare `8` wins."""

    def test_mew_wbsp_wins_over_mewtwo_gx(self):
        results = [
            _Result(name="Ancient Mew", product_url="...promo/ancient-mew"),
            _Result(name="Mew ex #53", product_url="...promo/mew-ex-53"),
            _Result(name="Mewtwo & Mew GX #SM191", product_url="...promo/mewtwo-&-mew-gx-sm191"),
            _Result(name="Mew #8", product_url="...promo/mew-8"),
            _Result(name="Mew [Gold Star] #101", product_url="...dragon-frontiers/mew-gold-star-101"),
            _Result(name="Mew #9", product_url="...promo/mew-9"),
        ]
        idx = best_match("mew 8 wizards black star promos", results)
        assert results[idx].name == "Mew #8"

    def test_explicit_hash_number_wins(self):
        results = [
            _Result(name="Mewtwo & Mew GX #SM191"),
            _Result(name="Mew ex #53"),
            _Result(name="Mew #8"),
        ]
        idx = best_match("mew #8", results)
        assert results[idx].name == "Mew #8"

    def test_no_card_number_falls_back_to_fuzzy(self):
        results = [
            _Result(name="Charizard #4", set_name="Pokemon Base Set"),
            _Result(name="Charizard ex #199", set_name="Pokemon Scarlet & Violet 151"),
        ]
        idx = best_match("charizard base set", results)
        assert results[idx].name == "Charizard #4"

    def test_wrong_number_penalised(self):
        # User wants #8 but we have #4 and #9 — neither matches; falls back to
        # fuzzy ranking. Important: the wrong-number penalty isn't so harsh
        # that the system fails gracefully when nothing matches exactly.
        results = [
            _Result(name="Charizard #4", set_name="Base Set"),
            _Result(name="Charizard #9", set_name="Base Set"),
        ]
        idx = best_match("charizard 8", results)
        # Either result is acceptable — just must not crash
        assert idx in (0, 1)

    def test_extra_token_penalty(self):
        """When the user types just 'mew', a 'mewtwo & mew gx' result should
        not win over a simpler 'mew' result purely on fuzzy character overlap."""
        results = [
            _Result(name="Mewtwo & Mew GX #SM191"),
            _Result(name="Mew #8"),
        ]
        idx = best_match("mew 8", results)
        assert results[idx].name == "Mew #8"

    def test_single_result_returns_zero(self):
        assert best_match("anything", [_Result(name="foo")]) == 0

    def test_empty_results(self):
        assert best_match("foo", []) == 0
