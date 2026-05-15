from urllib.parse import quote_plus

from src.db.models import ProductCategory
from src.collectors.subito_wallapop import subito_search_url, wallapop_search_url
from src.collectors.retrogaming import retrogamingshop_search_url, backingame_search_url

CARDMARKET_CATEGORIES = {
    ProductCategory.POKEMON: "Pokemon",
    ProductCategory.MAGIC: "MagicTheGathering",
    ProductCategory.YUGIOH: "YuGiOh",
}


def get_buy_links(product_name: str, category: str, product_url: str | None = None) -> str:
    """Generate buy links for a product based on its category."""
    encoded_name = quote_plus(product_name)
    links = []

    # Cardmarket (TCG cards only)
    cm_category = CARDMARKET_CATEGORIES.get(category)
    if cm_category:
        cm_url = f"https://www.cardmarket.com/en/{cm_category}/Products/Search?searchString={encoded_name}"
        links.append(f"[Cardmarket]({cm_url})")

    # Retrogaming shops (video games only)
    if category == ProductCategory.VIDEOGAME:
        links.append(f"[RetroGamingShop]({retrogamingshop_search_url(product_name)})")
        links.append(f"[BackInGame]({backingame_search_url(product_name)})")

    # Vinted
    vinted_url = f"https://www.vinted.it/catalog?search_text={encoded_name}&order=price_low_to_high"
    links.append(f"[Vinted]({vinted_url})")

    # Subito.it
    links.append(f"[Subito]({subito_search_url(product_name)})")

    # eBay
    ebay_url = f"https://www.ebay.it/sch/i.html?_nkw={encoded_name}&LH_BIN=1&_sop=15"
    links.append(f"[eBay]({ebay_url})")

    # Wallapop
    links.append(f"[Wallapop]({wallapop_search_url(product_name)})")

    # PriceCharting
    if product_url:
        links.append(f"[PriceCharting]({product_url})")

    return "🛒 " + " | ".join(links)
