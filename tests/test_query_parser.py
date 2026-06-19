"""Tests for the rule-based query parser (no LLM)."""
import pytest

from src.utils.query_parser import ParsedQuery, parse_card_query


class TestExpansionExtraction:
    def test_italian_set_name(self):
        p = parse_card_query("ex rubino e zaffiro charizard psa 10")
        assert p.expansion is not None
        assert p.expansion.code == "ex1"
        assert "charizard" in (p.name or "").lower()

    def test_english_set_name(self):
        p = parse_card_query("base set charizard")
        assert p.expansion is not None
        assert p.expansion.code == "base1"

    def test_set_alias(self):
        p = parse_card_query("rubino zaffiro pikachu")
        assert p.expansion is not None
        assert p.expansion.code == "ex1"

    def test_only_set_no_card(self):
        p = parse_card_query("ex rubino e zaffiro")
        assert p.is_pure_set_query
        assert p.expansion.code == "ex1"
        assert p.name is None

    def test_no_set_present(self):
        p = parse_card_query("charizard")
        assert p.expansion is None
        assert p.name == "charizard"

    def test_set_with_number_name(self):
        p = parse_card_query("151 charizard")
        assert p.expansion is not None
        assert p.expansion.code == "sv3pt5"
        assert "charizard" in (p.name or "").lower()

    def test_mega_evolution_set(self):
        p = parse_card_query("chaos rising")
        assert p.expansion is not None
        assert p.expansion.code == "me05"


class TestConditionExtraction:
    def test_graded_psa(self):
        p = parse_card_query("charizard psa 10")
        assert p.card_condition is not None
        assert p.card_condition.is_graded
        assert p.card_condition.grade == 10.0
        assert "charizard" in (p.name or "").lower()
        assert "psa" not in (p.name or "").lower()

    def test_graded_bgs(self):
        p = parse_card_query("mewtwo bgs 9.5")
        assert p.card_condition is not None
        assert p.card_condition.grade == 9.5

    def test_raw_nm(self):
        p = parse_card_query("pikachu near mint")
        assert p.card_condition is not None
        assert p.card_condition.raw_grade == "NM"

    def test_italian_condition(self):
        p = parse_card_query("carta perfetto stato")
        assert p.card_condition is not None
        assert p.card_condition.raw_grade == "NM"

    def test_no_condition(self):
        p = parse_card_query("just a card")
        assert p.card_condition is None


class TestLanguageExtraction:
    def test_italian_marker(self):
        p = parse_card_query("charizard ita")
        assert p.language == "ita"
        # Marker should be stripped from name
        assert " ita" not in (p.name or "")

    def test_english_marker(self):
        p = parse_card_query("charizard english")
        assert p.language == "eng"

    def test_japanese_marker(self):
        p = parse_card_query("charizard jp")
        assert p.language == "jpn"

    def test_no_language(self):
        p = parse_card_query("charizard")
        assert p.language is None


class TestVariantExtraction:
    def test_holo(self):
        p = parse_card_query("charizard holo")
        assert p.variant == "holo"

    def test_reverse_beats_holo(self):
        p = parse_card_query("charizard reverse holo")
        assert p.variant == "reverse holo"

    def test_full_art(self):
        p = parse_card_query("pikachu full art")
        assert p.variant == "full art"

    def test_shiny(self):
        p = parse_card_query("rayquaza shiny")
        assert p.variant == "shiny"

    def test_promo(self):
        p = parse_card_query("pikachu promo")
        assert p.variant == "promo"

    def test_no_variant(self):
        p = parse_card_query("charizard")
        assert p.variant is None


class TestCompositeQueries:
    def test_set_plus_condition_plus_language(self):
        p = parse_card_query("ex rubino zaffiro charizard psa 10 ita holo")
        assert p.expansion.code == "ex1"
        assert p.card_condition.is_graded and p.card_condition.grade == 10.0
        assert p.language == "ita"
        assert p.variant == "holo"
        assert (p.name or "").strip().lower() == "charizard"
        assert p.confidence >= 0.9

    def test_pure_set_high_confidence(self):
        p = parse_card_query("evoluzioni a paldea")
        assert p.is_pure_set_query
        assert p.confidence >= 0.4  # set found, that's enough

    def test_unknown_garbage(self):
        p = parse_card_query("xyzqq random gibberish")
        assert p.confidence <= 0.2

    def test_empty_string(self):
        p = parse_card_query("")
        assert p.name is None
        assert p.expansion is None
        assert p.confidence == 0.0


class TestParsedQueryDataclass:
    def test_set_code_property_with_expansion(self):
        p = parse_card_query("base set")
        assert p.set_code == "base1"

    def test_set_code_property_without_expansion(self):
        p = parse_card_query("charizard")
        assert p.set_code is None

    def test_set_name_property(self):
        p = parse_card_query("base set")
        assert p.set_name == "Base Set"

    def test_is_pure_set_query_true(self):
        assert parse_card_query("ex rubino e zaffiro").is_pure_set_query

    def test_is_pure_set_query_false_with_card_name(self):
        assert not parse_card_query("ex rubino zaffiro charizard").is_pure_set_query

    def test_is_pure_set_query_false_no_set(self):
        assert not parse_card_query("charizard").is_pure_set_query
