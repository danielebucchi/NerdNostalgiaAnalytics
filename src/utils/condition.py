"""
Detect product condition from listing title/description.

Two scopes:
- Videogames / generic products → 7 buckets: Graded / New-Sealed / Complete in Box /
  Missing Manual / Ungraded (disc/cart only) / Box Only / Manual Only.
  Use `detect_videogame_condition()` for the rich `VideogameCondition` object or the
  legacy `detect_condition()` for the string label (still used by older call sites).
- `detect_card_condition()`: trading cards → either graded (company + 1.0–10.0)
   or raw on the scale PO < PL < LP < GO < EX < NM.
"""
import re
from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────────────────────
# Videogame conditions
# ─────────────────────────────────────────────────────────────────────────────

# Canonical bucket labels (kept in sync with PriceCharting's `name_map`).
VG_GRADED = "Graded (PSA)"
VG_SEALED = "New/Sealed"
VG_CIB = "Complete in Box"
VG_MISSING_MANUAL = "Missing Manual"
VG_LOOSE = "Ungraded"
VG_BOX_ONLY = "Box Only"
VG_MANUAL_ONLY = "Manual Only"
VG_UNKNOWN = "Unknown"

VG_BUCKETS = (
    VG_GRADED, VG_SEALED, VG_CIB, VG_MISSING_MANUAL,
    VG_LOOSE, VG_BOX_ONLY, VG_MANUAL_ONLY,
)

# Quality ordering (higher = more complete / more valuable, broadly).
VG_QUALITY_SCORE = {
    VG_GRADED: 100,
    VG_SEALED: 90,
    VG_CIB: 70,
    VG_MISSING_MANUAL: 55,
    VG_LOOSE: 40,
    VG_BOX_ONLY: 25,
    VG_MANUAL_ONLY: 20,
    VG_UNKNOWN: 0,
}

# Multi-word "missing manual" first — these are CIB-like states where the manual
# is explicitly absent. They must beat the generic CIB matcher.
MISSING_MANUAL_KEYWORDS = [
    # Italian
    "senza manuale", "manca il manuale", "mancante manuale",
    "mancante del manuale", "no manuale", "senza libretto",
    # English
    "no manual", "missing manual", "without manual", "manual missing",
    "without booklet",
    # French
    "sans notice", "sans manuel",
    # German
    "ohne anleitung", "ohne handbuch",
]

# "Solo custodia" / "case only" / "box only" — box without the game inside.
# Two-word phrases listed first so they win over a bare "scatola"/"box".
BOX_ONLY_KEYWORDS = [
    # Italian
    "solo custodia", "solo scatola", "solo box", "solo confezione",
    "scatola vuota", "custodia vuota", "box vuoto", "box vuota",
    # English
    "box only", "case only", "empty box", "empty case",
    # French
    "boite seule", "boîte seule", "boite vide", "boîte vide",
    # German
    "nur ovp", "nur box", "leere ovp", "ohne spiel",
]

# Manual-only listings (rare but they exist on eBay/Subito).
MANUAL_ONLY_KEYWORDS = [
    # Italian
    "solo manuale", "solo libretto", "solo istruzioni",
    # English
    "manual only", "booklet only", "instructions only",
    # French
    "notice seule", "manuel seul",
    # German
    "nur anleitung", "nur handbuch",
]

# "Solo disco / cartuccia / gioco" — disc/cartridge without box.
LOOSE_KEYWORDS = [
    # Italian
    "solo cartuccia", "solo disco", "solo gioco", "senza scatola",
    "senza custodia", "senza box", "senza confezione",
    "no box", "no scatola", "loose", "sfuso", "cartuccia",
    "solo carta",
    # English
    "cart only", "cartridge only", "loose", "no box", "no case",
    "disc only", "game only", "card only", "disk only",
    # French
    "cartouche seule", "sans boite", "sans boîte", "cartouche",
    # German
    "nur modul", "nur disc", "nur disk", "ohne ovp", "lose",
]

CIB_KEYWORDS = [
    # Italian
    "completo", "con scatola", "con custodia", "con manuale",
    "scatola originale", "boxato", "in scatola",
    # English
    "complete", "cib", "complete in box", "with box", "with manual",
    "boxed", "with case",
    # French
    "complet", "avec boite", "avec boîte",
    # German
    "komplett", "mit ovp", "ovp",
]

SEALED_KEYWORDS = [
    # Italian
    "sigillato", "factory sealed", "blister",
    "mai aperto", "ancora sigillato", "cellophane",
    # English
    "sealed", "factory sealed", "mint sealed", "unopened",
    "brand new", "shrink wrap",
    # French
    "scellé", "neuf sous blister", "sous blister",
    # German
    "versiegelt", "originalverpackt", "neu ovp",
]

# Strict word-boundary keywords — avoid e.g. "nuovo" matching "nuovamente".
SEALED_KEYWORDS_STRICT = [
    "nuovo di zecca", "come nuovo",
]

# Videogame grading companies (WATA, VGA, CGC for games) + Pokemon-style PSA/BGS
# which also occasionally appears on game listings.
VG_GRADING_COMPANIES = ("WATA", "VGA", "CGC", "PSA", "BGS", "BECKETT")

# "WATA 9.8", "VGA 85", "CGC 9.6 A++". Accepts the optional "A+" / "A++" qualifier.
_VG_GRADED_PATTERN = re.compile(
    r"\b(" + "|".join(VG_GRADING_COMPANIES) + r")"
    r"\s*[-:]?\s*"
    r"(\d{1,3}(?:[.,]\d)?)"
    r"(?:\s*A\+{1,2})?",
    re.IGNORECASE,
)

# Plain `graded` keyword (no company) — last-resort signal.
GRADED_FALLBACK_KEYWORDS = ("graded",)


@dataclass
class VideogameCondition:
    """Detected videogame condition.

    Either `is_graded=True` with `grading_company` + optional `grade`, or a bucket
    label in `bucket` (one of VG_SEALED / VG_CIB / VG_MISSING_MANUAL / VG_LOOSE /
    VG_BOX_ONLY / VG_MANUAL_ONLY). `is_known` reports whether any signal matched.
    """
    bucket: str = VG_UNKNOWN
    is_graded: bool = False
    grade: float | None = None
    grading_company: str | None = None

    @property
    def is_known(self) -> bool:
        return self.is_graded or self.bucket != VG_UNKNOWN

    @property
    def label(self) -> str:
        """PriceCharting-compatible bucket label."""
        return VG_GRADED if self.is_graded else self.bucket

    @property
    def display(self) -> str:
        if self.is_graded:
            if self.grading_company and self.grade is not None:
                return f"{self.grading_company} {self.grade:g}"
            if self.grading_company:
                return f"Graded ({self.grading_company})"
            return "Graded"
        return self.bucket

    @property
    def quality_score(self) -> float:
        return float(VG_QUALITY_SCORE.get(self.label, 0))


def detect_videogame_condition(text: str) -> VideogameCondition:
    """Detect a videogame condition from a listing title/description.

    Checks are ordered from most-specific to least-specific so e.g.
    "senza manuale" wins over "completo" and "solo custodia" wins over
    "scatola originale".
    """
    if not text:
        return VideogameCondition()

    lower = text.lower()

    # 1. Graded — most specific. Match a known grading company + numeric grade.
    m = _VG_GRADED_PATTERN.search(text)
    if m:
        company = m.group(1).upper()
        if company == "BECKETT":
            company = "BGS"
        grade_str = m.group(2).replace(",", ".")
        try:
            grade = float(grade_str)
        except ValueError:
            grade = None
        # WATA/CGC/PSA/BGS use 1–10; VGA uses 1–100. Accept both ranges.
        if grade is not None and (1.0 <= grade <= 10.0 or 10.0 < grade <= 100.0):
            return VideogameCondition(
                bucket=VG_GRADED, is_graded=True,
                grading_company=company, grade=grade,
            )

    # 2. "Graded" without company — fall back to graded bucket without details.
    for kw in GRADED_FALLBACK_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', lower):
            return VideogameCondition(bucket=VG_GRADED, is_graded=True)

    # 3. Sealed (multi-word substring + strict word-boundary keywords).
    for kw in SEALED_KEYWORDS:
        if kw in lower:
            return VideogameCondition(bucket=VG_SEALED)
    for kw in SEALED_KEYWORDS_STRICT:
        if re.search(r'\b' + re.escape(kw) + r'\b', lower):
            return VideogameCondition(bucket=VG_SEALED)

    # 4. Box Only — checked before LOOSE/CIB so "scatola vuota" wins over "scatola".
    for kw in BOX_ONLY_KEYWORDS:
        if kw in lower:
            return VideogameCondition(bucket=VG_BOX_ONLY)

    # 5. Manual Only — checked before LOOSE so "solo manuale" doesn't get
    #    misread as a LOOSE keyword (none of them currently say "manuale" alone
    #    but keep the order strict for safety).
    for kw in MANUAL_ONLY_KEYWORDS:
        if kw in lower:
            return VideogameCondition(bucket=VG_MANUAL_ONLY)

    # 6. Missing Manual — CIB-like state minus the manual. Must beat LOOSE
    #    ("senza manuale" contains "senza" but it's NOT a loose listing) and
    #    must beat CIB ("completo" → ignored once we see "senza manuale").
    for kw in MISSING_MANUAL_KEYWORDS:
        if kw in lower:
            return VideogameCondition(bucket=VG_MISSING_MANUAL)

    # 7. LOOSE before CIB — "senza scatola" must override "scatola".
    for kw in LOOSE_KEYWORDS:
        if kw in lower:
            return VideogameCondition(bucket=VG_LOOSE)

    # 8. CIB — fallback positive signal (box / manual / complete mentioned).
    for kw in CIB_KEYWORDS:
        if kw in lower:
            return VideogameCondition(bucket=VG_CIB)

    return VideogameCondition()


def detect_condition(text: str) -> str:
    """Back-compat string-returning detector.

    Returns one of the canonical bucket labels — same set as before plus the
    new 'Missing Manual', 'Box Only' and 'Manual Only' labels.
    """
    return detect_videogame_condition(text).label


def get_condition_price(
    conditions: dict[str, list], detected_condition: str
) -> tuple[float | None, str]:
    """
    Get the appropriate price for a detected condition.
    Returns (price, condition_used).
    Falls back to Ungraded if detected condition not available.
    """
    # Direct match
    if detected_condition in conditions and conditions[detected_condition]:
        return conditions[detected_condition][-1].price, detected_condition

    # Fallback order based on detected condition. Newer buckets (Missing Manual,
    # Box Only, Manual Only) prefer the closest equivalent before degrading to
    # Ungraded as the last resort.
    fallback_map = {
        "Unknown": ["Ungraded", "Complete in Box", "New/Sealed"],
        "Ungraded": ["Ungraded", "Complete in Box"],
        "Complete in Box": ["Complete in Box", "Ungraded"],
        "Missing Manual": ["Complete in Box", "Ungraded"],
        "New/Sealed": ["New/Sealed", "Complete in Box"],
        "Graded (PSA)": ["Graded (PSA)", "New/Sealed"],
        "Box Only": ["Box Only", "Ungraded"],
        "Manual Only": ["Manual Only", "Ungraded"],
    }

    for fallback in fallback_map.get(detected_condition, ["Ungraded"]):
        if fallback in conditions and conditions[fallback]:
            return conditions[fallback][-1].price, fallback

    # Last resort: any available condition
    for name, prices in conditions.items():
        if prices:
            return prices[-1].price, name

    return None, "Unknown"


CONDITION_EMOJI = {
    "Ungraded": "💿",          # disc / cart only
    "Complete in Box": "📦✅",
    "Missing Manual": "📦📕❌",  # box + disc, manual missing
    "New/Sealed": "🆕",
    "Graded (PSA)": "💎",
    "Box Only": "📦",            # empty case
    "Manual Only": "📕",
    "Unknown": "❓",
}


# ─────────────────────────────────────────────────────────────────────────────
# Trading card conditions: graded (PSA/BGS/CGC/...) or raw (PO/PL/LP/GO/EX/NM).
# ─────────────────────────────────────────────────────────────────────────────

RAW_GRADES = ("NM", "EX", "GO", "LP", "PL", "PO")

# Quality ranking (higher = better) for sorting/comparison.
RAW_GRADE_SCORE = {"NM": 60, "EX": 50, "GO": 40, "LP": 30, "PL": 20, "PO": 10}

RAW_GRADE_LABEL = {
    "NM": "Near Mint",
    "EX": "Excellent",
    "GO": "Good",
    "LP": "Light Played",
    "PL": "Played",
    "PO": "Poor",
}

GRADING_COMPANIES = ("PSA", "BGS", "CGC", "SGC", "HGA", "GMA", "ACE", "TAG", "Beckett")

# Matches "PSA 10", "PSA10", "psa 9.5", "BGS 9,5", "CGC 8.5", "Beckett 9".
# No \b between company and grade: "PSA10" has no word-boundary there
# (letters→digits is word→word). Word-boundary on the left side prevents
# "EPSA 10" from matching.
_GRADED_PATTERN = re.compile(
    r"\b(" + "|".join(GRADING_COMPANIES) + r")"
    r"\s*(?:graded\s*)?[-:]?\s*"
    r"(\d{1,2}(?:[.,]\d)?)",
    re.IGNORECASE,
)

# Full multi-word phrases — ordered so more specific matches win.
# (LP must be checked before PL; "good condition" before bare "good", etc.)
_RAW_PHRASES: list[tuple[str, list[str]]] = [
    ("NM", [
        "near mint", "near-mint", "near/mint", "mint condition",
        "perfetta condizione", "perfette condizioni", "perfetto stato",
        "stato perfetto",
    ]),
    ("LP", [
        "lightly played", "light played", "lightly-played", "light-played",
        "leggermente giocata", "lievemente giocata", "poco giocata", "poco usata",
    ]),
    ("EX", [
        "near excellent", "excellent condition", "excellent",
        "eccellente", "ottimo stato", "ottime condizioni",
    ]),
    ("PO", [
        "poor condition", "heavily damaged", "very damaged",
        "pessime condizioni", "rovinata", "danneggiata", "molto rovinata",
    ]),
    ("GO", [
        "good condition", "buono stato", "buone condizioni",
    ]),
    ("PL", [
        "moderately played", "played condition", "played",
        "giocata", "usata",
    ]),
]

# Abbreviation map (matched only inside a delimiter context to avoid false positives).
_ABBREV_MAP = {
    "NM/M": "NM", "NM-M": "NM", "NM/MT": "NM", "NM": "NM", "M/NM": "NM", "MT": "NM",
    "EX/NM": "EX", "EX-NM": "EX", "EX+": "EX", "EX": "EX", "EXC": "EX",
    "GD": "GO",
    "LP": "LP", "SP": "LP",  # SP = Slightly Played ≈ LP
    "MP": "PL", "PL": "PL",  # MP = Moderately Played → PL
    "HP": "PO", "PO": "PO",  # HP = Heavily Played → PO
}

# Abbreviations are checked only when surrounded by clear delimiters,
# so "MP" in "AMPLIFIER" never matches.
_ABBREV_PATTERN = re.compile(
    r"(?:^|[\s\-\(\[\|/:,])"
    r"(NM/MT|NM/M|NM-M|M/NM|EX/NM|EX-NM|EX\+|NM|MT|EX|EXC|LP|SP|MP|PL|HP|PO|GD)"
    r"(?:[\s\-\)\]\|/:,.]|$)"
)


@dataclass
class CardCondition:
    """Detected card condition.

    Either `is_graded=True` with `grading_company` + `grade` (1.0–10.0),
    or raw with `raw_grade` ∈ RAW_GRADES.
    """
    is_graded: bool = False
    grade: float | None = None
    grading_company: str | None = None
    raw_grade: str | None = None

    @property
    def is_known(self) -> bool:
        return self.is_graded or self.raw_grade is not None

    @property
    def display(self) -> str:
        if self.is_graded and self.grading_company and self.grade is not None:
            grade_str = f"{self.grade:g}"  # 10 → "10", 9.5 → "9.5"
            return f"{self.grading_company} {grade_str}"
        if self.raw_grade:
            return f"Raw {self.raw_grade} ({RAW_GRADE_LABEL[self.raw_grade]})"
        return "Unknown"

    @property
    def quality_score(self) -> float:
        """Higher = better quality. Graded scores 10–100, raw 10–60."""
        if self.is_graded and self.grade is not None:
            return self.grade * 10
        if self.raw_grade:
            return RAW_GRADE_SCORE[self.raw_grade]
        return 0.0


# Canonical labels used by Cardmarket / CardTrader / TCGPlayer for raw conditions.
# Mapped directly to our 6-tier scale (Mint is collapsed onto NM — we don't keep them
# separate, NM is the top of our scale).
_CANONICAL_LABEL_MAP = {
    "mint": "NM",
    "near mint": "NM",
    "excellent": "EX",
    "good": "GO",
    "light played": "LP",
    "lightly played": "LP",
    "played": "PL",
    "moderately played": "PL",
    "poor": "PO",
    "heavily played": "PO",
    "damaged": "PO",
}


def card_condition_from_label(label: str | None) -> CardCondition:
    """Map a canonical Cardmarket/CardTrader/TCGPlayer condition string to a
    `CardCondition`. Falls back to the freetext detector for non-canonical input."""
    if not label:
        return CardCondition()
    direct = _CANONICAL_LABEL_MAP.get(label.strip().lower())
    if direct:
        return CardCondition(raw_grade=direct)
    return detect_card_condition(label)


# Map a CardCondition to the PriceCharting condition bucket. Cards on
# PriceCharting only distinguish "Ungraded" vs "Graded (PSA)".
def card_condition_to_pc_bucket(cc: CardCondition) -> str:
    if cc.is_graded:
        return "Graded (PSA)"
    return "Ungraded"


# Emoji per scale tier, for compact display in messages.
CARD_CONDITION_EMOJI = {
    "graded": "💎",
    "NM": "🟢",
    "EX": "🟢",
    "GO": "🟡",
    "LP": "🟡",
    "PL": "🟠",
    "PO": "🔴",
    None: "❓",
}


def card_condition_emoji(cc: CardCondition) -> str:
    if cc.is_graded:
        return CARD_CONDITION_EMOJI["graded"]
    return CARD_CONDITION_EMOJI.get(cc.raw_grade, "❓")


def detect_card_condition(text: str) -> CardCondition:
    """Detect a trading-card condition from a listing title/description."""
    if not text:
        return CardCondition()

    # 1. Graded — highest specificity. "PSA 10", "BGS 9.5", "CGC 8", "Beckett 9".
    m = _GRADED_PATTERN.search(text)
    if m:
        company = m.group(1).upper()
        if company == "BECKETT":
            company = "BGS"
        grade_str = m.group(2).replace(",", ".")
        try:
            grade = float(grade_str)
        except ValueError:
            grade = None
        if grade is not None and 1.0 <= grade <= 10.0:
            # Round to .5 increments (the only valid grades).
            grade = round(grade * 2) / 2
            return CardCondition(is_graded=True, grading_company=company, grade=grade)

    lower = text.lower()

    # 2. Full phrases (multi-word, low false-positive rate).
    for raw, phrases in _RAW_PHRASES:
        for p in phrases:
            if p in lower:
                return CardCondition(raw_grade=raw)

    # 3. Abbreviations — only when surrounded by clear delimiters.
    for m in _ABBREV_PATTERN.finditer(text):
        abbr = m.group(1).upper()
        if abbr in _ABBREV_MAP:
            return CardCondition(raw_grade=_ABBREV_MAP[abbr])

    return CardCondition()
