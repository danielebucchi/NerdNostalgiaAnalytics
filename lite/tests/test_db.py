"""Tests for the JSON-file database."""
import json
import os
import pytest
from pathlib import Path

from src.db import add_item, remove_item, get_watchlist, get_all_items, update_price, mark_notified, DB_FILE


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Use a temporary DB file for each test."""
    test_db = tmp_path / "watchlist.json"
    monkeypatch.setattr("src.db.DB_FILE", test_db)
    yield
    if test_db.exists():
        test_db.unlink()


class TestAddItem:
    def test_add_new(self):
        result = add_item(123, "Charizard", "base/charizard", "http://test", 300.0, 435.0)
        assert result == "added"
        items = get_watchlist(123)
        assert len(items) == 1
        assert items[0]["name"] == "Charizard"
        assert items[0]["target_price"] == 300.0
        assert items[0]["current_price"] == 435.0
        assert items[0]["notified"] is False

    def test_update_existing(self):
        add_item(123, "Charizard", "base/charizard", "http://test", 300.0, 435.0)
        result = add_item(123, "Charizard", "base/charizard", "http://test", 250.0, 435.0)
        assert result == "updated"
        items = get_watchlist(123)
        assert len(items) == 1
        assert items[0]["target_price"] == 250.0

    def test_different_users(self):
        add_item(100, "Charizard", "base/charizard", "http://test", 300.0, 435.0)
        add_item(200, "Charizard", "base/charizard", "http://test", 350.0, 435.0)
        assert len(get_watchlist(100)) == 1
        assert len(get_watchlist(200)) == 1
        assert len(get_all_items()) == 2

    def test_multiple_products(self):
        add_item(123, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        add_item(123, "Mewtwo", "base/mewtwo", "http://b", 50.0, 78.0)
        items = get_watchlist(123)
        assert len(items) == 2


class TestRemoveItem:
    def test_remove_by_name(self):
        add_item(123, "Charizard Base Set", "base/charizard", "http://a", 300.0, 435.0)
        add_item(123, "Mewtwo", "base/mewtwo", "http://b", 50.0, 78.0)
        removed = remove_item(123, "charizard")
        assert removed == 1
        assert len(get_watchlist(123)) == 1
        assert get_watchlist(123)[0]["name"] == "Mewtwo"

    def test_remove_no_match(self):
        add_item(123, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        removed = remove_item(123, "pikachu")
        assert removed == 0
        assert len(get_watchlist(123)) == 1

    def test_remove_only_own(self):
        add_item(100, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        add_item(200, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        removed = remove_item(100, "charizard")
        assert removed == 1
        assert len(get_watchlist(200)) == 1


class TestUpdatePrice:
    def test_update(self):
        add_item(123, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        update_price("base/charizard", 290.0)
        items = get_watchlist(123)
        assert items[0]["current_price"] == 290.0

    def test_update_affects_all_users(self):
        add_item(100, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        add_item(200, "Charizard", "base/charizard", "http://a", 350.0, 435.0)
        update_price("base/charizard", 280.0)
        assert get_watchlist(100)[0]["current_price"] == 280.0
        assert get_watchlist(200)[0]["current_price"] == 280.0


class TestNotified:
    def test_mark_notified(self):
        add_item(123, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        mark_notified(123, "base/charizard", True)
        assert get_watchlist(123)[0]["notified"] is True

    def test_reset_notified(self):
        add_item(123, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        mark_notified(123, "base/charizard", True)
        mark_notified(123, "base/charizard", False)
        assert get_watchlist(123)[0]["notified"] is False

    def test_notified_per_user(self):
        add_item(100, "Charizard", "base/charizard", "http://a", 300.0, 435.0)
        add_item(200, "Charizard", "base/charizard", "http://a", 350.0, 435.0)
        mark_notified(100, "base/charizard", True)
        assert get_watchlist(100)[0]["notified"] is True
        assert get_watchlist(200)[0]["notified"] is False


class TestEmptyDb:
    def test_empty_watchlist(self):
        assert get_watchlist(123) == []

    def test_empty_all(self):
        assert get_all_items() == []

    def test_remove_from_empty(self):
        assert remove_item(123, "charizard") == 0
