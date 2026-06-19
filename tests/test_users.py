"""Tests for the user service (registration, preferences, admin bootstrapping)."""
import asyncio
import os
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.db.models import Base, User


class _FakeTelegramUser:
    """Minimal stand-in for telegram.User. The real class is a Telegram-PB
    wrapper we don't need to instantiate just for unit tests."""

    def __init__(self, id: int, first_name="Daniele", last_name=None,
                 username=None, language_code="it", is_bot=False):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.language_code = language_code
        self.is_bot = is_bot


@pytest_asyncio.fixture
async def isolated_db(tmp_path, monkeypatch):
    """Spin up a tmp SQLite for each test so user creates don't leak across
    them. Patches src.db.database.async_session in-place."""
    db_path = tmp_path / "test_users.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import src.db.database as db_module
    import src.services.users as users_module
    monkeypatch.setattr(db_module, "async_session", session_factory)
    monkeypatch.setattr(users_module, "async_session", session_factory)

    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def empty_admin_env(monkeypatch):
    """Force ADMIN_TELEGRAM_IDS and WHITELIST_TELEGRAM_IDS to be empty so the
    bootstrap-first-user logic kicks in."""
    from src.config import settings
    monkeypatch.setattr(settings, "admin_telegram_ids", "")
    monkeypatch.setattr(settings, "whitelist_telegram_ids", "")


class TestRegistration:
    @pytest.mark.asyncio
    async def test_first_user_becomes_admin(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user
        tg = _FakeTelegramUser(id=111, first_name="Alice")
        user = await get_or_create_user(tg)
        assert user.telegram_user_id == 111
        assert user.is_admin is True

    @pytest.mark.asyncio
    async def test_second_user_is_not_admin(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user
        await get_or_create_user(_FakeTelegramUser(id=111))
        bob = await get_or_create_user(_FakeTelegramUser(id=222, first_name="Bob"))
        assert bob.is_admin is False

    @pytest.mark.asyncio
    async def test_explicit_admin_env_overrides_bootstrap(self, isolated_db, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "admin_telegram_ids", "999")
        monkeypatch.setattr(settings, "whitelist_telegram_ids", "")
        from src.services.users import get_or_create_user
        u1 = await get_or_create_user(_FakeTelegramUser(id=111))
        u2 = await get_or_create_user(_FakeTelegramUser(id=999))
        # First user does NOT become admin when env list is non-empty
        assert u1.is_admin is False
        # The listed user IS admin
        assert u2.is_admin is True

    @pytest.mark.asyncio
    async def test_existing_user_profile_sync(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user
        await get_or_create_user(_FakeTelegramUser(id=111, first_name="Alice", username="alice"))
        # Same id, new username + last_name
        updated = await get_or_create_user(_FakeTelegramUser(
            id=111, first_name="Alice", last_name="Smith", username="alice_new"
        ))
        assert updated.username == "alice_new"
        assert updated.last_name == "Smith"

    @pytest.mark.asyncio
    async def test_last_seen_bumped(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user
        u1 = await get_or_create_user(_FakeTelegramUser(id=111))
        original = u1.last_seen
        await asyncio.sleep(0.01)
        u2 = await get_or_create_user(_FakeTelegramUser(id=111))
        assert u2.last_seen > original


class TestWhitelist:
    def test_is_allowed_open_when_no_whitelist(self, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "whitelist_telegram_ids", "")
        from src.services.users import is_allowed, whitelist_active
        assert not whitelist_active()
        assert is_allowed(42)

    def test_is_allowed_blocks_outsiders(self, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "whitelist_telegram_ids", "111,222")
        from src.services.users import is_allowed, whitelist_active
        assert whitelist_active()
        assert is_allowed(111)
        assert is_allowed(222)
        assert not is_allowed(333)

    def test_invalid_id_in_whitelist_ignored(self, monkeypatch, caplog):
        from src.config import settings
        monkeypatch.setattr(settings, "whitelist_telegram_ids", "111,not-a-number,222")
        from src.services.users import is_allowed
        assert is_allowed(111)
        assert is_allowed(222)
        assert not is_allowed(333)


class TestPreferences:
    @pytest.mark.asyncio
    async def test_default_preferences(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user, get_preference
        user = await get_or_create_user(_FakeTelegramUser(id=111))
        assert get_preference(user, "currency") == "EUR"
        assert get_preference(user, "default_margin_pct") == 30
        assert get_preference(user, "default_card_condition") == "NM"

    @pytest.mark.asyncio
    async def test_set_and_read_preference(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user, get_preference, set_preference
        await get_or_create_user(_FakeTelegramUser(id=111))
        await set_preference(111, "default_margin_pct", 40)
        await set_preference(111, "default_card_condition", "LP")
        user = await get_or_create_user(_FakeTelegramUser(id=111))
        assert get_preference(user, "default_margin_pct") == 40
        assert get_preference(user, "default_card_condition") == "LP"
        # Untouched prefs still return defaults
        assert get_preference(user, "currency") == "EUR"

    @pytest.mark.asyncio
    async def test_set_unknown_preference_rejected(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user, set_preference
        await get_or_create_user(_FakeTelegramUser(id=111))
        with pytest.raises(ValueError, match="Unknown preference key"):
            await set_preference(111, "bogus", "x")

    @pytest.mark.asyncio
    async def test_set_preference_for_nonexistent_user(self, isolated_db, empty_admin_env):
        from src.services.users import set_preference
        with pytest.raises(ValueError, match="not found"):
            await set_preference(999, "currency", "USD")


class TestAdminMutation:
    @pytest.mark.asyncio
    async def test_set_admin_toggle(self, isolated_db, empty_admin_env):
        from src.services.users import get_or_create_user, set_admin
        u = await get_or_create_user(_FakeTelegramUser(id=111))
        assert u.is_admin is True  # bootstrap
        await set_admin(111, False)
        refetched = await get_or_create_user(_FakeTelegramUser(id=111))
        assert refetched.is_admin is False
