"""One-shot script to enrich `src/data/expansions.json` from pokemontcg.io.

Fetches the full set catalogue from https://api.pokemontcg.io/v2/sets and
merges any sets we don't already have into our local JSON. Existing entries
are left alone (we have hand-tuned Italian names + aliases that the API
doesn't know about).

Run via:
    python scripts/sync_expansions.py

Or inside Docker:
    docker exec nerd-nostalgia-bot python scripts/sync_expansions.py

Idempotent — re-running only adds new sets. Writes the file atomically.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EXPANSIONS_PATH = ROOT / "src" / "data" / "expansions.json"

API_URL = "https://api.pokemontcg.io/v2/sets"
PAGE_SIZE = 250


def _load_existing() -> dict:
    with EXPANSIONS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(payload: dict) -> None:
    tmp = EXPANSIONS_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EXPANSIONS_PATH)


async def _fetch_all_sets() -> list[dict]:
    """Paginate through pokemontcg.io until we have every set."""
    headers = {}
    # The API works without a key but rate-limits more aggressively.
    api_key = os.getenv("POKEMONTCG_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key

    sets: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        while True:
            params = {"page": page, "pageSize": PAGE_SIZE}
            r = await client.get(API_URL, params=params)
            r.raise_for_status()
            data = r.json()
            batch = data.get("data", [])
            if not batch:
                break
            sets.extend(batch)
            total_count = data.get("totalCount", len(sets))
            if len(sets) >= total_count:
                break
            page += 1
    return sets


def _convert(api_set: dict) -> dict:
    """Convert a pokemontcg.io set entry into our local schema."""
    code = (api_set.get("id") or "").strip()
    name = (api_set.get("name") or "").strip()
    series = (api_set.get("series") or "").strip()
    release_date = (api_set.get("releaseDate") or "").replace("/", "-")
    total = api_set.get("printedTotal") or api_set.get("total")
    # We don't know the Italian translation from the API — copy English so the
    # fuzzy matcher still works. A human can override later.
    return {
        "code": code,
        "game": "pokemon",
        "series": series,
        "name_en": name,
        "name_it": name,
        "release_date": release_date,
        "total_cards": total,
        "aliases": [name.lower()] if name else [],
    }


def _merge(local: dict, api_sets: list[dict]) -> tuple[dict, int, int]:
    """Merge API sets into local catalogue. Returns (updated, added, skipped)."""
    existing_codes = {e["code"] for e in local["expansions"]}
    existing_names = {e["name_en"].lower() for e in local["expansions"]}

    added = 0
    skipped = 0
    new_entries: list[dict] = []
    for s in api_sets:
        entry = _convert(s)
        if not entry["code"]:
            skipped += 1
            continue
        if entry["code"] in existing_codes:
            skipped += 1
            continue
        # Also dedupe by lowercase name (some sets have non-API codes locally)
        if entry["name_en"].lower() in existing_names:
            skipped += 1
            continue
        new_entries.append(entry)
        existing_codes.add(entry["code"])
        existing_names.add(entry["name_en"].lower())
        added += 1

    # Append new entries; preserve hand-curated order at the front.
    local["expansions"].extend(new_entries)

    # Update metadata
    local.setdefault("_metadata", {})
    local["_metadata"]["last_synced_from_api"] = "pokemontcg.io"
    local["_metadata"]["last_synced_count"] = len(local["expansions"])

    return local, added, skipped


async def main() -> int:
    print(f"📚 Caricamento catalogo locale da {EXPANSIONS_PATH}...")
    local = _load_existing()
    print(f"   {len(local['expansions'])} set esistenti")

    print(f"🌐 Fetch da {API_URL}...")
    try:
        api_sets = await _fetch_all_sets()
    except httpx.HTTPError as e:
        print(f"❌ Errore API: {e}")
        return 1
    print(f"   {len(api_sets)} set ricevuti dall'API")

    merged, added, skipped = _merge(local, api_sets)
    print(f"➕ {added} set nuovi aggiunti, {skipped} già presenti")

    if added == 0:
        print("✅ Niente da aggiornare.")
        return 0

    _atomic_write(merged)
    print(f"✅ Catalogo aggiornato: ora {len(merged['expansions'])} set totali")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
