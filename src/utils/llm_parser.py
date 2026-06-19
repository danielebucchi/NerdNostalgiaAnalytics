"""
LLM fallback parser for noisy TCG queries.

Used when the rule-based parser (`query_parser.parse_card_query`) returns low
confidence. Calls Groq with Llama 3.3 70B Versatile via the OpenAI-compatible
chat completion endpoint and JSON-object mode.

Why Groq:
- Genuinely free tier (no billing setup, EEA-friendly): 30 RPM / 6000 RPD.
- Llama 3.3 70B is more than enough for structured extraction from a fixed
  expansion catalogue.
- Sub-second latency on free tier — the tool runner / agentic loop pays
  almost nothing for a fallback parse.

The system prompt + expansion catalogue + few-shot examples are stable across
requests. Groq doesn't expose prompt caching to clients, but the body is
identical between calls so any server-side de-duplication still benefits.
"""
from __future__ import annotations

import json
import logging
import re as _re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from groq import AsyncGroq, GroqError

from src.config import settings
from src.utils.condition import (
    VG_BOX_ONLY,
    VG_BUCKETS,
    VG_CIB,
    VG_GRADED,
    VG_LOOSE,
    VG_MANUAL_ONLY,
    VG_MISSING_MANUAL,
    VG_SEALED,
    VG_UNKNOWN,
    CardCondition,
    VideogameCondition,
    detect_videogame_condition,
)
from src.utils.expansions import get_registry
from src.utils.query_parser import ParsedQuery

logger = logging.getLogger(__name__)

# Llama 3.3 70B Versatile — best free-tier model for instruction following
# and JSON output. Free tier limits at the time of writing: 30 RPM / 6000 RPD.
_MODEL = "llama-3.3-70b-versatile"

_FEW_SHOT_EXAMPLES = [
    ("charizard base set psa 10 ita holo",
     {"name": "Charizard", "set_code": "base1", "language": "ita", "variant": "holo",
      "is_graded": True, "grading_company": "PSA", "grade": 10.0, "raw_grade": None, "confidence": 1.0}),
    ("ex rubino zaffiro rayquaza shiny",
     {"name": "Rayquaza", "set_code": "ex1", "language": None, "variant": "shiny",
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.85}),
    ("151 charizard nm jp",
     {"name": "Charizard", "set_code": "sv3pt5", "language": "jpn", "variant": None,
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": "NM", "confidence": 1.0}),
    ("mewtwo bgs 9.5",
     {"name": "Mewtwo", "set_code": None, "language": None, "variant": None,
      "is_graded": True, "grading_company": "BGS", "grade": 9.5, "raw_grade": None, "confidence": 0.9}),
    ("blastoise base set leggermente giocata",
     {"name": "Blastoise", "set_code": "base1", "language": None, "variant": None,
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": "LP", "confidence": 0.95}),
    ("pikachu illustrator promo",
     {"name": "Pikachu Illustrator", "set_code": None, "language": None, "variant": "promo",
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.8}),
    ("lugia neo genesis 1st edition holo english",
     {"name": "Lugia", "set_code": "neo1", "language": "eng", "variant": "holo",
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.95}),
    ("evoluzioni paldea pikachu reverse",
     {"name": "Pikachu", "set_code": "sv2", "language": None, "variant": "reverse holo",
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.9}),
    ("chaos rising mega charizard ex full art",
     {"name": "Mega Charizard ex", "set_code": "me05", "language": None, "variant": "full art",
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.9}),
    ("destini occulti charizard gx alt art",
     {"name": "Charizard GX", "set_code": "sm115", "language": None, "variant": "alt art",
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.9}),
    ("just a random card name",
     {"name": "just a random card name", "set_code": None, "language": None, "variant": None,
      "is_graded": False, "grading_company": None, "grade": None, "raw_grade": None, "confidence": 0.2}),
    ("psa 10 charizard",
     {"name": "Charizard", "set_code": None, "language": None, "variant": None,
      "is_graded": True, "grading_company": "PSA", "grade": 10.0, "raw_grade": None, "confidence": 0.7}),
]


@lru_cache(maxsize=1)
def _build_system_prompt() -> str:
    """Cache the rendered system prompt. Invalidates only on process restart
    (which is when the expansion registry reloads from disk)."""
    registry = get_registry()
    cat_lines = ["code | name_en | name_it | aliases"]
    for exp in registry.all():
        aliases = ",".join(exp.aliases) if exp.aliases else ""
        cat_lines.append(f"{exp.code} | {exp.name_en} | {exp.name_it} | {aliases}")
    catalogue = "\n".join(cat_lines)

    examples_str = "\n\n".join(
        f"Query: {q!r}\nOutput: {json.dumps(out, ensure_ascii=False)}"
        for q, out in _FEW_SHOT_EXAMPLES
    )

    return f"""You parse noisy Pokémon TCG marketplace queries into structured JSON.

The bot's users write in mixed Italian/English (sometimes French/German/Japanese), with typos, abbreviations, set names in either language, and tokens in any order. Your job is to extract the card name, the expansion (if mentioned), the condition (graded or raw), the language, and the variant.

You MUST respond with a single JSON object using EXACTLY these keys: name, set_code, language, variant, is_graded, grading_company, grade, raw_grade, confidence. Use null for any field you cannot fill. Do not add any other keys, prose, or explanation.

Rules:
1. **set_code**: Match user text against the expansion catalogue below using EITHER name_en, name_it, or any alias — they're all equivalent. Return the exact `code` (e.g. "ex1", "sv3pt5", "me05"). Match conservatively: only fill set_code when you're confident the user referenced a known expansion. NEVER invent codes that aren't in the catalogue.
2. **Condition** — graded vs raw:
   - `is_graded=true` + `grading_company` (PSA / BGS / CGC / SGC / HGA / GMA / ACE / TAG; normalize Beckett → BGS) + `grade` (1.0–10.0 with .5 increments).
   - `raw_grade` ∈ NM/EX/GO/LP/PL/PO when the user mentioned a raw condition. Italian:
     - "perfetto stato" / "perfette condizioni" / "come nuova" / "near mint" → NM
     - "ottimo stato" / "eccellente" → EX
     - "buono stato" / "buone condizioni" / "good condition" → GO
     - "leggermente giocata" / "poco giocata" / "lightly played" → LP
     - "giocata" / "usata" / "played" → PL
     - "rovinata" / "danneggiata" / "pessime condizioni" / "poor" → PO
3. **name**: The card name with set/condition/language qualifiers stripped. Preserve relevant suffixes that are part of the card name itself (e.g. "Charizard GX", "Mewtwo V", "Pikachu Illustrator"). If the query is JUST a set name with no card, return null.
4. **language**: One of "ita" / "eng" / "jpn" / "fra" / "deu" when explicitly mentioned. Default to null.
5. **variant**: holo / reverse holo / full art / alt art / secret rare / rainbow / gold / shiny / ex / gx / v / vmax / vstar / promo, when present.
6. **confidence**: 1.0 every field unambiguous; ~0.7 some hedging; ~0.3 you guessed; ~0.0 you couldn't parse anything useful.

Examples:

{examples_str}

Expansion catalogue (use the EXACT `code` value, never invent):

{catalogue}
"""


# Valid enums — used for post-hoc validation since Groq doesn't enforce a
# schema like Gemini's response_schema does.
_VALID_LANGUAGES = {"ita", "eng", "jpn", "fra", "deu"}
_VALID_GRADING_COMPANIES = {"PSA", "BGS", "CGC", "SGC", "HGA", "GMA", "ACE", "TAG"}
_VALID_RAW_GRADES = {"NM", "EX", "GO", "LP", "PL", "PO"}


def is_configured() -> bool:
    return bool(settings.groq_api_key)


_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# In-memory TTL cache for LLM responses.
#
# Every LLM-calling function is wrapped via `_cached_call` so identical inputs
# don't re-hit Groq. The cache key is the (prompt_type, input) pair — the same
# query under different prompts (e.g. "mew #8" parsed as a card vs analyzed as
# a bundle) gets distinct entries. Returns are stored as-is (including None
# for failures, so we don't retry busted inputs in a tight loop).
#
# TTL defaults to 15 minutes — long enough to absorb a session's worth of
# /evaluate /offer /link calls on the same product, short enough that price
# state (the only thing that matters here) doesn't go stale silently.
# ─────────────────────────────────────────────────────────────────────────────
import time as _time
from typing import Awaitable, Callable, TypeVar

_T = TypeVar("_T")

# {key: (expires_at_unix_ts, value)}
_LLM_CACHE: dict[str, tuple[float, Any]] = {}
_LLM_CACHE_TTL_DEFAULT = 15 * 60  # 15 minutes
_LLM_CACHE_MAX_ENTRIES = 1024     # crude upper bound, evicted in age order


def _llm_cache_key(prompt_type: str, payload: str) -> str:
    """Build a stable cache key. Lower + strip the input so trivial whitespace /
    case differences hit the same entry."""
    return f"{prompt_type}::{payload.strip().lower()}"


def _llm_cache_get(key: str) -> tuple[bool, Any]:
    """Return (hit, value). On miss returns (False, None). Also evicts expired."""
    entry = _LLM_CACHE.get(key)
    if not entry:
        return False, None
    expires_at, value = entry
    if expires_at < _time.time():
        _LLM_CACHE.pop(key, None)
        return False, None
    return True, value


def _llm_cache_put(key: str, value: Any, ttl: int = _LLM_CACHE_TTL_DEFAULT) -> None:
    if len(_LLM_CACHE) >= _LLM_CACHE_MAX_ENTRIES:
        # Evict the 64 oldest entries when we hit the cap. Cheaper than running
        # eviction on every put.
        for k in sorted(_LLM_CACHE.keys(), key=lambda k: _LLM_CACHE[k][0])[:64]:
            _LLM_CACHE.pop(k, None)
    _LLM_CACHE[key] = (_time.time() + ttl, value)


async def _cached_call(
    prompt_type: str,
    payload: str,
    fn: Callable[[], Awaitable[_T]],
    *,
    ttl: int = _LLM_CACHE_TTL_DEFAULT,
) -> _T:
    """Hit the cache first; on miss call `fn` and stash its return value."""
    key = _llm_cache_key(prompt_type, payload)
    hit, cached = _llm_cache_get(key)
    if hit:
        logger.debug(f"LLM cache hit: {prompt_type}")
        return cached
    value = await fn()
    _llm_cache_put(key, value, ttl=ttl)
    return value


def llm_cache_stats() -> dict:
    """Used by tests and debug commands to inspect cache state."""
    now = _time.time()
    live = sum(1 for (exp, _) in _LLM_CACHE.values() if exp >= now)
    return {"entries": len(_LLM_CACHE), "live": live}


def llm_cache_clear() -> None:
    """Wipe the cache. Used by tests."""
    _LLM_CACHE.clear()


async def llm_parse_card_query(query: str) -> ParsedQuery | None:
    """Parse `query` via Groq Llama 3.3 70B. Returns None when:
    - GROQ_API_KEY isn't set,
    - the query is empty,
    - the API call fails (network/quota/safety/etc.)."""
    if not query or not query.strip():
        return None
    if not is_configured():
        return None

    async def _do_call() -> ParsedQuery | None:
        client = _get_client()
        try:
            response = await client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _build_system_prompt()},
                    {"role": "user", "content": query.strip()},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=512,
            )
        except GroqError as e:
            logger.warning(f"Groq parser failed for {query!r}: {e}")
            return None
        if not response.choices:
            logger.warning(f"Groq returned no choices for {query!r}")
            return None
        text = response.choices[0].message.content or ""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"Groq returned unparseable JSON for {query!r}: {e}; raw={text[:200]!r}")
            return None
        return _to_parsed_query(payload)

    return await _cached_call("card_query", query, _do_call)


def _sanitize(payload: dict) -> dict:
    """Coerce LLM output into the expected shape, dropping invalid enum values
    instead of carrying them forward as bogus condition / language fields."""
    out: dict[str, Any] = dict(payload)
    if out.get("language") not in _VALID_LANGUAGES:
        out["language"] = None
    if out.get("grading_company") not in _VALID_GRADING_COMPANIES:
        out["grading_company"] = None
    if out.get("raw_grade") not in _VALID_RAW_GRADES:
        out["raw_grade"] = None
    grade = out.get("grade")
    try:
        if grade is not None:
            grade = float(grade)
            if not 1.0 <= grade <= 10.0:
                grade = None
        out["grade"] = grade
    except (TypeError, ValueError):
        out["grade"] = None
    # If is_graded is true but the company/grade are missing, downgrade.
    if out.get("is_graded") and (out.get("grading_company") is None or out.get("grade") is None):
        out["is_graded"] = False
    return out


def _to_parsed_query(payload: dict) -> ParsedQuery:
    """Convert the LLM's JSON payload into a `ParsedQuery`, looking up the
    expansion by `set_code` so downstream code gets a full Expansion object."""
    payload = _sanitize(payload)
    registry = get_registry()
    expansion = registry.by_code(payload["set_code"]) if payload.get("set_code") else None

    card_cond: CardCondition | None = None
    if payload.get("is_graded"):
        card_cond = CardCondition(
            is_graded=True,
            grading_company=payload["grading_company"],
            grade=float(payload["grade"]),
        )
    elif payload.get("raw_grade"):
        card_cond = CardCondition(raw_grade=payload["raw_grade"])

    return ParsedQuery(
        name=payload.get("name") or None,
        expansion=expansion,
        card_condition=card_cond,
        language=payload.get("language") or None,
        variant=payload.get("variant") or None,
        confidence=float(payload.get("confidence", 0.0)),
    )


async def parse_with_llm_fallback(query: str, *, llm_threshold: float = 0.4) -> ParsedQuery:
    """Run the rule-based parser first; escalate to the LLM only when the
    rule-based confidence is below `llm_threshold` AND the LLM is configured."""
    from src.utils.query_parser import parse_card_query

    rule_based = parse_card_query(query)
    if rule_based.confidence >= llm_threshold or not is_configured():
        return rule_based

    llm = await llm_parse_card_query(query)
    if llm is None:
        return rule_based
    return llm if llm.confidence > rule_based.confidence else rule_based


# ─────────────────────────────────────────────────────────────────────────────
# Videogame condition LLM fallback.
#
# Used by /evaluate, /offer, /link when a Vinted/Subito/eBay title contains
# idiomatic phrasing the rule-based detector can't catch
# (e.g. "ho perso il libretto", "regalo solo il cofanetto vuoto"). The LLM
# returns one of the 7 canonical buckets — no free-form labels.
# ─────────────────────────────────────────────────────────────────────────────

_VG_FEW_SHOT_EXAMPLES = [
    ("Pokemon Smeraldo GBA con scatola ma ho perso il libretto",
     {"bucket": "Missing Manual", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 0.95}),
    ("Super Mario 64 N64 funzionante solo cartuccia",
     {"bucket": "Ungraded", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 1.0}),
    ("Zelda Majora Mask N64 PAL sigillato ancora nel cellophane",
     {"bucket": "New/Sealed", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 1.0}),
    ("Super Mario Bros NES WATA 9.8 A++ Seal",
     {"bucket": "Graded (PSA)", "is_graded": True, "grading_company": "WATA",
      "grade": 9.8, "confidence": 1.0}),
    ("regalo solo il cofanetto vuoto di Pokemon Rosso, gioco perso anni fa",
     {"bucket": "Box Only", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 0.9}),
    ("Final Fantasy VII PS1 completo di tutto, disco perfetto",
     {"bucket": "Complete in Box", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 0.95}),
    ("vendo libretto istruzioni originale Pokemon Cristallo, gioco non incluso",
     {"bucket": "Manual Only", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 0.95}),
    ("Crash Bandicoot PS1 disco un po' rigato ma funziona, no custodia",
     {"bucket": "Ungraded", "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 0.95}),
    ("Pokemon Red Gameboy VGA 85 NM+",
     {"bucket": "Graded (PSA)", "is_graded": True, "grading_company": "VGA",
      "grade": 85.0, "confidence": 1.0}),
    ("just a videogame, no info",
     {"bucket": None, "is_graded": False, "grading_company": None,
      "grade": None, "confidence": 0.0}),
]


@lru_cache(maxsize=1)
def _build_videogame_system_prompt() -> str:
    examples_str = "\n\n".join(
        f"Listing: {q!r}\nOutput: {json.dumps(out, ensure_ascii=False)}"
        for q, out in _VG_FEW_SHOT_EXAMPLES
    )
    return f"""You classify videogame marketplace listings into a single condition bucket.

Users write in mixed Italian/English/French/German with typos and informal phrasing. Extract ONLY the physical state of the item — not name, platform, region.

You MUST respond with a single JSON object using EXACTLY these keys: bucket, is_graded, grading_company, grade, confidence. Use null for any field you cannot fill. Do not add other keys, prose, or explanation.

Buckets (use the EXACT string):
- "Graded (PSA)" — professionally graded (WATA, VGA, CGC, PSA, BGS) with a numeric grade. Set is_graded=true, fill grading_company and grade.
- "New/Sealed" — factory-sealed, blister, mai aperto, scellé, OVP versiegelt.
- "Complete in Box" — game + box + manual all present; "completo", CIB, "tutto originale".
- "Missing Manual" — game + box present, manual missing. Italian: "senza manuale", "manca il libretto", "ho perso il manuale". English: "no manual", "missing manual".
- "Ungraded" — disc/cartridge only. Italian: "solo disco", "solo cartuccia", "sfuso", "senza scatola". English: "loose", "disc only", "cart only".
- "Box Only" — empty case/box, no game. Italian: "solo custodia", "scatola vuota", "regalo cofanetto". English: "box only", "case only", "empty box".
- "Manual Only" — only the manual/booklet. Italian: "solo manuale", "solo libretto", "solo istruzioni".

Rules:
1. If the listing has multiple signals, pick the WORST state mentioned (e.g. "completo senza manuale" → Missing Manual, not CIB).
2. WATA/CGC/PSA/BGS grade scale is 1.0–10.0 (often with A++ qualifier — ignore the qualifier, just take the number). VGA grade scale is 10–100.
3. confidence: 1.0 unambiguous signal; ~0.7 hedged; ~0.3 guessed; 0.0 no signal — leave bucket null.
4. If the listing has NO condition signal at all (just a title like "Pokemon Red Gameboy"), return bucket=null with confidence 0.

Examples:

{examples_str}
"""


_VG_VALID_BUCKETS = set(VG_BUCKETS)
_VG_VALID_COMPANIES = {"WATA", "VGA", "CGC", "PSA", "BGS"}


def _sanitize_vg(payload: dict) -> dict:
    out: dict[str, Any] = dict(payload)
    bucket = out.get("bucket")
    if bucket not in _VG_VALID_BUCKETS:
        out["bucket"] = None
    company = out.get("grading_company")
    if company is not None:
        company = str(company).upper()
        if company == "BECKETT":
            company = "BGS"
        if company not in _VG_VALID_COMPANIES:
            company = None
    out["grading_company"] = company
    grade = out.get("grade")
    try:
        if grade is not None:
            grade = float(grade)
            # Accept both the 1–10 scale (WATA/CGC/PSA/BGS) and 1–100 (VGA).
            if not (1.0 <= grade <= 100.0):
                grade = None
        out["grade"] = grade
    except (TypeError, ValueError):
        out["grade"] = None
    # If is_graded but grading details are missing, drop the graded flag.
    if out.get("is_graded") and (out.get("grading_company") is None or out.get("grade") is None):
        out["is_graded"] = False
        if out.get("bucket") == VG_GRADED:
            out["bucket"] = None
    return out


def _vg_payload_to_condition(payload: dict) -> VideogameCondition:
    payload = _sanitize_vg(payload)
    if payload.get("is_graded"):
        return VideogameCondition(
            bucket=VG_GRADED,
            is_graded=True,
            grading_company=payload.get("grading_company"),
            grade=payload.get("grade"),
        )
    bucket = payload.get("bucket")
    if bucket is None:
        return VideogameCondition()
    return VideogameCondition(bucket=bucket)


async def llm_parse_videogame_condition(text: str) -> tuple[VideogameCondition, float] | None:
    """Parse a videogame listing into a condition via Groq Llama 3.3 70B.

    Returns (condition, confidence) on success, or None when:
    - GROQ_API_KEY isn't set,
    - text is empty,
    - the API call or JSON parsing fails.
    """
    if not text or not text.strip():
        return None
    if not is_configured():
        return None

    async def _do_call() -> tuple[VideogameCondition, float] | None:
        client = _get_client()
        try:
            response = await client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _build_videogame_system_prompt()},
                    {"role": "user", "content": text.strip()},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=256,
            )
        except GroqError as e:
            logger.warning(f"Groq videogame parser failed for {text!r}: {e}")
            return None
        if not response.choices:
            return None
        raw = response.choices[0].message.content or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"Groq returned unparseable JSON for VG {text!r}: {e}; raw={raw[:200]!r}")
            return None
        cond = _vg_payload_to_condition(payload)
        try:
            conf = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return cond, conf

    return await _cached_call("vg_condition", text, _do_call)


async def detect_videogame_condition_with_llm_fallback(
    text: str, *, llm_threshold: float = 0.6,
) -> VideogameCondition:
    """Rule-based first, LLM fallback when the rule-based returns Unknown.

    `llm_threshold` is the minimum confidence we accept from the LLM before
    overriding the rule-based Unknown verdict — protects against the model
    hallucinating a bucket from a bare title with no condition signal.
    """
    rule_based = detect_videogame_condition(text)
    if rule_based.is_known or not is_configured():
        return rule_based

    result = await llm_parse_videogame_condition(text)
    if result is None:
        return rule_based
    cond, conf = result
    if not cond.is_known or conf < llm_threshold:
        return rule_based
    return cond


# ─────────────────────────────────────────────────────────────────────────────
# Bundle / lot detection.
#
# Vinted/Subito listings often pack multiple items together — "lotto 30 carte
# Pokemon", "PS1 + 5 giochi", "stock Pokemon vintage". The bot's per-item
# valuation pipeline is meaningless for bundles. We flag them so /evaluate
# and /link_analyzer can warn the user instead of silently producing a
# confident-but-wrong verdict.
# ─────────────────────────────────────────────────────────────────────────────

# Cheap rule-based pre-check. If NONE of these match, we skip the LLM call
# entirely (the vast majority of listings are single-item).
_BUNDLE_TRIGGERS = _re.compile(
    r"\b("
    r"lotto|lotti|stock|collezione|raccolta|"            # IT
    r"bundle|bulk|lot|collection|pack of|set of|"        # EN
    r"kit|combo|"
    r"x\s?\d{1,3}|"                                      # "x10", "x 20"
    r"\d{2,}\s+(?:carte|cards|giochi|games|figurine|"    # "50 carte"
    r"booster|sleeves|pezzi|pieces|items)"
    r")\b",
    _re.IGNORECASE,
)

# Multi-game patterns: "PS1 + 5 giochi", "console + N games"
_BUNDLE_PLUS_PATTERN = _re.compile(
    r"\+\s*(?:\d+\s+)?(?:carte|cards|giochi|games|booster|pezzi|pieces)",
    _re.IGNORECASE,
)


@dataclass
class BundleAnalysis:
    """LLM-extracted summary of a multi-item listing."""
    is_bundle: bool = False
    item_count: int | None = None
    item_type: str | None = None
    key_items: list[str] | None = None
    confidence: float = 0.0
    notes: str | None = None

    @property
    def display_summary(self) -> str:
        if not self.is_bundle:
            return ""
        bits = []
        if self.item_count:
            bits.append(f"~{self.item_count} pezzi")
        if self.item_type:
            bits.append(self.item_type)
        head = " · ".join(bits) if bits else "Bundle/lotto"
        if self.key_items:
            head += f" — top: {', '.join(self.key_items[:3])}"
        return head


_BUNDLE_FEW_SHOT = [
    ("Lotto 50 carte Pokemon vintage olografiche misto base set jungle fossil",
     {"is_bundle": True, "item_count": 50, "item_type": "carte Pokemon vintage",
      "key_items": ["Base Set", "Jungle", "Fossil"],
      "confidence": 1.0, "notes": "Lotto misto vintage TCG"}),
    ("Charizard base set holo italiano",
     {"is_bundle": False, "item_count": None, "item_type": None,
      "key_items": [], "confidence": 1.0, "notes": None}),
    ("PS1 console + 5 giochi originali tra cui Crash e FFVII",
     {"is_bundle": True, "item_count": 6, "item_type": "PS1 console + giochi",
      "key_items": ["PS1 console", "Crash", "FFVII"],
      "confidence": 0.95, "notes": "Console + bundle giochi"}),
    ("Stock Pokemon: 200+ carte comuni e rare, no holo",
     {"is_bundle": True, "item_count": 200, "item_type": "carte Pokemon comuni/rare",
      "key_items": [], "confidence": 0.95, "notes": "Stock comuni/rare"}),
    ("Pokemon Smeraldo GBA solo cartuccia funzionante",
     {"is_bundle": False, "item_count": None, "item_type": None,
      "key_items": [], "confidence": 1.0, "notes": None}),
    ("Collezione completa Magic the Gathering Alpha 4 carte + box",
     {"is_bundle": True, "item_count": 4, "item_type": "MTG Alpha cards",
      "key_items": ["Alpha"], "confidence": 0.9, "notes": "Mini-collezione MTG Alpha"}),
]


@lru_cache(maxsize=1)
def _build_bundle_system_prompt() -> str:
    examples_str = "\n\n".join(
        f"Listing: {q!r}\nOutput: {json.dumps(out, ensure_ascii=False)}"
        for q, out in _BUNDLE_FEW_SHOT
    )
    return f"""You analyze marketplace listings to determine whether they describe a single item or a bundle/lot of multiple items.

You MUST respond with a single JSON object using EXACTLY these keys: is_bundle, item_count, item_type, key_items, confidence, notes. Do not add other keys or prose.

Rules:
1. **is_bundle**: true when the listing offers multiple distinct items (cards, games, consoles+games, packs, lots). false for a single product even if accessories are included (sleeves, holder).
2. **item_count**: integer best-estimate of how many items. null if the listing doesn't say or it's "200+".
3. **item_type**: short string describing what kind of items ("Pokemon TCG cards", "PS1 games", "MTG Alpha"). null if unclear.
4. **key_items**: array (max 5) of the most notable individual items called out by name (e.g. ["Charizard", "Mewtwo"]). Empty array if none mentioned.
5. **confidence**: 1.0 unambiguous; ~0.7 hedged; 0.0 you couldn't tell.
6. **notes**: optional short Italian note (≤ 80 chars) describing what's in the lot. null if not a bundle.

Examples:

{examples_str}
"""


async def llm_analyze_bundle(text: str) -> BundleAnalysis | None:
    """Call Groq to classify a listing as bundle/single-item. Returns None on
    config / network / parse failure."""
    if not text or not text.strip():
        return None
    if not is_configured():
        return None

    async def _do_call() -> BundleAnalysis | None:
        client = _get_client()
        try:
            response = await client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _build_bundle_system_prompt()},
                    {"role": "user", "content": text.strip()},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=384,
            )
        except GroqError as e:
            logger.warning(f"Groq bundle detector failed for {text!r}: {e}")
            return None
        if not response.choices:
            return None
        raw = response.choices[0].message.content or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"Groq returned unparseable bundle JSON: {e}; raw={raw[:200]!r}")
            return None
        return _bundle_payload_to_analysis(payload)

    return await _cached_call("bundle", text, _do_call)


def _bundle_payload_to_analysis(payload: dict) -> BundleAnalysis:
    is_bundle = bool(payload.get("is_bundle"))
    item_count = payload.get("item_count")
    try:
        item_count = int(item_count) if item_count is not None else None
        if item_count is not None and (item_count < 0 or item_count > 100_000):
            item_count = None
    except (TypeError, ValueError):
        item_count = None
    key_items = payload.get("key_items") or []
    if not isinstance(key_items, list):
        key_items = []
    # Drop non-string entries, strip, cap at 5.
    key_items = [str(x).strip() for x in key_items if isinstance(x, (str, int)) and str(x).strip()][:5]
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return BundleAnalysis(
        is_bundle=is_bundle,
        item_count=item_count,
        item_type=payload.get("item_type") or None,
        key_items=key_items or None,
        confidence=max(0.0, min(1.0, confidence)),
        notes=payload.get("notes") or None,
    )


def _looks_like_bundle_pre_check(text: str) -> bool:
    """Rule-based pre-filter: does the listing have ANY bundle-ish signal?
    Used to gate the LLM call so single-item listings stay free."""
    if not text:
        return False
    return bool(_BUNDLE_TRIGGERS.search(text) or _BUNDLE_PLUS_PATTERN.search(text))


async def detect_bundle(text: str, *, llm_threshold: float = 0.6) -> BundleAnalysis:
    """Detect whether a listing is a bundle/lot.

    Pre-check (cheap): if no keyword trigger matches, return a single-item
    verdict without calling the LLM. Otherwise escalate to the LLM and apply
    a confidence floor before accepting the bundle verdict.
    """
    if not _looks_like_bundle_pre_check(text):
        return BundleAnalysis(is_bundle=False, confidence=1.0)
    if not is_configured():
        # We saw a trigger but can't confirm via LLM — flag as a possible bundle
        # so the caller can warn the user. confidence reflects uncertainty.
        return BundleAnalysis(is_bundle=True, confidence=0.4,
                              notes="Trigger keyword rilevato (LLM offline)")
    analysis = await llm_analyze_bundle(text)
    if analysis is None:
        return BundleAnalysis(is_bundle=True, confidence=0.4,
                              notes="Trigger keyword rilevato (LLM non disponibile)")
    if analysis.is_bundle and analysis.confidence < llm_threshold:
        # LLM hedged below threshold — keep as single-item rather than warn
        # the user with low-confidence noise.
        return BundleAnalysis(is_bundle=False, confidence=analysis.confidence)
    return analysis


# ─────────────────────────────────────────────────────────────────────────────
# Reddit sentiment.
#
# `calculate_hype_score` counts post volume + upvotes but can't tell positive
# excitement from a flood of "scam!" / "fake!" complaints. The LLM reads the
# top post titles and returns a sentiment ∈ [-1, +1] that multiplies the raw
# hype score: positive amplifies, strongly-negative dampens.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class HypeAnalysis:
    """Sentiment-adjusted hype reading."""
    score: int                  # 0–100, after sentiment adjustment
    raw_score: int              # 0–100, rule-based original
    sentiment: float            # -1.0 (very negative) … +1.0 (very positive)
    summary: str                # Short LLM observation (≤120 chars)
    description: str            # Emoji-prefixed verdict label

    @property
    def has_sentiment(self) -> bool:
        return self.summary != ""


@lru_cache(maxsize=1)
def _build_sentiment_system_prompt() -> str:
    return """You analyze Reddit post titles about a collectible (Pokemon TCG card, videogame, etc.) and report the community sentiment.

You MUST respond with a single JSON object using EXACTLY these keys: sentiment, summary. Do not add other keys or prose.

Rules:
1. **sentiment**: float in [-1.0, 1.0].
   - +0.7 to +1.0: strong positive — excitement, FOMO, "buy now", price predictions up.
   - +0.3 to +0.7: positive — mostly favorable discussion.
   - -0.3 to +0.3: neutral / mixed — informational, questions, no strong opinion.
   - -0.3 to -0.7: negative — complaints, dissatisfaction, price predictions down.
   - -0.7 to -1.0: strong negative — scams, fakes, controversy, "avoid".
2. **summary**: a short (≤100 char) Italian observation describing the dominant sentiment, with reasoning. Example: "Hype alto: previsioni di rialzo e nuovo set in arrivo".

You will receive a numbered list of post titles. Judge ONLY by what the titles convey — do not invent facts.
"""


async def llm_analyze_reddit_sentiment(titles: list[str]) -> tuple[float, str] | None:
    """Call Groq with a list of Reddit post titles. Returns (sentiment, summary)
    or None on config / empty input / API failure."""
    titles = [t.strip() for t in (titles or []) if t and t.strip()]
    if not titles or not is_configured():
        return None

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles[:20]))

    async def _do_call() -> tuple[float, str] | None:
        client = _get_client()
        try:
            response = await client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _build_sentiment_system_prompt()},
                    {"role": "user", "content": numbered},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=256,
            )
        except GroqError as e:
            logger.warning(f"Groq sentiment failed: {e}")
            return None
        if not response.choices:
            return None
        raw = response.choices[0].message.content or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"Groq returned unparseable sentiment JSON: {e}; raw={raw[:200]!r}")
            return None
        try:
            sentiment = float(payload.get("sentiment", 0.0))
        except (TypeError, ValueError):
            sentiment = 0.0
        sentiment = max(-1.0, min(1.0, sentiment))
        summary = str(payload.get("summary") or "").strip()
        return sentiment, summary

    # Sentiment is more stable in time than a card-price snapshot — cache it
    # for an hour rather than the 15-min default.
    return await _cached_call("sentiment", numbered, _do_call, ttl=3600)


# Multiplier curve: positive sentiment amplifies, strong-negative dampens.
def _sentiment_adjusted_score(raw_score: int, sentiment: float) -> int:
    if sentiment >= 0.5:
        return min(100, int(raw_score * 1.3))
    if sentiment >= 0.2:
        return min(100, int(raw_score * 1.1))
    if sentiment <= -0.5:
        # Loud bad news kills hype regardless of post volume
        return max(0, int(raw_score * 0.3))
    if sentiment <= -0.2:
        return max(0, int(raw_score * 0.7))
    return raw_score


def _label_for_score(score: int) -> str:
    if score >= 70:
        return "🔥🔥🔥 HYPE ALTISSIMO - Forte interesse della community"
    if score >= 50:
        return "🔥🔥 HYPE ALTO - Molto discusso online"
    if score >= 30:
        return "🔥 HYPE MODERATO - Qualche discussione attiva"
    if score >= 10:
        return "💬 HYPE BASSO - Poche menzioni"
    return "😴 NESSUN HYPE - Prodotto non discusso"


async def enrich_hype_with_sentiment(
    posts: list, raw_score: int, raw_description: str,
    *, min_posts_for_sentiment: int = 3,
) -> HypeAnalysis:
    """Wrap a `calculate_hype_score()` result with LLM sentiment.

    When there are too few posts to draw a signal, or the LLM is unavailable,
    we fall back to the raw rule-based reading with sentiment=0.
    """
    if len(posts) < min_posts_for_sentiment or not is_configured():
        return HypeAnalysis(
            score=raw_score, raw_score=raw_score, sentiment=0.0,
            summary="", description=raw_description,
        )

    titles = [getattr(p, "title", "") for p in posts]
    result = await llm_analyze_reddit_sentiment(titles)
    if result is None:
        return HypeAnalysis(
            score=raw_score, raw_score=raw_score, sentiment=0.0,
            summary="", description=raw_description,
        )

    sentiment, summary = result
    adjusted = _sentiment_adjusted_score(raw_score, sentiment)
    return HypeAnalysis(
        score=adjusted, raw_score=raw_score, sentiment=sentiment,
        summary=summary, description=_label_for_score(adjusted),
    )
