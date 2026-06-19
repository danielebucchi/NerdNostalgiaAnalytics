"""
User-facing commands: /me (profile + stats), /settings (preferences editor).

`/me` is read-only — counts a few things from the user's scoped tables and
prints a card. `/settings` shows current preferences and offers inline buttons
to cycle through valid values for each one (no free-text input → no edge cases
to validate).
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.db.database import async_session
from src.db.models import (
    Alert,
    PortfolioEntry,
    PriceAlert,
    User,
    VintedWatch,
    WatchlistEntry,
)
from src.services.users import (
    DEFAULT_PREFERENCES,
    get_or_create_user,
    get_preference,
    set_preference,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────── /me ──────────────────────────────────────

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = context.user_data.get("user") or await get_or_create_user(tg_user)

    async with async_session() as session:
        watchlist_count = (await session.execute(
            select(func.count(WatchlistEntry.id))
            .where(WatchlistEntry.telegram_user_id == user.telegram_user_id)
        )).scalar_one()
        alerts_count = (await session.execute(
            select(func.count(Alert.id))
            .where(Alert.telegram_user_id == user.telegram_user_id, Alert.is_active.is_(True))
        )).scalar_one()
        price_alerts_count = (await session.execute(
            select(func.count(PriceAlert.id))
            .where(PriceAlert.telegram_user_id == user.telegram_user_id, PriceAlert.is_active.is_(True))
        )).scalar_one()
        vinted_watches_count = (await session.execute(
            select(func.count(VintedWatch.id))
            .where(VintedWatch.telegram_user_id == user.telegram_user_id, VintedWatch.is_active.is_(True))
        )).scalar_one()
        portfolio_open = (await session.execute(
            select(func.count(PortfolioEntry.id))
            .where(PortfolioEntry.telegram_user_id == user.telegram_user_id, PortfolioEntry.sold.is_(False))
        )).scalar_one()
        portfolio_closed = (await session.execute(
            select(func.count(PortfolioEntry.id))
            .where(PortfolioEntry.telegram_user_id == user.telegram_user_id, PortfolioEntry.sold.is_(True))
        )).scalar_one()

    display_name = user.first_name or user.username or str(user.telegram_user_id)
    badge = "👑 *Admin*" if user.is_admin else "👤 Utente"
    joined = user.created_at.strftime("%d %b %Y") if user.created_at else "?"

    lines = [
        f"{badge}",
        f"*{display_name}*"
        + (f" (@{user.username})" if user.username else "")
        + f"\n_ID Telegram: `{user.telegram_user_id}`_",
        f"📅 Iscritto: {joined}",
        "",
        "📊 *Le tue attivita':*",
        f"  👁  Watchlist: *{watchlist_count}* prodotti",
        f"  🔔 Alert segnali attivi: *{alerts_count}*",
        f"  💰 Price alert attivi: *{price_alerts_count}*",
        f"  🛒 Vinted watch attivi: *{vinted_watches_count}*",
        f"  💼 Portfolio: *{portfolio_open}* aperti / *{portfolio_closed}* chiusi",
        "",
        "⚙️ Usa /settings per le preferenze.",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────── /settings ────────────────────────────────────

# Each editable preference is presented as a row of buttons. The user clicks a
# value → callback rewrites the preference → message is re-rendered.
_EDITABLE_PREFS: dict[str, dict] = {
    "currency": {
        "label": "💱 Valuta",
        "values": ["EUR", "USD"],
        "format": lambda v: v,
    },
    "default_margin_pct": {
        "label": "🎯 Margine default (%)",
        "values": [15, 20, 25, 30, 40, 50],
        "format": lambda v: f"{v}%",
    },
    "default_card_condition": {
        "label": "🎴 Condizione carta default",
        "values": ["NM", "EX", "GO", "LP", "PL", "PO"],
        "format": lambda v: v,
    },
    "notifications": {
        "label": "🔔 Notifiche",
        "values": [True, False],
        "format": lambda v: "ON" if v else "OFF",
    },
    "display_language": {
        "label": "🌐 Lingua interfaccia",
        "values": [None, "it", "en"],
        "format": lambda v: {None: "auto", "it": "italiano", "en": "english"}[v],
    },
}


def _render_settings_message(user: User) -> tuple[str, InlineKeyboardMarkup]:
    """Render the settings card + inline keyboard for the given user."""
    lines = ["⚙️ *Le tue preferenze:*\n"]
    rows: list[list[InlineKeyboardButton]] = []
    for key, meta in _EDITABLE_PREFS.items():
        current = get_preference(user, key)
        formatted = meta["format"](current)
        lines.append(f"{meta['label']}: *{formatted}*")
        # Build buttons for valid values. Highlight the current one with ✓.
        buttons = []
        for v in meta["values"]:
            label = meta["format"](v)
            if v == current:
                label = f"✓ {label}"
            buttons.append(InlineKeyboardButton(label, callback_data=f"pref:{key}:{_serialize(v)}"))
        rows.append(buttons)
    lines.append("\n_Premi un valore per cambiarlo._")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _serialize(value) -> str:
    """Compact callback-data encoding (Telegram cap: 64 bytes)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _deserialize(key: str, raw: str):
    """Reverse of `_serialize`, typed per preference key."""
    if raw == "null":
        return None
    if key in ("notifications",):
        return raw == "1"
    if key in ("default_margin_pct",):
        return int(raw)
    return raw


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("user") or await get_or_create_user(update.effective_user)
    text, kb = _render_settings_message(user)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def settings_pref_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, key, raw_value = query.data.split(":", 2)
        if key not in _EDITABLE_PREFS:
            await query.answer("Preferenza sconosciuta.", show_alert=True)
            return
        value = _deserialize(key, raw_value)
        await set_preference(update.effective_user.id, key, value)
    except Exception as e:
        logger.exception(f"Failed to update preference {query.data!r}: {e}")
        await query.answer("Errore nel salvataggio.", show_alert=True)
        return

    # Re-render with the updated user.
    user = await get_or_create_user(update.effective_user)
    text, kb = _render_settings_message(user)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        # No-op edits (same content) raise — ignore.
        pass
