"""Tests for the videogame-condition LLM fallback in llm_parser.py.

The Groq client is mocked — these tests never hit the network. They verify:
- Sanitization (invalid bucket/company/grade → null).
- Fallback semantics (rule-based wins when it knows; LLM only kicks in on Unknown).
- Confidence threshold (LLM returning low confidence doesn't override Unknown).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.utils.condition import VG_CIB, VG_GRADED, VG_LOOSE, VG_MISSING_MANUAL, VG_UNKNOWN
from src.utils.llm_parser import (
    _sanitize_vg,
    _vg_payload_to_condition,
    detect_videogame_condition_with_llm_fallback,
    llm_parse_videogame_condition,
)


def _mock_groq_response(payload: dict):
    """Build a fake response shaped like Groq's chat completion."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))
        ]
    )


class TestSanitization:
    def test_valid_payload_passes(self):
        out = _sanitize_vg({
            "bucket": "Missing Manual",
            "is_graded": False,
            "grading_company": None,
            "grade": None,
        })
        assert out["bucket"] == "Missing Manual"

    def test_unknown_bucket_dropped(self):
        out = _sanitize_vg({"bucket": "Bogus", "is_graded": False,
                            "grading_company": None, "grade": None})
        assert out["bucket"] is None

    def test_unknown_grading_company_dropped(self):
        out = _sanitize_vg({"bucket": "Graded (PSA)", "is_graded": True,
                            "grading_company": "FAKE", "grade": 9.0})
        assert out["grading_company"] is None
        # is_graded gets demoted, bucket cleared because company is missing
        assert out["is_graded"] is False
        assert out["bucket"] is None

    def test_beckett_normalized_to_bgs(self):
        out = _sanitize_vg({"bucket": "Graded (PSA)", "is_graded": True,
                            "grading_company": "BECKETT", "grade": 9.0})
        assert out["grading_company"] == "BGS"
        assert out["is_graded"] is True

    def test_out_of_range_grade_dropped(self):
        out = _sanitize_vg({"bucket": "Graded (PSA)", "is_graded": True,
                            "grading_company": "WATA", "grade": 999})
        assert out["grade"] is None
        assert out["is_graded"] is False

    def test_vga_high_grade_accepted(self):
        out = _sanitize_vg({"bucket": "Graded (PSA)", "is_graded": True,
                            "grading_company": "VGA", "grade": 85})
        assert out["grade"] == 85.0
        assert out["is_graded"] is True


class TestPayloadToCondition:
    def test_graded(self):
        cond = _vg_payload_to_condition({
            "bucket": "Graded (PSA)", "is_graded": True,
            "grading_company": "WATA", "grade": 9.8,
        })
        assert cond.is_graded
        assert cond.grading_company == "WATA"
        assert cond.grade == 9.8
        assert cond.label == "Graded (PSA)"

    def test_bucket_only(self):
        cond = _vg_payload_to_condition({
            "bucket": "Missing Manual", "is_graded": False,
            "grading_company": None, "grade": None,
        })
        assert not cond.is_graded
        assert cond.bucket == "Missing Manual"

    def test_null_bucket_returns_unknown(self):
        cond = _vg_payload_to_condition({
            "bucket": None, "is_graded": False,
            "grading_company": None, "grade": None,
        })
        assert cond.is_known is False


class TestLlmParseVideogameCondition:
    @pytest.mark.asyncio
    async def test_returns_none_when_unconfigured(self):
        with patch("src.utils.llm_parser.is_configured", return_value=False):
            assert await llm_parse_videogame_condition("foo") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_input(self):
        with patch("src.utils.llm_parser.is_configured", return_value=True):
            assert await llm_parse_videogame_condition("") is None
            assert await llm_parse_videogame_condition("   ") is None

    @pytest.mark.asyncio
    async def test_parses_graded_payload(self):
        fake = _mock_groq_response({
            "bucket": "Graded (PSA)", "is_graded": True,
            "grading_company": "WATA", "grade": 9.8, "confidence": 1.0,
        })
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser._get_client") as mock_get_client:
            mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=fake)
            result = await llm_parse_videogame_condition("Mario WATA 9.8 sealed")
        assert result is not None
        cond, conf = result
        assert cond.is_graded
        assert cond.grading_company == "WATA"
        assert conf == 1.0

    @pytest.mark.asyncio
    async def test_handles_unparseable_json(self):
        fake = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content="not json"))
        ])
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser._get_client") as mock_get_client:
            mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=fake)
            result = await llm_parse_videogame_condition("blah")
        assert result is None


class TestFallbackChain:
    """`detect_videogame_condition_with_llm_fallback` should defer to the rule
    based detector first and only escalate when it returns Unknown."""

    @pytest.mark.asyncio
    async def test_rule_based_wins_when_known(self):
        # Rule-based catches "solo manuale" → Manual Only, LLM never called
        with patch("src.utils.llm_parser.llm_parse_videogame_condition") as mock_llm:
            mock_llm.return_value = None  # would fail if called
            cond = await detect_videogame_condition_with_llm_fallback("Pokemon solo manuale")
        assert cond.bucket == "Manual Only"
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_kicks_in_on_unknown(self):
        fake_cond_pair = (_vg_payload_to_condition({
            "bucket": "Missing Manual", "is_graded": False,
            "grading_company": None, "grade": None,
        }), 0.9)
        # Rule-based returns Unknown for this idiomatic phrasing
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_parse_videogame_condition",
                      AsyncMock(return_value=fake_cond_pair)):
            cond = await detect_videogame_condition_with_llm_fallback(
                "ho perso il libretto del gioco"
            )
        assert cond.bucket == "Missing Manual"

    @pytest.mark.asyncio
    async def test_low_confidence_llm_rejected(self):
        # LLM hallucinates a bucket but with low confidence — keep Unknown
        fake_cond_pair = (_vg_payload_to_condition({
            "bucket": "Complete in Box", "is_graded": False,
            "grading_company": None, "grade": None,
        }), 0.1)
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_parse_videogame_condition",
                      AsyncMock(return_value=fake_cond_pair)):
            cond = await detect_videogame_condition_with_llm_fallback(
                "Pokemon Red"  # bare title, no signal
            )
        assert cond.is_known is False

    @pytest.mark.asyncio
    async def test_llm_disabled_keeps_unknown(self):
        with patch("src.utils.llm_parser.is_configured", return_value=False):
            cond = await detect_videogame_condition_with_llm_fallback("Pokemon Red")
        assert cond.is_known is False

    @pytest.mark.asyncio
    async def test_llm_api_failure_returns_rule_based(self):
        with patch("src.utils.llm_parser.is_configured", return_value=True), \
                patch("src.utils.llm_parser.llm_parse_videogame_condition",
                      AsyncMock(return_value=None)):
            cond = await detect_videogame_condition_with_llm_fallback("Pokemon Red")
        # API failure → fall back to rule-based, which says Unknown for bare titles
        assert cond.is_known is False
