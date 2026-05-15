"""
Smart search matching — pick the best result from multiple search results.
Prefers exact/close matches over special editions.
"""
import re

from rapidfuzz import fuzz


def best_match(query: str, results: list, name_attr: str = "name") -> int:
    """
    Find the best matching result index for a query.
    Penalizes special editions, brackets, extra words.
    Returns index of the best match (0-based).
    """
    if not results:
        return 0
    if len(results) == 1:
        return 0

    query_lower = query.lower().strip()
    query_words = set(query_lower.split())
    scores = []

    for i, result in enumerate(results):
        name = getattr(result, name_attr, "") if hasattr(result, name_attr) else str(result)
        name_lower = name.lower().strip()

        # Also check set_name and product_url for matching
        set_name = (getattr(result, "set_name", "") or "").lower()
        product_url = (getattr(result, "product_url", "") or "").lower()
        full_text = f"{name_lower} {set_name} {product_url}"

        # Strip brackets for matching
        name_clean = re.sub(r'\[.*?\]', '', name_lower).strip()
        name_clean = re.sub(r'#\d+', '', name_clean).strip()

        # Base: fuzzy score on FULL name (not cleaned) to differentiate variants
        fuzzy_score = fuzz.ratio(query_lower, name_clean)

        # Bonus: all query words appear in name + set + url
        full_words = set(re.findall(r'\w+', full_text))
        matching_words = query_words & full_words
        fuzzy_score += len(matching_words) * 10

        # Bonus: exact substring match in name
        if query_lower in name_clean:
            fuzzy_score += 20

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
    return scores[0][0]
