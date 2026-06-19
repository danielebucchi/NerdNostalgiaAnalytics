"""Shared infrastructure for the "match incerto — scegli quale prodotto" picker.

Used by /evaluate, /offer, /link when `best_match_with_confidence` returns a
confidence below the prompt threshold. Each handler stashes its in-progress
state under its own namespace in `context.user_data["picker"]`, then renders
an inline keyboard whose callback_data carries the namespace + a short token.

State expires after `PICKER_TTL_SECONDS` so abandoned sessions don't leak,
and is one-shot (removed on retrieve) so re-clicking the same button doesn't
double-run the underlying handler.
"""
from __future__ import annotations

import secrets
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes


PICKER_TTL_SECONDS = 5 * 60  # 5 min — long enough for the user to scroll/decide
PICKER_MAX_CANDIDATES = 5
PICKER_BUTTON_LABEL_MAX = 60


def stash_picker_state(
    context: ContextTypes.DEFAULT_TYPE, namespace: str, state: dict,
) -> str:
    """Save picker state under `namespace` and return the token used in callback_data.

    Each namespace (e.g. "evaluate", "offer", "link") gets its own bucket so
    one handler's tokens can't be confused with another's.
    """
    root = context.user_data.setdefault("picker", {})
    bucket = root.setdefault(namespace, {})
    # Evict expired entries first — keeps per-user state small without a
    # dedicated cleanup job.
    now = time.time()
    for k in list(bucket):
        if bucket[k].get("expires_at", 0) < now:
            del bucket[k]
    token = secrets.token_urlsafe(6)
    state["expires_at"] = now + PICKER_TTL_SECONDS
    bucket[token] = state
    return token


def retrieve_picker_state(
    context: ContextTypes.DEFAULT_TYPE, namespace: str, token: str,
) -> dict | None:
    """Look up + delete state by token. Returns None when expired/missing/wrong."""
    root = (context.user_data or {}).get("picker") or {}
    bucket = root.get(namespace) or {}
    state = bucket.get(token)
    if not state:
        return None
    if state.get("expires_at", 0) < time.time():
        del bucket[token]
        return None
    # One-shot: remove after retrieval so re-clicking the same button doesn't
    # double-run the evaluation.
    del bucket[token]
    return state


def discard_picker_state(
    context: ContextTypes.DEFAULT_TYPE, namespace: str, token: str,
) -> None:
    """Drop state without running anything (used by the Cancel button)."""
    root = (context.user_data or {}).get("picker") or {}
    bucket = root.get(namespace) or {}
    bucket.pop(token, None)


def build_picker_keyboard(
    candidates: list[Any],
    suggested_idx: int,
    callback_prefix: str,
    token: str,
    name_attr: str = "name",
) -> InlineKeyboardMarkup:
    """Build an inline keyboard with one button per candidate (suggested = 💡).

    `callback_prefix` is the handler-specific tag (e.g. "eval_pick" → callback
    data is "eval_pick:<token>:<idx>" or "eval_pick:<token>:cancel").
    """
    rows = []
    for i, r in enumerate(candidates[:PICKER_MAX_CANDIDATES]):
        marker = "💡 " if i == suggested_idx else ""
        name = getattr(r, name_attr, str(r))
        label = f"{marker}{name}"[:PICKER_BUTTON_LABEL_MAX]
        rows.append([InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{token}:{i}")])
    rows.append([InlineKeyboardButton(
        "❌ Annulla", callback_data=f"{callback_prefix}:{token}:cancel",
    )])
    return InlineKeyboardMarkup(rows)


def parse_picker_callback(data: str) -> tuple[str, str | None]:
    """Decode `<prefix>:<token>:<choice>`. Returns (token, choice) where choice
    is either an int-as-string or "cancel" or None on malformed input."""
    try:
        _, token, choice = data.split(":", 2)
        return token, choice
    except ValueError:
        return "", None
