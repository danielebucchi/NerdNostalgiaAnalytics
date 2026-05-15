from urllib.parse import quote_plus

from src.db.models import ProductCategory
from src.collectors.subito_wallapop import subito_search_url, wallapop_search_url
from src.collectors.retrogaming import retrogamingshop_search_url, backingame_search_url

CARDMARKET_CATEGORIES = {
    ProductCategory.POKEMON: "Pokemon",
    ProductCategory.MAGIC: "MagicTheGathering",
    ProductCategory.YUGIOH: "YuGiOh",
}


def _clean_name_for_search(name: str) -> str:
    """Clean product name for marketplace search.
    Removes card numbers (#4), edition tags ([1st Edition]), etc.
    """
    import re
    clean = name
    clean = re.sub(r'\s*#\d+', '', clean)           # Remove #4, #123
    clean = re.sub(r'\s*\[.*?\]', '', clean)         # Remove [1st Edition], [Shadowless]
    clean = re.sub(r'\s*\(.*?\)', '', clean)         # Remove (Base Set)
    clean = re.sub(r'\s+', ' ', clean).strip()       # Clean whitespace
    return clean


def get_buy_links(product_name: str, category: str, product_url: str | None = None) -> str:
    """Generate buy links for a product based on its category."""
    clean_name = _clean_name_for_search(product_name)
    encoded_name = quote_plus(clean_name)
    encoded_full = quote_plus(product_name)
    links = []

    # Cardmarket (TCG cards only) — use clean name + v2 search mode
    cm_category = CARDMARKET_CATEGORIES.get(category)
    if cm_category:
        cm_url = f"https://www.cardmarket.com/en/{cm_category}/Products/Search?category=-1&searchString={encoded_name}&searchMode=v2"
        links.append(f"[Cardmarket]({cm_url})")

    # Retrogaming shops (video games only)
    if category == ProductCategory.VIDEOGAME:
        links.append(f"[RetroGamingShop]({retrogamingshop_search_url(product_name)})")
        links.append(f"[BackInGame]({backingame_search_url(product_name)})")
        bits26_url = f"https://www.26bits.it/?s={encoded_name}&post_type=product"
        links.append(f"[26bits]({bits26_url})")

    # Vinted — clean name works better
    vinted_url = f"https://www.vinted.it/catalog?search_text={encoded_name}&order=price_low_to_high"
    links.append(f"[Vinted]({vinted_url})")

    # Subito.it
    links.append(f"[Subito]({subito_search_url(clean_name)})")

    # eBay — full name is better for specificity
    ebay_url = f"https://www.ebay.it/sch/i.html?_nkw={encoded_full}&LH_BIN=1&_sop=15"
    links.append(f"[eBay]({ebay_url})")

    # Wallapop
    links.append(f"[Wallapop]({wallapop_search_url(clean_name)})")

    # PriceCharting
    if product_url:
        links.append(f"[PriceCharting]({product_url})")

    return "🛒 " + " | ".join(links)
