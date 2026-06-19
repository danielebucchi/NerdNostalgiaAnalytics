"""Tests for bundle/lot detection and Reddit sentiment LLM helpers.

Groq client is mocked — no network calls. Covers:
- Bundle rule-based pre-check (no LLM call when no trigger keyword).
- Bundle LLM payload sanitization.
- Sentiment multiplier curve.
- enrich_hype_with_sentiment fallback paths (too few posts, LLM offline, API failure).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.utils.llm_parser import (
    BundleAnalysis,
    HypeAnalysis,
    _bundle_payload_to_analysis,
    _looks_like_bundle_pre_check,
    _sentiment_adjusted_score,
    detect_bundle,
    enrich_hype_with_sentiment,
    llm_analyze_bundle,
    llm_analyze_reddit_sentiment,
)


def _mock_response(payload: dict):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


def _fake_posts(titles):
    return [SimpleNamespace(title=t) for t in titles]


# ─────────────────────────── BUNDLE: pre-check ─────────────────────────────

class TestBundlePreCheck:
    def test_no_trigger_returns_false(self):
        assert _looks_like_bundle_pre_check("Charizard base set holo") is False
        assert _looks_like_bundle_pre_check("Pokemon Smeraldo GBA") is False

    def test_lotto_triggers(self):
        assert _looks_like_bundle_pre_check("Lotto carte Pokemon vintage") is True

    def test_xN_triggers(self):
        assert _looks_like_bundle_pre_check("Carte pokemon x10") is True
        assert _looks_like_bundle_pre_check("x 25 booster pack") is True

    def test_plus_n_giochi_triggers(self):
        assert _looks_like_bundle_pre_check("PS1 console + 5 giochi") is True

    def test_numeric_collection_triggers(self):
        assert _looks_like_bundle_pre_check("50 carte rare comuni") is True

    def test_empty(self):
        assert _looks_like_bundle_pre_check("") is False


# ─────────────────────── BUNDLE: payload sanitization ──────────────────────

class TestBundlePayloadSanitization:
    def test_valid_bundle(self):
        a = _bundle_payload_to_analysis({
            "is_bundle": True, "item_count": 50,
            "item_type": "Pokemon TCG cards", "key_items": ["Charizard", "Pikachu"],
            "confidence": 0.95, "notes": "Lotto vintage"
        })
        assert a.is_bundle is True
        assert a.item_count == 50
        assert a.key_items == ["Charizard", "Pikachu"]
        assert a.confidence == 0.95

    def test_negative_item_count_dropped(self):
        a = _bundle_payload_to_analysis({
            "is_bundle": True, "item_count": -5, "item_type": None,
            "key_items": [], "confidence": 0.9, "notes": None,
        })
        assert a.item_count is None

    def test_absurdly_large_item_count_dropped(self):
        a = _bundle_payload_to_analysis({
            "is_bundle": True, "item_count": 9_999_999, "item_type": None,
            "key_items": [], "confidence": 0.9, "notes": None,
        })
        assert a.item_count is None

    def test_key_items_capped_at_5(self):
        a = _bundle_payload_to_analysis({
            "is_bundle": True, "item_count": 10, "item_type": "stuff",
            "key_items": ["a", "b", "c", "d", "e", "f", "g"],
            "confidence": 1.0, "notes": None,
        })
        assert len(a.key_items) == 5

    def test_confidence_clamped(self):
        a = _bundle_payload_to_analysis({
            "is_bundle": True, "item_count": 1, "item_type": None,
            "key_items": [], "confidence": 99.9, "notes": None,
        })
        assert a.confidence == 1.0

    def test_display_summary_with_count_and_type(self):
        a = BundleAnalysis(is_bundle=True, item_count=50,
                           item_type="Pokemon TCG", key_items=["Charizard"],
                           confidence=1.0)
        assert "50" in a.display_summary
        assert "Pokemon TCG" in a.display_summary
        assert "Charizard" in a.display_summary

    def test_display_summary_empty_when_not_bundle(self):
        a = BundleAnalysis(is_bundle=False)
        assert a.display_summary == ""


# ─────────────────── BUNDLE: detect_bundle orchestration ──────────────────

class TestDetectBundle:
    @pytest.mark.asyncio
    async def test_no_trigger_skips_llm(self):
        """Single-item listings shouldn't even reach the LLM."""
        with patch("src.utils.llm_parser.llm_analyze_bundle") as mock_llm:
            mock_llm.return_value = None
            result = await detect_bundle("Charizard base set holo")
        assert result.is_bundle is False
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_but_llm_offline_flags_uncertain(self):
        """When LLM is offline, a trigger raises a low-confidence bundle flag
        so the caller can still warn the user."""
        with patch("src.utils.llm_parser.is_configured", return_value=False):
            result = await detect_bundle("Lotto carte pokemon")
        assert result.is_bundle is True
        assert result.confidence < 0.6
        assert "LLM" in (result.notes or "")

    @pytest.mark.asyncio
    async def test_trigger_with_llm_confirmation(self):
        """LLM confirms a bundle → use its details."""
        fake = BundleAnalysis(
            is_bundle=True, item_count=50, item_type="Pokemon TCG cards",
            key_items=["Charizard"], confidence=0.95,
        )
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_analyze_bundle",
                      AsyncMock(return_value=fake)):
            result = await detect_bundle("Lotto 50 carte Pokemon vintage")
        assert result.is_bundle is True
        assert result.item_count == 50
        assert result.key_items == ["Charizard"]

    @pytest.mark.asyncio
    async def test_trigger_but_llm_rejects(self):
        """User wrote 'x10' but LLM correctly says it's a single item."""
        fake = BundleAnalysis(is_bundle=False, confidence=1.0)
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_analyze_bundle",
                      AsyncMock(return_value=fake)):
            # "x10 holo" might be a card NUMBER (10/X) not a quantity
            result = await detect_bundle("Pikachu holo card x10")
        assert result.is_bundle is False

    @pytest.mark.asyncio
    async def test_llm_low_confidence_downgraded(self):
        """LLM says bundle but with <0.6 confidence → don't warn the user."""
        fake = BundleAnalysis(is_bundle=True, confidence=0.3, notes="incerto")
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_analyze_bundle",
                      AsyncMock(return_value=fake)):
            result = await detect_bundle("Lotto pokemon")
        assert result.is_bundle is False


# ─────────────────── SENTIMENT: multiplier curve ──────────────────────────

class TestSentimentMultiplier:
    def test_strong_positive_amplifies(self):
        assert _sentiment_adjusted_score(50, 0.8) > 50

    def test_strong_negative_dampens(self):
        # Lots of complaints kill hype
        assert _sentiment_adjusted_score(80, -0.7) < 30

    def test_neutral_passes_through(self):
        assert _sentiment_adjusted_score(50, 0.0) == 50
        assert _sentiment_adjusted_score(50, 0.1) == 50

    def test_clamping_at_100(self):
        # 80 * 1.3 = 104 → clamp 100
        assert _sentiment_adjusted_score(80, 0.8) == 100

    def test_clamping_at_0(self):
        assert _sentiment_adjusted_score(10, -0.9) >= 0


# ──────────────── SENTIMENT: LLM call orchestration ───────────────────────

class TestLlmSentimentCall:
    @pytest.mark.asyncio
    async def test_empty_titles_returns_none(self):
        with patch("src.utils.llm_parser.is_configured", return_value=True):
            assert await llm_analyze_reddit_sentiment([]) is None
            assert await llm_analyze_reddit_sentiment(["", "   "]) is None

    @pytest.mark.asyncio
    async def test_unconfigured_returns_none(self):
        with patch("src.utils.llm_parser.is_configured", return_value=False):
            assert await llm_analyze_reddit_sentiment(["a real title"]) is None

    @pytest.mark.asyncio
    async def test_clamps_sentiment(self):
        # Model returns out-of-range sentiment → clamp to [-1, 1]
        fake = _mock_response({"sentiment": 5.0, "summary": "?"})
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser._get_client") as mock_get_client:
            mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=fake)
            sentiment, summary = await llm_analyze_reddit_sentiment(["test"])
        assert sentiment == 1.0


# ─────────────────── SENTIMENT: enrich_hype fallback ──────────────────────

class TestEnrichHype:
    @pytest.mark.asyncio
    async def test_too_few_posts_skips_llm(self):
        with patch("src.utils.llm_parser.llm_analyze_reddit_sentiment") as mock_llm:
            mock_llm.return_value = None
            hype = await enrich_hype_with_sentiment(_fake_posts(["a"]), 30, "desc")
        assert hype.score == 30
        assert hype.sentiment == 0.0
        assert hype.has_sentiment is False
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_raw(self):
        with patch("src.utils.llm_parser.is_configured", return_value=False):
            hype = await enrich_hype_with_sentiment(
                _fake_posts(["a", "b", "c", "d"]), 40, "desc"
            )
        assert hype.score == 40
        assert hype.has_sentiment is False

    @pytest.mark.asyncio
    async def test_positive_sentiment_amplifies(self):
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_analyze_reddit_sentiment",
                      AsyncMock(return_value=(0.8, "Hype alto: nuovo set in arrivo"))):
            hype = await enrich_hype_with_sentiment(
                _fake_posts(["new set incoming", "buy now", "to the moon"]),
                50, "🔥🔥 HYPE ALTO",
            )
        assert hype.score > 50
        assert hype.has_sentiment is True
        assert "set" in hype.summary

    @pytest.mark.asyncio
    async def test_negative_sentiment_dampens(self):
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_analyze_reddit_sentiment",
                      AsyncMock(return_value=(-0.8, "Sentiment negativo: scam reports"))):
            hype = await enrich_hype_with_sentiment(
                _fake_posts(["scam!", "fake!", "avoid"]),
                70, "🔥🔥 HYPE ALTO",
            )
        # raw 70 with strong-negative → dampened heavily
        assert hype.score < 30
        assert hype.has_sentiment is True

    @pytest.mark.asyncio
    async def test_llm_failure_returns_raw(self):
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_analyze_reddit_sentiment",
                      AsyncMock(return_value=None)):
            hype = await enrich_hype_with_sentiment(
                _fake_posts(["a", "b", "c"]), 40, "desc"
            )
        assert hype.score == 40
        assert hype.has_sentiment is False
