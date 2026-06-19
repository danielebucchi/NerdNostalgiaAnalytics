"""Tests for the shared picker state machinery in src/bot/picker.py.

We don't run the full Telegram callback handlers here — those are exercised
in smoke tests against the running bot. These tests cover the state stash:
namespacing, TTL, one-shot retrieval, cancellation.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.bot.picker import (
    PICKER_MAX_CANDIDATES,
    PICKER_TTL_SECONDS,
    build_picker_keyboard,
    discard_picker_state,
    parse_picker_callback,
    retrieve_picker_state,
    stash_picker_state,
)


def _ctx():
    """Build a stand-in for telegram.ext.ContextTypes.DEFAULT_TYPE."""
    return SimpleNamespace(user_data={})


class TestStashRetrieve:
    def test_roundtrip(self):
        ctx = _ctx()
        token = stash_picker_state(ctx, "evaluate", {"results": [1, 2, 3]})
        state = retrieve_picker_state(ctx, "evaluate", token)
        assert state is not None
        assert state["results"] == [1, 2, 3]

    def test_one_shot_removes_state(self):
        """Re-clicking the same button shouldn't re-run the handler."""
        ctx = _ctx()
        token = stash_picker_state(ctx, "evaluate", {"x": 1})
        first = retrieve_picker_state(ctx, "evaluate", token)
        second = retrieve_picker_state(ctx, "evaluate", token)
        assert first is not None
        assert second is None

    def test_namespaces_dont_collide(self):
        """Same token in /evaluate and /offer namespaces are independent."""
        ctx = _ctx()
        # We can't actually force the same token here (secrets.token_urlsafe
        # makes that astronomical), but we can verify cross-namespace lookup
        # by mismatching the namespace.
        token = stash_picker_state(ctx, "evaluate", {"x": "eval"})
        # Looking it up under "offer" must not find it
        assert retrieve_picker_state(ctx, "offer", token) is None
        # Original namespace still works
        assert retrieve_picker_state(ctx, "evaluate", token)["x"] == "eval"

    def test_unknown_token(self):
        ctx = _ctx()
        assert retrieve_picker_state(ctx, "evaluate", "bogus") is None

    def test_unknown_namespace(self):
        ctx = _ctx()
        assert retrieve_picker_state(ctx, "no-such-namespace", "x") is None


class TestExpiry:
    def test_expired_state_returns_none(self):
        ctx = _ctx()
        token = stash_picker_state(ctx, "evaluate", {"x": 1})
        # Forcibly expire by rewriting the stored expires_at
        ctx.user_data["picker"]["evaluate"][token]["expires_at"] = time.time() - 1
        assert retrieve_picker_state(ctx, "evaluate", token) is None

    def test_expired_entries_evicted_on_next_stash(self):
        ctx = _ctx()
        t1 = stash_picker_state(ctx, "evaluate", {"x": 1})
        ctx.user_data["picker"]["evaluate"][t1]["expires_at"] = time.time() - 1
        t2 = stash_picker_state(ctx, "evaluate", {"x": 2})
        # Expired t1 should be gone after the second stash
        assert t1 not in ctx.user_data["picker"]["evaluate"]
        assert t2 in ctx.user_data["picker"]["evaluate"]

    def test_ttl_constant_is_reasonable(self):
        # 5 minutes — long enough to decide, short enough to not leak.
        assert 60 <= PICKER_TTL_SECONDS <= 30 * 60


class TestDiscard:
    def test_discard_removes_state(self):
        ctx = _ctx()
        token = stash_picker_state(ctx, "evaluate", {"x": 1})
        discard_picker_state(ctx, "evaluate", token)
        assert retrieve_picker_state(ctx, "evaluate", token) is None

    def test_discard_unknown_token_no_error(self):
        ctx = _ctx()
        # Should not raise even if the token never existed
        discard_picker_state(ctx, "evaluate", "bogus")


class TestKeyboardBuilder:
    def test_builds_one_button_per_candidate(self):
        candidates = [
            SimpleNamespace(name="Mew #8"),
            SimpleNamespace(name="Mewtwo & Mew GX #SM191"),
            SimpleNamespace(name="Ancient Mew"),
        ]
        kb = build_picker_keyboard(candidates, 0, "eval_pick", "abc123")
        # 3 candidates + 1 cancel row
        assert len(kb.inline_keyboard) == 4
        assert kb.inline_keyboard[-1][0].text == "❌ Annulla"

    def test_suggested_index_gets_marker(self):
        candidates = [SimpleNamespace(name="A"), SimpleNamespace(name="B")]
        kb = build_picker_keyboard(candidates, 1, "eval_pick", "tk")
        # Marker on the second button
        assert "💡" in kb.inline_keyboard[1][0].text

    def test_caps_at_max_candidates(self):
        candidates = [SimpleNamespace(name=f"r{i}") for i in range(20)]
        kb = build_picker_keyboard(candidates, 0, "eval_pick", "tk")
        # MAX + 1 cancel row
        assert len(kb.inline_keyboard) == PICKER_MAX_CANDIDATES + 1

    def test_callback_data_format(self):
        candidates = [SimpleNamespace(name="X")]
        kb = build_picker_keyboard(candidates, 0, "eval_pick", "abc")
        assert kb.inline_keyboard[0][0].callback_data == "eval_pick:abc:0"
        assert kb.inline_keyboard[1][0].callback_data == "eval_pick:abc:cancel"


class TestParseCallback:
    def test_normal_index(self):
        assert parse_picker_callback("eval_pick:abc:0") == ("abc", "0")

    def test_cancel(self):
        assert parse_picker_callback("eval_pick:abc:cancel") == ("abc", "cancel")

    def test_malformed(self):
        token, choice = parse_picker_callback("malformed")
        assert choice is None
