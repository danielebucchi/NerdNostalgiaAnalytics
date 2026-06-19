"""
Expansion registry: loads src/data/expansions.json into memory, exposes a fuzzy
matcher across name_en/name_it/code/aliases, and persists external-source codes
(cardtrader_id, cardmarket_code, tcgplayer_code, ptcgo_code) on each discovery
so the next call can skip the lookup.

The registry is a process-wide singleton. Writes are serialized through an
asyncio.Lock; reads are lock-free because we only mutate Expansion fields,
never the list structure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "expansions.json"

# External code fields we persist. Each is initially absent from the JSON and
# populated lazily by the collectors when they resolve an expansion.
EXTERNAL_FIELDS = ("cardtrader_id", "cardmarket_code", "tcgplayer_code", "ptcgo_code")


@dataclass
class Expansion:
    code: str
    game: str
    series: str
    name_en: str
    name_it: str
    release_date: str
    total_cards: int | None
    aliases: list[str] = field(default_factory=list)
    is_special: bool = False
    # External codes (lazily populated)
    cardtrader_id: int | None = None
    cardmarket_code: str | None = None
    tcgplayer_code: str | None = None
    ptcgo_code: str | None = None

    @property
    def display_name(self) -> str:
        return self.name_it if self.name_it else self.name_en

    def matches_against(self) -> list[str]:
        """Strings we'll fuzzy-match user queries against."""
        out = [self.name_en, self.name_it, self.code]
        out.extend(self.aliases)
        return [s for s in out if s]


@dataclass
class Match:
    expansion: Expansion
    score: float       # 0–100
    matched_text: str  # which field caused the hit


class ExpansionRegistry:
    """Process-wide singleton; instantiate via `get_registry()`."""

    def __init__(self, data_path: Path = DATA_PATH):
        self._path = data_path
        self._metadata: dict = {}
        self._expansions: list[Expansion] = []
        # Maps each candidate string (lowercased) → Expansion, used by rapidfuzz.
        self._candidates: dict[str, Expansion] = {}
        # Indexed by code for O(1) lookup when we already have a TCG-API id.
        self._by_code: dict[str, Expansion] = {}
        self._write_lock = asyncio.Lock()
        self._load()

    # ──────────────────────────── loading ─────────────────────────────

    def _load(self) -> None:
        with self._path.open(encoding="utf-8") as f:
            data = json.load(f)
        self._metadata = data.get("_metadata", {})
        self._expansions = []
        self._candidates = {}
        self._by_code = {}
        for raw in data.get("expansions", []):
            exp = Expansion(
                code=raw["code"],
                game=raw["game"],
                series=raw["series"],
                name_en=raw["name_en"],
                name_it=raw.get("name_it") or raw["name_en"],
                release_date=raw["release_date"],
                total_cards=raw.get("total_cards"),
                aliases=list(raw.get("aliases", [])),
                is_special=bool(raw.get("is_special", False)),
                cardtrader_id=raw.get("cardtrader_id"),
                cardmarket_code=raw.get("cardmarket_code"),
                tcgplayer_code=raw.get("tcgplayer_code"),
                ptcgo_code=raw.get("ptcgo_code"),
            )
            self._expansions.append(exp)
            self._by_code[exp.code.lower()] = exp
            for candidate in exp.matches_against():
                self._candidates[candidate.lower()] = exp

    # ─────────────────────────── lookups ──────────────────────────────

    def all(self) -> list[Expansion]:
        return list(self._expansions)

    def by_code(self, code: str) -> Expansion | None:
        return self._by_code.get(code.lower()) if code else None

    def find(self, query: str, *, game: str | None = None, threshold: int = 75) -> Match | None:
        """Fuzzy lookup. Returns the best match above `threshold`, or None."""
        if not query:
            return None

        # Exact code hit beats everything.
        exact = self.by_code(query.strip())
        if exact and (game is None or exact.game == game):
            return Match(exact, 100.0, exact.code)

        candidates = {
            text: exp for text, exp in self._candidates.items()
            if game is None or exp.game == game
        }
        if not candidates:
            return None

        result = process.extractOne(
            query.lower(),
            candidates.keys(),
            scorer=fuzz.WRatio,
            score_cutoff=threshold,
        )
        if result is None:
            return None
        matched_text, score, _ = result
        return Match(candidates[matched_text], float(score), matched_text)

    def find_in_text(self, text: str, *, game: str | None = None, threshold: int = 85) -> Match | None:
        """Scan a freetext blob (listing title, description) for any expansion
        mention. Uses a higher threshold because false positives are costly."""
        if not text:
            return None
        lower = text.lower()

        # Substring-first: long names embedded in a title are unambiguous.
        # Iterate from longest candidate down to avoid 'XY' matching inside
        # 'XY Furious Fists' etc.
        sorted_candidates = sorted(
            self._candidates.items(), key=lambda kv: -len(kv[0]),
        )
        for cand, exp in sorted_candidates:
            if game is not None and exp.game != game:
                continue
            # Require at least 4 chars to avoid 'xy' matching 'oxygen' etc.
            if len(cand) < 4:
                continue
            if cand in lower:
                return Match(exp, 100.0, cand)

        # Fall back to fuzzy across the whole text — useful for typos.
        return self.find(text, game=game, threshold=threshold)

    # ─────────────────────── external code upsert ─────────────────────

    async def record_external_code(self, code: str, field_name: str, value) -> bool:
        """Persist an external code for `code`. No-op (returns False) when:
        - the expansion isn't in the registry,
        - `field_name` isn't a known external field,
        - the value is already cached (idempotency).
        Returns True if we wrote a new value to disk."""
        if field_name not in EXTERNAL_FIELDS:
            logger.warning(f"record_external_code: unknown field {field_name!r}")
            return False
        exp = self.by_code(code)
        if exp is None:
            return False
        current = getattr(exp, field_name)
        if current == value or value in (None, ""):
            return False

        async with self._write_lock:
            # Re-read inside the lock to avoid TOCTOU on the file
            current = getattr(exp, field_name)
            if current == value:
                return False
            setattr(exp, field_name, value)
            try:
                self._flush_to_disk()
            except Exception as e:
                logger.error(f"Failed to persist {field_name} for {code}: {e}")
                # Keep the in-memory update even if disk write fails — at worst
                # we re-discover next process boot.
                return True
            logger.info(f"expansions.json: {code}.{field_name} ← {value!r}")
            return True

    def _flush_to_disk(self) -> None:
        """Serialize the in-memory registry back to the JSON file atomically."""
        payload = {
            "_metadata": self._metadata,
            "expansions": [self._serialize(exp) for exp in self._expansions],
        }
        # Atomic write: temp file + rename. Survives crashes mid-write.
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._path)

    @staticmethod
    def _serialize(exp: Expansion) -> dict:
        out: dict = {
            "code": exp.code,
            "game": exp.game,
            "series": exp.series,
            "name_en": exp.name_en,
            "name_it": exp.name_it,
            "release_date": exp.release_date,
            "total_cards": exp.total_cards,
            "aliases": exp.aliases,
        }
        if exp.is_special:
            out["is_special"] = True
        # Only emit external fields that have a value, to keep diffs minimal.
        for fname in EXTERNAL_FIELDS:
            v = getattr(exp, fname)
            if v is not None:
                out[fname] = v
        return out


_registry: ExpansionRegistry | None = None


def get_registry() -> ExpansionRegistry:
    """Lazy-initialise the process-wide registry."""
    global _registry
    if _registry is None:
        _registry = ExpansionRegistry()
    return _registry


def reset_registry_for_tests(path: Path | None = None) -> ExpansionRegistry:
    """Force-reload the registry. Tests use this to point at a tmp JSON copy."""
    global _registry
    _registry = ExpansionRegistry(path or DATA_PATH)
    return _registry
