"""
Smart search matching — pick the best result from multiple search results.
Prefers exact/close matches over special editions.
"""
import re

from rapidfuzz import fuzz

# Tokens we should ignore when looking at "extra tokens in the result name"
# — they're noise (article, ampersand, set markers) and don't indicate a
# more-specific card than the user asked for.
_IGNORED_NAME_TOKENS = {"&", "the", "of", "and", "il", "lo", "la", "di"}


def _extract_card_number(text: str) -> str | None:
    """Pull a card number out of `text`.

    Recognises explicit `#N` markers and bare digit tokens (1–4 digits) that
    aren't part of a longer token. Returns the digit string or None.
    """
    # Explicit `#N`
    m = re.search(r"#\s*(\d{1,4})\b", text)
    if m:
        return m.group(1)
    # Bare standalone digit (preferred order: dedicated card-number-looking
    # tokens like `xy110`, `sm191` first, then plain digits).
    m = re.search(r"\b([a-z]{2,3}\d{1,4})\b", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r"\b(\d{1,4})\b", text)
    if m:
        return m.group(1)
    return None


def best_match(query: str, results: list, name_attr: str = "name") -> int:
    """Find the best matching result index for a query.

    Back-compat wrapper around `best_match_with_confidence` for call-sites
    that just want the index.
    """
    idx, _ = best_match_with_confidence(query, results, name_attr=name_attr)
    return idx


def best_match_with_confidence(
    query: str, results: list, name_attr: str = "name",
) -> tuple[int, float]:
    """Like `best_match` but also returns a confidence ∈ [0, 1].

    Confidence is derived from the gap between the top score and the runner-up
    AND the absolute score of the winner. A wide gap + high top score is high
    confidence; a tight race or universally-low scores is low.
    """
    if not results:
        return 0, 0.0
    if len(results) == 1:
        return 0, 1.0

    query_lower = query.lower().strip()
    query_words = set(query_lower.split())
    query_number = _extract_card_number(query_lower)
    scores = []

    for i, result in enumerate(results):
        name = getattr(result, name_attr, "") if hasattr(result, name_attr) else str(result)
        name_lower = name.lower().strip()

        # Also check set_name and product_url for matching
        set_name = (getattr(result, "set_name", "") or "").lower()
        product_url = (getattr(result, "product_url", "") or "").lower()
        full_text = f"{name_lower} {set_name} {product_url}"

        # Strip brackets for matching, capture the card number separately.
        result_number = _extract_card_number(name_lower)
        name_clean = re.sub(r'\[.*?\]', '', name_lower).strip()
        name_clean = re.sub(r'#\s*\d+', '', name_clean).strip()
        name_clean = re.sub(r'\b[a-z]{2,3}\d{1,4}\b', '', name_clean).strip()

        # Base: fuzzy score on FULL name (not cleaned) to differentiate variants
        fuzzy_score = fuzz.ratio(query_lower, name_clean)

        # Bonus: all query words appear in name + set + url
        full_words = set(re.findall(r'\w+', full_text))
        matching_words = query_words & full_words
        fuzzy_score += len(matching_words) * 10

        # Bonus: exact substring match in name
        if query_lower in name_clean:
            fuzzy_score += 20

        # CARD NUMBER MATCH — the strongest disambiguator when present.
        # If the user typed "mew 8" and a result is "Mew #8", that's a near-
        # certain match; if it's "Mewtwo & Mew GX #SM191" that's a near-certain
        # non-match. This bonus/penalty is intentionally large enough to
        # override fuzzy-ratio differences from longer result names.
        if query_number:
            if result_number == query_number:
                fuzzy_score += 60
            elif result_number is not None:
                # Result has a different card number than the user asked for.
                fuzzy_score -= 25

        # Penalty: significant name tokens that aren't in the query.
        # Mew #8 → result_tokens={"mew"} → 0 extras vs query {"mew","8","wizards",...}
        # Mewtwo & Mew GX #SM191 → result_tokens={"mewtwo","mew","gx"} → 2 extras
        # ("mewtwo","gx" not in query) → penalty kicks in.
        result_tokens = {t for t in name_clean.split() if len(t) >= 2 and t not in _IGNORED_NAME_TOKENS}
        extra_tokens = result_tokens - query_words
        if extra_tokens:
            fuzzy_score -= len(extra_tokens) * 8

        # Penalty: special edition brackets — if the query doesn't mention
        # the bracket content, the user likely wants the standard version
        bracket_count = len(re.findall(r'\[.*?\]', name))
        if bracket_count > 0:
            bracket_text = " ".join(re.findall(r'\[(.*?)\]', name_lower))
            bracket_words = set(bracket_text.split())
            # How many bracket words are in the query? If none → big penalty
            bracket_overlap = query_words & bracket_words
            if not bracket_overlap:
                fuzzy_score -= bracket_count * 25  # User didn't ask for this edition
            else:
                fuzzy_score -= bracket_count * 3   # Partial match, small penalty

        # Penalty: much longer name (too specific)
        extra_words = len(name_clean.split()) - len(query_lower.split())
        if extra_words > 3:
            fuzzy_score -= (extra_words - 3) * 3

        # Bonus: first result (PriceCharting's own relevance)
        if i == 0:
            fuzzy_score += 3

        scores.append((i, fuzzy_score))

    scores.sort(key=lambda x: x[1], reverse=True)
    top_idx, top_score = scores[0]
    runner_up_score = scores[1][1] if len(scores) > 1 else top_score - 50

    # Confidence model: a wide gap between top and runner-up + a high absolute
    # top score means we're sure. A tight race or universally low scores means
    # we're not.
    gap = max(0, top_score - runner_up_score)
    # Map score and gap onto [0, 1]. Calibrated by observing real PriceCharting
    # outputs: top_score 80+ with gap 30+ is near-certain; top_score 40 with
    # gap 5 is a coin flip.
    score_component = min(1.0, max(0.0, (top_score - 20) / 80))   # 20 → 0, 100 → 1
    gap_component = min(1.0, gap / 40)                            # 40-point lead → maxed
    confidence = round(0.5 * score_component + 0.5 * gap_component, 3)
    return top_idx, confidence


def confidence_emoji(confidence: float) -> str:
    """Map a [0, 1] confidence to a traffic-light emoji for display."""
    if confidence >= 0.85:
        return "🟢🟢"
    if confidence >= 0.6:
        return "🟢"
    if confidence >= 0.35:
        return "🟡"
    return "🟠"
