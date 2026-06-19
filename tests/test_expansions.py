"""Tests for ExpansionRegistry: fuzzy matching, persistence, idempotency."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest

from src.utils.expansions import (
    DATA_PATH,
    EXTERNAL_FIELDS,
    Expansion,
    ExpansionRegistry,
)


@pytest.fixture
def tmp_registry(tmp_path: Path) -> ExpansionRegistry:
    """Fresh registry pointing at a tmp copy of the real expansions.json."""
    target = tmp_path / "expansions.json"
    shutil.copy(DATA_PATH, target)
    return ExpansionRegistry(target)


class TestLoad:
    def test_loads_all(self, tmp_registry):
        # Snapshot expectations: keep these loose so they don't break with each new set
        exps = tmp_registry.all()
        assert len(exps) >= 120
        assert all(e.game == "pokemon" for e in exps)

    def test_by_code(self, tmp_registry):
        ex = tmp_registry.by_code("ex1")
        assert ex is not None
        assert ex.name_en == "EX Ruby & Sapphire"
        assert ex.name_it == "EX Rubino e Zaffiro"

    def test_by_code_case_insensitive(self, tmp_registry):
        assert tmp_registry.by_code("EX1") is not None
        assert tmp_registry.by_code("SV3PT5") is not None

    def test_unknown_code(self, tmp_registry):
        assert tmp_registry.by_code("nonexistent") is None

    def test_name_it_falls_back_to_name_en(self, tmp_registry):
        # Verified via Charizard's home set — the spec promises Italian name is
        # always populated (falls back to English when no IT translation exists).
        for e in tmp_registry.all():
            assert e.name_it, f"{e.code} has empty name_it"


class TestFind:
    def test_exact_code(self, tmp_registry):
        m = tmp_registry.find("ex1")
        assert m is not None
        assert m.expansion.code == "ex1"
        assert m.score == 100

    def test_italian_full_name(self, tmp_registry):
        m = tmp_registry.find("EX Rubino e Zaffiro")
        assert m is not None
        assert m.expansion.code == "ex1"

    def test_english_full_name(self, tmp_registry):
        m = tmp_registry.find("EX Ruby & Sapphire")
        assert m is not None
        assert m.expansion.code == "ex1"

    def test_alias(self, tmp_registry):
        m = tmp_registry.find("rubino e zaffiro")
        assert m is not None
        assert m.expansion.code == "ex1"

    def test_fuzzy_typo(self, tmp_registry):
        m = tmp_registry.find("ex rubino zafiro")  # missing 'f'
        assert m is not None
        assert m.expansion.code == "ex1"

    def test_below_threshold(self, tmp_registry):
        # Garbage query should not match anything
        assert tmp_registry.find("zzzqqqxxxnomatch") is None

    def test_set_151(self, tmp_registry):
        m = tmp_registry.find("151")
        assert m is not None
        assert m.expansion.code == "sv3pt5"

    def test_scarlet_violet_151_alias(self, tmp_registry):
        m = tmp_registry.find("scarlet violet 151")
        assert m is not None
        assert m.expansion.code == "sv3pt5"

    def test_game_filter_excludes_other_games(self, tmp_registry):
        # Future-proofing: if magic/yugioh entries are added, the filter must hold
        m = tmp_registry.find("ex1", game="magic")
        assert m is None  # ex1 is pokemon

    def test_empty_query(self, tmp_registry):
        assert tmp_registry.find("") is None


class TestFindInText:
    def test_finds_expansion_in_title(self, tmp_registry):
        m = tmp_registry.find_in_text("Charizard EX Rubino e Zaffiro PSA 10 holo")
        assert m is not None
        assert m.expansion.code == "ex1"

    def test_finds_set_name_in_description(self, tmp_registry):
        m = tmp_registry.find_in_text("Carta Pikachu del set Sole e Luna in vendita")
        assert m is not None
        assert m.expansion.code == "sm1"

    def test_prefers_longer_match(self, tmp_registry):
        # "XY" is a valid set, but "XY Furious Fists" should win
        m = tmp_registry.find_in_text("Lucario - XY Furious Fists - holo")
        assert m is not None
        assert m.expansion.code == "xy3"  # Furious Fists, not bare XY

    def test_too_short_query_skipped(self, tmp_registry):
        # 'xy' alone (2 chars) won't substring-match; needs context
        m = tmp_registry.find_in_text("oxygen rich card", threshold=99)
        # 'oxygen' contains 'xy' — must not falsely match the XY set
        assert m is None or m.expansion.code != "xy1"

    def test_empty_text(self, tmp_registry):
        assert tmp_registry.find_in_text("") is None


class TestRecordExternalCode:
    def test_records_new_value(self, tmp_registry):
        wrote = asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 85))
        assert wrote is True
        assert tmp_registry.by_code("ex1").cardtrader_id == 85

    def test_idempotent(self, tmp_registry):
        asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 85))
        wrote = asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 85))
        assert wrote is False

    def test_overwrites_different_value(self, tmp_registry):
        asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 85))
        wrote = asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 86))
        assert wrote is True
        assert tmp_registry.by_code("ex1").cardtrader_id == 86

    def test_unknown_code(self, tmp_registry):
        wrote = asyncio.run(tmp_registry.record_external_code("nonexistent", "cardtrader_id", 1))
        assert wrote is False

    def test_unknown_field(self, tmp_registry):
        wrote = asyncio.run(tmp_registry.record_external_code("ex1", "bogus_field", 1))
        assert wrote is False

    def test_none_value_skipped(self, tmp_registry):
        wrote = asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", None))
        assert wrote is False

    def test_empty_string_skipped(self, tmp_registry):
        wrote = asyncio.run(tmp_registry.record_external_code("ex1", "cardmarket_code", ""))
        assert wrote is False

    def test_all_external_fields_accepted(self, tmp_registry):
        for field in EXTERNAL_FIELDS:
            value = 1 if field == "cardtrader_id" else "TEST"
            wrote = asyncio.run(tmp_registry.record_external_code("ex1", field, value))
            assert wrote is True

    def test_persists_to_disk(self, tmp_registry, tmp_path):
        asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 85))
        # Re-load from disk
        fresh = ExpansionRegistry(tmp_registry._path)
        assert fresh.by_code("ex1").cardtrader_id == 85

    def test_serialised_only_when_set(self, tmp_registry):
        # ex2 has no external codes — its serialised dict must not contain those keys.
        # This keeps diffs minimal in the JSON file.
        asyncio.run(tmp_registry.record_external_code("ex1", "cardtrader_id", 85))
        with tmp_registry._path.open() as f:
            data = json.load(f)
        ex1 = next(e for e in data["expansions"] if e["code"] == "ex1")
        ex2 = next(e for e in data["expansions"] if e["code"] == "ex2")
        assert ex1.get("cardtrader_id") == 85
        assert "cardtrader_id" not in ex2
