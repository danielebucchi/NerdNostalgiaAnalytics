"""
Detect product condition from listing title/description.
Maps to PriceCharting conditions: Ungraded, Complete in Box, New/Sealed, Graded (PSA).
"""
import re

# Condition keywords per language
LOOSE_KEYWORDS = [
    # Italian
    "solo cartuccia", "cartuccia", "senza scatola", "senza custodia",
    "no box", "no scatola", "loose", "sfuso", "solo gioco", "solo disco",
    "solo carta", "senza manuale",
    # English
    "cart only", "cartridge only", "loose", "no box", "no case",
    "disc only", "game only", "card only",
    # French
    "cartouche seule", "sans boite", "sans boîte",
    # German
    "nur modul", "ohne ovp", "lose",
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
    "sigillato", "nuovo", "sealed", "factory sealed", "blister",
    "mai aperto", "ancora sigillato", "cellophane",
    # English
    "sealed", "new", "factory sealed", "mint sealed", "unopened",
    "brand new", "shrink wrap",
    # French
    "scellé", "neuf sous blister",
    # German
    "versiegelt", "neu", "originalverpackt",
]

GRADED_KEYWORDS = [
    "psa", "bgs", "cgc", "beckett", "graded",
    "psa 10", "psa 9", "psa 8", "psa 7",
    "bgs 10", "bgs 9.5", "bgs 9",
]


def detect_condition(text: str) -> str:
    """
    Detect condition from listing title/description.
    Returns: 'Ungraded', 'Complete in Box', 'New/Sealed', 'Graded (PSA)', or 'Unknown'.
    """
    lower = text.lower()

    # Check graded first (most specific)
    for kw in GRADED_KEYWORDS:
        if kw in lower:
            return "Graded (PSA)"

    # Check sealed
    for kw in SEALED_KEYWORDS:
        if kw in lower:
            return "New/Sealed"

    # Check LOOSE before CIB — "ohne ovp" / "senza scatola" must override "ovp" / "scatola"
    # Loose negates completeness, so check it first
    for kw in LOOSE_KEYWORDS:
        if kw in lower:
            return "Ungraded"

    # Check CIB
    for kw in CIB_KEYWORDS:
        if kw in lower:
            return "Complete in Box"

    return "Unknown"


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

    # Fallback order based on detected condition
    fallback_map = {
        "Unknown": ["Ungraded", "Complete in Box", "New/Sealed"],
        "Ungraded": ["Ungraded", "Complete in Box"],
        "Complete in Box": ["Complete in Box", "Ungraded"],
        "New/Sealed": ["New/Sealed", "Complete in Box"],
        "Graded (PSA)": ["Graded (PSA)", "New/Sealed"],
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
    "Ungraded": "📦",
    "Complete in Box": "📦✅",
    "New/Sealed": "🆕",
    "Graded (PSA)": "💎",
    "Unknown": "❓",
}
