"""
User context middleware.

Runs in PTB group=-1 before every other handler, so it fires for every command
/ message / callback that reaches the bot. Two responsibilities:

1. Upsert the user (auto-registration + last_seen bump + profile sync).
2. Enforce the whitelist if WHITELIST_TELEGRAM_IDS is configured — blocked
   users get a polite "no access" reply and downstream handlers are skipped
   via `ApplicationHandlerStop`.

The resolved `User` object is stashed in `context.user_data["user"]` so any
downstream handler can read preferences without another DB round-trip.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from src.services.users import (
    get_or_create_user,
    is_allowed,
    whitelist_active,
)

logger = logging.getLogger(__name__)


async def user_context_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-handler: register/sync user + enforce whitelist."""
    tg_user = update.effective_user
    if tg_user is None or tg_user.is_bot:
        # Updates without an attached user (channel_post, etc.) — nothing to do.
        return

    # Whitelist enforcement. Cheap check before touching the DB.
    if whitelist_active() and not is_allowed(tg_user.id):
        logger.info(f"Blocked non-whitelisted user: id={tg_user.id} username={tg_user.username}")
        try:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "🚫 *Accesso negato.*\n\n"
                    "Questo bot e' privato. Contatta il proprietario "
                    f"se pensi di dover avere accesso.\n\n"
                    f"_Il tuo ID Telegram: {tg_user.id}_",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.warning(f"Failed to send block message to {tg_user.id}: {e}")
        # Stop propagation: no other handler should process this update.
        raise ApplicationHandlerStop

    try:
        user = await get_or_create_user(tg_user)
    except Exception as e:
        # Don't take the bot down because of a user-registration failure.
        # Log and let the request proceed without a cached User.
        logger.exception(f"Failed to register/sync user {tg_user.id}: {e}")
        return

    # Stash in context for downstream handlers.
    context.user_data["user"] = user

    # Hard block on disabled users (toggleable from /admin commands later).
    if user.is_blocked:
        logger.info(f"Blocked is_blocked user: id={user.telegram_user_id}")
        try:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "🚫 Il tuo accesso e' stato disattivato dall'amministratore.",
                )
        except Exception:
            pass
        raise ApplicationHandlerStop
