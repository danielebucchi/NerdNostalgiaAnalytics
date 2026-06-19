"""
Rule-based parser for noisy TCG queries.

Takes a user query ("/search ex rubino zaffiro charizard psa10 ita") or a
listing title ("Charizard Base Set 1999 PSA 10 Holo English") and extracts:
- expansion (via ExpansionRegistry fuzzy match)
- card condition (via detect_card_condition)
- language (it/en/jp/fr/de)
- variant (holo, reverse, full art, secret rare, ex/gx/v/vmax/vstar, promo)
- residual card name

Composes cleanly with the LLM fallback in llm_parser.py: if `confidence` is
low or `name` is empty, callers escalate to the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.utils.condition import CardCondition, detect_card_condition
from src.utils.expansions import Expansion, get_registry


@dataclass
class ParsedQuery:
    name: str | None = None
    expansion: Expansion | None = None
    card_condition: CardCondition | None = None
    language: str | None = None   # ita / eng / jpn / fra / deu
    variant: str | None = None
    confidence: float = 0.0        # 0–1, used by callers to decide whether to escalate

    @property
    def set_code(self) -> str | None:
        return self.expansion.code if self.expansion else None

    @property
    def set_name(self) -> str | None:
        return self.expansion.name_en if self.expansion else None

    @property
    def is_pure_set_query(self) -> bool:
        """User typed only an expansion name with no card name. Used by /search
        to switch from card-lookup to set-summary."""
        return self.expansion is not None and not self.name


# Language markers (multi-language). Word-boundary matched.
_LANGUAGE_PATTERNS: list[tuple[str, list[str]]] = [
    ("ita", ["italiano", "italian", "italiana", "italiane", "ita", "it"]),
    ("eng", ["english", "inglese", "eng", "en"]),
    ("jpn", ["japanese", "jp", "jap", "giapponese", "jpn"]),
    ("fra", ["french", "francese", "français", "francais", "fra", "fr"]),
    ("deu", ["german", "tedesco", "deutsch", "deu", "de", "ger"]),
]

# Variant markers. Longer/more-specific first so "full art" beats "art".
_VARIANT_PATTERNS: list[tuple[str, list[str]]] = [
    ("alt art", ["alternate art", "alt art", "alt-art"]),
    ("full art", ["full art", "full-art"]),
    ("secret rare", ["secret rare"]),
    ("rainbow rare", ["rainbow rare", "rainbow"]),
    ("gold", ["gold rare", "gold"]),
    # "non holo" / "no holo" must beat plain "holo" — they explicitly negate it.
    ("non holo", ["non holo", "no holo", "no-holo", "non-holo", "non holographic"]),
    ("reverse holo", ["reverse holo", "reverse-holo", "reverse"]),
    ("holo", ["holo", "holographic", "holofoil"]),
    ("shiny", ["shiny", "luccicante"]),
    ("vmax", ["vmax"]),
    ("vstar", ["vstar", "v-star"]),
    ("v", [" v "]),  # padded with spaces to avoid matching inside other tokens
    ("ex", [" ex "]),
    ("gx", [" gx "]),
    ("promo", ["promo", "promotional"]),
]


def _strip_tokens(text: str, tokens: list[str]) -> str:
    """Remove each token from `text` (case-insensitive, word-boundary)."""
    for tok in tokens:
        if tok.startswith(" ") and tok.endswith(" "):
            # Pre/post-padded — only match between spaces or at boundaries.
            text = re.sub(rf"(?:^|\s){re.escape(tok.strip())}(?:\s|$)", " ", text, flags=re.IGNORECASE)
        else:
            text = re.sub(rf"\b{re.escape(tok)}\b", " ", text, flags=re.IGNORECASE)
    return text


def _extract_language(text: str) -> tuple[str | None, str]:
    """Find a language marker. Return (code, text_with_marker_removed)."""
    for code, words in _LANGUAGE_PATTERNS:
        for w in words:
            if re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE):
                return code, _strip_tokens(text, [w])
    return None, text


def _extract_variant(text: str) -> tuple[str | None, str]:
    """Find a variant marker. Return (variant, text_with_marker_removed)."""
    padded = f" {text} "
    for variant, patterns in _VARIANT_PATTERNS:
        for p in patterns:
            if re.search(rf"\b{re.escape(p.strip())}\b", padded, re.IGNORECASE):
                cleaned = _strip_tokens(text, [p])
                return variant, cleaned
    return None, text


def _strip_expansion_words(text: str, expansion: Expansion) -> str:
    """Remove the expansion's name (IT and EN) and aliases from `text` so the
    residual contains only the card name + any unrecognised tokens."""
    tokens_to_strip = [expansion.name_en, expansion.name_it, *expansion.aliases]
    # Sort longest-first so "ex ruby & sapphire" is stripped before "ex".
    tokens_to_strip.sort(key=len, reverse=True)
    for tok in tokens_to_strip:
        if not tok or len(tok) < 3:
            continue
        text = re.sub(re.escape(tok), " ", text, flags=re.IGNORECASE)
    return text


def _strip_condition(text: str, cc: CardCondition) -> str:
    """Remove condition tokens from `text` (graded `PSA 10`, raw `NM`, etc.)."""
    if cc.is_graded and cc.grading_company and cc.grade is not None:
        # Match "PSA 10", "PSA10", "psa10", "PSA-10", etc.
        pattern = rf"\b{re.escape(cc.grading_company)}\s*[-:]?\s*{re.escape(f'{cc.grade:g}')}\b"
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        # Strip leftover company name
        text = re.sub(rf"\b{re.escape(cc.grading_company)}\b", " ", text, flags=re.IGNORECASE)
    elif cc.raw_grade:
        # Strip both the abbreviation and the canonical phrase. Best-effort.
        from src.utils.condition import RAW_GRADE_LABEL
        text = re.sub(rf"\b{re.escape(cc.raw_grade)}\b", " ", text)
        label = RAW_GRADE_LABEL.get(cc.raw_grade, "")
        if label:
            text = re.sub(re.escape(label), " ", text, flags=re.IGNORECASE)
    return text


def _cleanup(text: str) -> str:
    """Collapse whitespace + punctuation noise so the residual name is clean."""
    text = re.sub(r"[\(\)\[\]/,;:|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_card_query(text: str) -> ParsedQuery:
    """Parse a TCG query into structured fields. Best-effort, no LLM.

    Confidence is high (≥ 0.7) when we found a recognised expansion OR a card
    condition; it stays low when the query is just an unknown name."""
    if not text or not text.strip():
        return ParsedQuery()

    working = text.strip()

    # 1. Expansion — use the substring-preferred find_in_text so a partial
    #    match doesn't accidentally swallow the card name.
    registry = get_registry()
    match = registry.find_in_text(working)
    expansion = match.expansion if match else None
    if expansion:
        working = _strip_expansion_words(working, expansion)

    # 2. Card condition. Detected from the ORIGINAL text (some tokens like
    #    "perfetto stato" might already have been swallowed by an expansion
    #    name strip, though that's unusual).
    cc = detect_card_condition(text)
    if cc.is_known:
        working = _strip_condition(working, cc)

    # 3. Language
    language, working = _extract_language(working)

    # 4. Variant
    variant, working = _extract_variant(working)

    # 5. Whatever's left is the card name (or empty if the query was just a set).
    residual = _cleanup(working)
    name = residual if residual else None

    # 6. Confidence heuristic. The caller uses this to decide whether to invoke
    #    the LLM fallback.
    confidence = 0.0
    if expansion is not None:
        confidence += 0.4
    if cc.is_known:
        confidence += 0.3
    if language:
        confidence += 0.1
    if variant:
        confidence += 0.1
    if name:
        confidence += 0.1
    confidence = min(1.0, confidence)

    return ParsedQuery(
        name=name,
        expansion=expansion,
        card_condition=cc if cc.is_known else None,
        language=language,
        variant=variant,
        confidence=confidence,
    )
