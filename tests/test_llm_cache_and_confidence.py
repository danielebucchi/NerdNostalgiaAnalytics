"""Tests for the LLM TTL cache and the best_match confidence score."""
from __future__ import annotations

import asyncio
import time

import pytest

from src.utils.llm_parser import (
    _cached_call,
    llm_cache_clear,
    llm_cache_stats,
)
from src.utils.search_match import (
    best_match,
    best_match_with_confidence,
    confidence_emoji,
)


from dataclasses import dataclass


@dataclass
class _R:
    name: str
    set_name: str = ""
    product_url: str = ""


class TestLlmCache:
    def setup_method(self):
        llm_cache_clear()

    @pytest.mark.asyncio
    async def test_cache_returns_same_value_without_recall(self):
        call_count = 0

        async def fake_call():
            nonlocal call_count
            call_count += 1
            return {"value": 42}

        v1 = await _cached_call("test", "key1", fake_call)
        v2 = await _cached_call("test", "key1", fake_call)
        assert v1 == v2 == {"value": 42}
        assert call_count == 1  # second call hit the cache

    @pytest.mark.asyncio
    async def test_cache_distinguishes_prompt_types(self):
        async def f_a():
            return "from-a"

        async def f_b():
            return "from-b"

        a = await _cached_call("type-a", "same-input", f_a)
        b = await _cached_call("type-b", "same-input", f_b)
        assert a == "from-a"
        assert b == "from-b"

    @pytest.mark.asyncio
    async def test_cache_key_normalizes_whitespace_and_case(self):
        call_count = 0

        async def fake_call():
            nonlocal call_count
            call_count += 1
            return "result"

        await _cached_call("test", "  Hello World  ", fake_call)
        await _cached_call("test", "hello world", fake_call)
        assert call_count == 1  # same key after lower+strip

    @pytest.mark.asyncio
    async def test_cache_ttl_expires(self):
        call_count = 0

        async def fake_call():
            nonlocal call_count
            call_count += 1
            return "v"

        await _cached_call("test", "k", fake_call, ttl=0.05)
        # Wait past expiry
        await asyncio.sleep(0.1)
        await _cached_call("test", "k", fake_call, ttl=0.05)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_stats(self):
        async def fake():
            return 1

        await _cached_call("test", "a", fake)
        await _cached_call("test", "b", fake)
        s = llm_cache_stats()
        assert s["entries"] == 2
        assert s["live"] == 2

    @pytest.mark.asyncio
    async def test_cache_stores_none_returns(self):
        """We cache None too — failure cases shouldn't be retried tight-loop."""
        call_count = 0

        async def returns_none():
            nonlocal call_count
            call_count += 1
            return None

        v1 = await _cached_call("test", "k", returns_none)
        v2 = await _cached_call("test", "k", returns_none)
        assert v1 is None and v2 is None
        assert call_count == 1


class TestBestMatchConfidence:
    def test_single_result_returns_full_confidence(self):
        _, conf = best_match_with_confidence("anything", [_R(name="foo")])
        assert conf == 1.0

    def test_empty_results_returns_zero_confidence(self):
        _, conf = best_match_with_confidence("anything", [])
        assert conf == 0.0

    def test_unambiguous_match_is_high_confidence(self):
        """When one result clearly dominates (card number match + few extra
        tokens), confidence should be high."""
        results = [
            _R(name="Mew #8", product_url="...promo/mew-8"),
            _R(name="Mewtwo & Mew GX #SM191"),
            _R(name="Pikachu #25"),
        ]
        idx, conf = best_match_with_confidence("mew 8", results)
        assert results[idx].name == "Mew #8"
        assert conf >= 0.6  # solid 🟢 or 🟢🟢

    def test_ambiguous_match_is_low_confidence(self):
        """Two nearly-identical results → confidence should drop into picker
        territory."""
        results = [
            _R(name="Charizard #4"),
            _R(name="Charizard #4 [Reverse Holo]"),
        ]
        _, conf = best_match_with_confidence("charizard", results)
        assert conf < 0.85

    def test_back_compat_best_match_still_works(self):
        """`best_match()` is the legacy index-only API — must still work."""
        results = [_R(name="Foo"), _R(name="Bar")]
        idx = best_match("foo", results)
        assert results[idx].name == "Foo"


class TestConfidenceEmoji:
    def test_double_green(self):
        assert confidence_emoji(0.95) == "🟢🟢"

    def test_single_green(self):
        assert confidence_emoji(0.7) == "🟢"

    def test_yellow(self):
        assert confidence_emoji(0.5) == "🟡"

    def test_orange(self):
        assert confidence_emoji(0.2) == "🟠"

    def test_boundary_at_zero(self):
        assert confidence_emoji(0.0) == "🟠"
