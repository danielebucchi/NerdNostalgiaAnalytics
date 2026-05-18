"""
Minimal JSON-file database. No SQLAlchemy, no async — just a JSON file.
"""
import json
import os
from pathlib import Path

DB_FILE = Path("watchlist.json")


def _load() -> dict:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text())
    return {"items": []}


def _save(data: dict):
    DB_FILE.write_text(json.dumps(data, indent=2))


def add_item(user_id: int, name: str, external_id: str, url: str, target_price: float, current_price: float | None):
    data = _load()
    # Check duplicate
    for item in data["items"]:
        if item["user_id"] == user_id and item["external_id"] == external_id:
            item["target_price"] = target_price
            _save(data)
            return "updated"

    data["items"].append({
        "user_id": user_id,
        "name": name,
        "external_id": external_id,
        "url": url,
        "target_price": target_price,
        "current_price": current_price,
        "notified": False,
    })
    _save(data)
    return "added"


def remove_item(user_id: int, name_query: str) -> int:
    data = _load()
    before = len(data["items"])
    data["items"] = [
        i for i in data["items"]
        if not (i["user_id"] == user_id and name_query.lower() in i["name"].lower())
    ]
    removed = before - len(data["items"])
    _save(data)
    return removed


def get_watchlist(user_id: int) -> list[dict]:
    data = _load()
    return [i for i in data["items"] if i["user_id"] == user_id]


def get_all_items() -> list[dict]:
    return _load()["items"]


def update_price(external_id: str, price: float):
    data = _load()
    for item in data["items"]:
        if item["external_id"] == external_id:
            item["current_price"] = price
    _save(data)


def mark_notified(user_id: int, external_id: str, notified: bool):
    data = _load()
    for item in data["items"]:
        if item["user_id"] == user_id and item["external_id"] == external_id:
            item["notified"] = notified
    _save(data)
