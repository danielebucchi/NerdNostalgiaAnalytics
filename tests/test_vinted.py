import pytest
from src.collectors.vinted import (
    VintedCollector, VintedListing, VINTED_DOMAINS, POKEMON_TRANSLATIONS,
)


class TestVintedDomains:
    def test_all_domains_exist(self):
        for country in ["it", "fr", "de", "es", "nl", "be", "pt", "pl"]:
            assert country in VINTED_DOMAINS
            assert "vinted" in VINTED_DOMAINS[country]

    def test_domains_are_https(self):
        for url in VINTED_DOMAINS.values():
            assert url.startswith("https://")


class TestTitleMatching:
    def test_exact_match(self):
        assert VintedCollector._title_matches("Charizard Base Set", "charizard")

    def test_no_match(self):
        assert not VintedCollector._title_matches("Pikachu EX", "charizard")

    def test_translation_match(self):
        assert VintedCollector._title_matches("Dracaufeu carte pokemon", "charizard")
        assert VintedCollector._title_matches("Glurak Gold Star", "charizard")

    def test_multi_word(self):
        assert VintedCollector._title_matches("Pokemon Emerald GBA", "pokemon emerald")

    def test_case_insensitive(self):
        assert VintedCollector._title_matches("CHARIZARD EX", "charizard")


class TestSuspiciousDetection:
    def test_too_cheap(self):
        listing = VintedListing("Charizard", 0.10, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing, min_price=0.50)

    def test_normal_price_ok(self):
        listing = VintedListing("Charizard", 50.0, "", None, "user", None)
        assert not VintedCollector.is_suspicious(listing)

    def test_scam_trade(self):
        listing = VintedListing("Scambio Charizard", 50.0, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing)

    def test_scam_looking_for(self):
        listing = VintedListing("Cerco Charizard", 50.0, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing)

    def test_scam_french(self):
        listing = VintedListing("Échange Dracaufeu", 50.0, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing)

    def test_scam_german(self):
        listing = VintedListing("Tausch Glurak", 50.0, "", None, "user", None)
        assert VintedCollector.is_suspicious(listing)


class TestTranslation:
    def test_french(self):
        v = VintedCollector()
        result = v._translate_query("charizard base set", "fr")
        assert "dracaufeu" in result

    def test_german(self):
        v = VintedCollector()
        result = v._translate_query("charizard base set", "de")
        assert "glurak" in result

    def test_italian_no_change(self):
        v = VintedCollector()
        assert v._translate_query("charizard", "it") == "charizard"

    def test_unknown_pokemon_no_change(self):
        v = VintedCollector()
        assert v._translate_query("talonflame", "fr") == "talonflame"

    def test_translations_database(self):
        assert len(POKEMON_TRANSLATIONS) >= 10
        for name, translations in POKEMON_TRANSLATIONS.items():
            assert len(translations) >= 2, f"{name} has too few translations"
