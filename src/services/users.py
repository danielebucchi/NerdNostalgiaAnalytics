"""
User service: auto-registration, profile sync, preferences, admin checks.

Telegram supplies all profile data for free on every update (`update.effective_user`),
so we don't need any sign-up flow — we just upsert on first interaction. Admin
rights come from ADMIN_TELEGRAM_IDS env (or the first registered user when the
env list is empty — bootstrap-the-owner pattern). Whitelist (if set) gates
access entirely.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from telegram import User as TelegramUser

from src.config import settings
from src.db.database import async_session
from src.db.models import User

logger = logging.getLogger(__name__)


# Defaults baked in. Any preference missing from a user's `preferences` JSON
# falls back to these values via `get_preference()`.
DEFAULT_PREFERENCES: dict[str, Any] = {
    "currency": "EUR",                  # "EUR" | "USD"
    "default_margin_pct": 30,           # int 1-90, used by /offer when no margin given
    "default_card_condition": "NM",     # raw grade used by /evaluate/offer for cards
    "notifications": True,              # alerts on/off (master switch)
    "display_language": None,           # null = use Telegram language_code
}


# ─────────────────────── id-set helpers (env parsing) ──────────────────────

def _parse_id_set(raw: str) -> set[int]:
    out: set[int] = set()
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            logger.warning(f"Ignoring non-integer telegram_id in config: {token!r}")
    return out


def _whitelist() -> set[int]:
    return _parse_id_set(settings.whitelist_telegram_ids)


def _env_admin_ids() -> set[int]:
    return _parse_id_set(settings.admin_telegram_ids)


def whitelist_active() -> bool:
    return bool(_whitelist())


def is_allowed(telegram_user_id: int) -> bool:
    """Returns True when this user is allowed to interact with the bot.
    When whitelist is empty, everyone is allowed."""
    wl = _whitelist()
    if not wl:
        return True
    return telegram_user_id in wl


# ─────────────────────────── registration/sync ─────────────────────────────

async def get_or_create_user(tg_user: TelegramUser) -> User:
    """Upsert the User row from a Telegram user object and stamp last_seen.
    Bootstraps the first registered user as admin when no admin IDs are
    configured in env."""
    now = datetime.utcnow()

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_user_id == tg_user.id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            # First time we see this Telegram user — register them.
            env_admins = _env_admin_ids()
            if env_admins:
                # Admin list explicitly configured → membership defines admin.
                is_admin = tg_user.id in env_admins
            else:
                # Bootstrap: first user to register becomes admin.
                count = (await session.execute(select(func.count(User.telegram_user_id)))).scalar_one()
                is_admin = (count == 0)

            user = User(
                telegram_user_id=tg_user.id,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                username=tg_user.username,
                language_code=tg_user.language_code,
                is_admin=is_admin,
                preferences={},
                created_at=now,
                last_seen=now,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logger.info(f"Registered new user: {user!r} admin={is_admin}")
            return user

        # Existing user — refresh profile fields that may have changed and
        # bump last_seen. Profile sync is best-effort: it keeps display names
        # current without ever blocking on a write.
        changed = False
        if user.first_name != tg_user.first_name:
            user.first_name = tg_user.first_name
            changed = True
        if user.last_name != tg_user.last_name:
            user.last_name = tg_user.last_name
            changed = True
        if user.username != tg_user.username:
            user.username = tg_user.username
            changed = True
        if user.language_code != tg_user.language_code:
            user.language_code = tg_user.language_code
            changed = True
        # If admin env list changed, re-evaluate. Otherwise leave the flag
        # alone (manual admin promotions stick).
        env_admins = _env_admin_ids()
        if env_admins:
            should_be_admin = user.telegram_user_id in env_admins
            if user.is_admin != should_be_admin:
                user.is_admin = should_be_admin
                changed = True

        user.last_seen = now
        await session.commit()
        if changed:
            await session.refresh(user)
        return user


# ────────────────────────────── preferences ────────────────────────────────

def get_preference(user: User, key: str) -> Any:
    """Read a preference key with default fallback. Never raises on missing
    keys — preferences are optional by design."""
    if user.preferences is None:
        return DEFAULT_PREFERENCES.get(key)
    return user.preferences.get(key, DEFAULT_PREFERENCES.get(key))


async def set_preference(telegram_user_id: int, key: str, value: Any) -> None:
    """Set a preference key. Validates against the known default keys to avoid
    typos polluting the JSON column."""
    if key not in DEFAULT_PREFERENCES:
        raise ValueError(f"Unknown preference key: {key!r}. Valid: {list(DEFAULT_PREFERENCES)}")

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError(f"User {telegram_user_id} not found")
        # SQLAlchemy needs a brand-new dict to mark JSON column dirty.
        prefs = dict(user.preferences or {})
        prefs[key] = value
        user.preferences = prefs
        await session.commit()


async def get_user_by_id(telegram_user_id: int) -> User | None:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()


# ─────────────────────────────── admin tooling ─────────────────────────────

async def list_users(*, only_admins: bool = False) -> list[User]:
    async with async_session() as session:
        stmt = select(User).order_by(User.created_at)
        if only_admins:
            stmt = stmt.where(User.is_admin.is_(True))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def set_admin(telegram_user_id: int, is_admin: bool) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError(f"User {telegram_user_id} not found")
        user.is_admin = is_admin
        await session.commit()
