"""
Analyze a product listing URL from any platform.
Send a link to the bot and get: price evaluation, market comparison, offer suggestion.
Supports: Vinted, eBay, Cardmarket, Subito, PriceCharting.
"""
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.analysis.indicators import analyze, Signal, SIGNAL_EMOJI
from src.analysis.prediction import predict_prices
from src.bot.handlers.signal import get_or_fetch_prices
from src.bot.handlers.stats import COMMISSIONS
from src.collectors.ebay import EbayCollector
from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.pokemontcg_api import search_card_prices
from src.collectors.retrogaming import search_retrogamingshop
from src.collectors.vinted import VintedCollector
from src.db.database import async_session
from src.db.models import Product, ProductCategory
from src.utils.condition import detect_condition, get_condition_price, CONDITION_EMOJI
from src.utils.currency import get_exchange_rates, usd_to_eur
from src.utils.price_aggregator import aggregate_prices, format_aggregated_prices
from src.utils.buy_links import get_buy_links

logger = logging.getLogger(__name__)
pc = PriceChartingCollector()
vinted = VintedCollector()
ebay = EbayCollector()


async def _extract_listing_info(url: str) -> dict | None:
    """
    Extract title, price, and description from a listing URL.
    Returns {"title": str, "price_eur": float, "platform": str, "description": str} or None.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
            },
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        return None

    # --- VINTED ---
    if "vinted." in url:
        return _parse_vinted(soup, url)

    # --- EBAY ---
    if "ebay." in url:
        return _parse_ebay(soup, url)

    # --- CARDMARKET ---
    if "cardmarket." in url:
        return _parse_cardmarket(soup, url)

    # --- SUBITO ---
    if "subito." in url:
        return _parse_subito(soup, url)

    # --- GENERIC: try JSON-LD ---
    return _parse_jsonld(soup, url)


def _parse_vinted(soup: BeautifulSoup, url: str) -> dict | None:
    description = ""
    # Get description from page
    desc_el = soup.select_one('[itemprop="description"]') or soup.select_one('[data-testid*="description"]')
    if desc_el:
        description = desc_el.get_text(strip=True)

    # JSON-LD is the most reliable for title/price
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if data.get("@type") == "Product":
                title = data.get("name", "")
                desc_ld = data.get("description", "")
                if desc_ld:
                    description = desc_ld

                offers = data.get("offers", {})
                price = None
                if isinstance(offers, dict):
                    price_str = offers.get("price")
                    if price_str:
                        price = float(str(price_str).replace(",", "."))
                if price is None:
                    price_el = soup.select_one('[itemprop="price"]')
                    if price_el:
                        price_text = price_el.get("content") or price_el.get_text(strip=True)
                        price_match = re.search(r'([\d.,]+)', price_text.replace(".", "").replace(",", "."))
                        if price_match:
                            price = float(price_match.group(1))
                if title and price:
                    return {"title": title, "price_eur": price, "platform": "Vinted",
                            "description": description}
        except (json.JSONDecodeError, ValueError):
            continue

    # Fallback: HTML
    title_el = soup.select_one("h1")
    price_el = soup.select_one('[itemprop="price"]')
    if title_el:
        title = title_el.get_text(strip=True)
        price = None
        if price_el:
            price_text = price_el.get_text(strip=True)
            match = re.search(r'([\d]+[.,]?\d*)', price_text.replace(".", "").replace(",", "."))
            if match:
                price = float(match.group(1))
        if price:
            return {"title": title, "price_eur": price, "platform": "Vinted",
                    "description": description}
    return None


def _parse_ebay(soup: BeautifulSoup, url: str) -> dict | None:
    title_el = soup.select_one("#itemTitle, h1.x-item-title__mainTitle span")
    price_el = soup.select_one("#prcIsum, .x-price-primary span")

    title = title_el.get_text(strip=True).replace("Details about  \xa0", "") if title_el else None
    price = None
    if price_el:
        price_text = price_el.get_text(strip=True)
        match = re.search(r'EUR\s*([\d.,]+)', price_text)
        if match:
            price = float(match.group(1).replace(".", "").replace(",", "."))
        else:
            match = re.search(r'([\d.,]+)', price_text.replace(".", "").replace(",", "."))
            if match:
                price = float(match.group(1))

    if title and price:
        return {"title": title, "price_eur": price, "platform": "eBay"}

    # Try JSON-LD
    return _parse_jsonld(soup, url, platform="eBay")


def _parse_cardmarket(soup: BeautifulSoup, url: str) -> dict | None:
    title_el = soup.select_one("h1")
    price_el = soup.select_one(".price-container .text-end, .col-price")

    title = title_el.get_text(strip=True) if title_el else None
    price = None
    if price_el:
        match = re.search(r'([\d.,]+)\s*€', price_el.get_text(strip=True))
        if match:
            price = float(match.group(1).replace(".", "").replace(",", "."))

    if title and price:
        return {"title": title, "price_eur": price, "platform": "Cardmarket"}
    return None


def _parse_subito(soup: BeautifulSoup, url: str) -> dict | None:
    # Try JSON-LD first
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if data.get("@type") == "Product" or data.get("@type") == "Offer":
                title = data.get("name", "")
                offers = data.get("offers", data)
                price = None
                if isinstance(offers, dict):
                    price = offers.get("price")
                elif isinstance(offers, list) and offers:
                    price = offers[0].get("price")
                if title and price:
                    return {"title": title, "price_eur": float(price), "platform": "Subito"}
        except (json.JSONDecodeError, ValueError):
            continue

    # HTML fallback
    title_el = soup.select_one("h1")
    price_el = soup.select_one('[class*="price"]')
    if title_el and price_el:
        title = title_el.get_text(strip=True)
        match = re.search(r'€?\s*([\d.,]+)', price_el.get_text(strip=True).replace(".", "").replace(",", "."))
        if match:
            return {"title": title, "price_eur": float(match.group(1)), "platform": "Subito"}
    return None


def _parse_jsonld(soup: BeautifulSoup, url: str, platform: str = "Web") -> dict | None:
    """Generic JSON-LD parser for any website."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Product", "Offer", "IndividualProduct"):
                    title = item.get("name", "")
                    offers = item.get("offers", item)
                    price = None
                    if isinstance(offers, dict):
                        price = offers.get("price")
                    elif isinstance(offers, list) and offers:
                        price = offers[0].get("price")
                    if title and price:
                        return {"title": title, "price_eur": float(price), "platform": platform}
        except (json.JSONDecodeError, ValueError):
            continue
    return None


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages containing URLs — analyze the listing."""
    text = update.message.text or ""

    # Extract URL from message
    url_match = re.search(r'https?://\S+', text)
    if not url_match:
        return
    url = url_match.group(0)

    # Only handle known marketplace URLs
    known_domains = ["vinted.", "ebay.", "cardmarket.", "subito.", "wallapop.", "pricecharting."]
    if not any(d in url for d in known_domains):
        return

    msg = await update.message.reply_text(f"🔗 Analizzo l'annuncio...")

    # Extract listing info
    listing = await _extract_listing_info(url)
    if not listing:
        await msg.edit_text(
            "Non riesco a leggere i dettagli dell'annuncio.\n"
            "Prova con /evaluate <nome> <prezzo>"
        )
        return

    title = listing["title"]
    price_eur = listing["price_eur"]
    platform = listing["platform"]

    await msg.edit_text(
        f"📦 *{title}*\n"
        f"💰 {platform}: €{price_eur:.2f}\n\n"
        f"⏳ Raccolgo dati di mercato...",
        parse_mode="Markdown",
    )

    rates = await get_exchange_rates()

    # Search on PriceCharting
    search_query = _simplify_title(title)
    results = await pc.search(search_query, max_results=3)

    if not results:
        # Try with shorter query
        words = search_query.split()[:3]
        results = await pc.search(" ".join(words), max_results=3)

    if not results:
        await msg.edit_text(
            f"📦 *{title}*\n"
            f"💰 {platform}: €{price_eur:.2f}\n\n"
            f"⚠ Prodotto non trovato su PriceCharting.\n"
            f"Prova /evaluate <nome specifico> {price_eur:.0f}",
            parse_mode="Markdown",
        )
        return

    product_result = results[0]

    # Save product
    async with async_session() as session:
        existing = await session.execute(
            select(Product).where(
                Product.external_id == product_result.external_id,
                Product.source == product_result.source,
            )
        )
        product = existing.scalar_one_or_none()
        if not product:
            product = Product(
                external_id=product_result.external_id, source=product_result.source,
                name=product_result.name, category=product_result.category,
                set_name=product_result.set_name, product_url=product_result.product_url,
                current_price=product_result.current_price,
            )
            session.add(product)
            await session.commit()
            await session.refresh(product)

    # --- DETECT CONDITION ---
    listing_text = f"{title} {listing.get('description', '')}"
    detected_condition = detect_condition(listing_text)

    # For video games on Vinted/Subito, default to Ungraded (loose) if unknown
    # Most second-hand game listings are just the cartridge/disc
    is_videogame = product.category == ProductCategory.VIDEOGAME
    if detected_condition == "Unknown" and is_videogame:
        detected_condition = "Ungraded"

    # For cards on Vinted, default to Ungraded if unknown
    if detected_condition == "Unknown" and platform in ("Vinted", "Subito"):
        detected_condition = "Ungraded"

    cond_emoji = CONDITION_EMOJI.get(detected_condition, "")

    # --- COLLECT ALL PRICES ---
    # 1. PriceCharting (by condition)
    conditions = await pc.get_all_conditions(product_result.external_id)
    if conditions:
        pc_usd, condition_used = get_condition_price(conditions, detected_condition)
    else:
        # Fallback: search price is typically the loose/used price
        pc_usd = product_result.current_price
        condition_used = "Ungraded"
        logger.warning(f"No condition data for {product_result.external_id}, using search price")
    pc_usd = pc_usd or product_result.current_price or 0

    # Sanity check: if the PriceCharting price is >5x the offered price,
    # we probably matched the wrong condition. Fall back to Ungraded.
    eur_rate = 0.92
    if pc_usd * eur_rate > price_eur * 5 and condition_used != "Ungraded":
        if "Ungraded" in conditions and conditions["Ungraded"]:
            old_cond = condition_used
            pc_usd = conditions["Ungraded"][-1].price
            condition_used = "Ungraded"
            logger.info(f"Sanity check: {old_cond} too high, switched to Ungraded")

    # 2. Pokemon TCG API (Cardmarket + TCGPlayer) — only for cards
    cm_trend = cm_avg_sell = cm_low = tcg_market = None
    is_card = product.category in (
        ProductCategory.POKEMON, ProductCategory.MAGIC, ProductCategory.YUGIOH,
    )
    if is_card:
        tcg_cards = await search_card_prices(search_query, max_results=1)
        if tcg_cards:
            tcg_card = tcg_cards[0]
            cm_trend = tcg_card.cm_trend
            cm_avg_sell = tcg_card.cm_avg_sell
            cm_low = tcg_card.cm_low
            tcg_market = tcg_card.tcg_market

    # 3. eBay sold (if API configured)
    ebay_avg = None
    ebay_count = 0
    if ebay.is_configured:
        ebay_data = await ebay.get_sold_prices(search_query, marketplace="it")
        ebay_avg = ebay_data.get("avg")
        ebay_count = ebay_data.get("count", 0)

    # 4. RetroGamingShop.it (for video games)
    rgs_avg = None
    if is_videogame:
        rgs_listings = await search_retrogamingshop(search_query, max_results=5)
        if rgs_listings:
            rgs_prices = [l.price_eur for l in rgs_listings]
            rgs_avg = sum(rgs_prices) / len(rgs_prices)

    # 5. Vinted
    vinted_listings = await vinted.search_listings(search_query, max_results=10, order="price_low_to_high")
    vinted_relevant = [l for l in vinted_listings
                       if vinted._title_matches(l.title, search_query)
                       and not vinted.is_suspicious(l)]
    vinted_avg = (sum(l.price_eur for l in vinted_relevant[:5]) / min(5, len(vinted_relevant))
                  if vinted_relevant else None)

    # 5. Aggregate all sources
    eur_rate = rates.get("EUR", 0.92) if rates else 0.92
    agg = aggregate_prices(
        pricecharting_usd=pc_usd,
        cardmarket_trend_eur=cm_trend,
        cardmarket_avg_sell_eur=cm_avg_sell,
        cardmarket_low_eur=cm_low,
        tcgplayer_market_usd=tcg_market,
        vinted_avg_eur=vinted_avg,
        ebay_sold_avg_eur=ebay_avg,
        ebay_sold_count=ebay_count,
        retrogamingshop_avg_eur=rgs_avg,
        usd_to_eur_rate=eur_rate,
    )
    fair_value = agg.fair_value_eur

    # Analysis
    df = await get_or_fetch_prices(product.id)
    analysis = analyze(df) if df is not None and len(df) >= 6 else None

    # --- VERDICT ---
    lines = [
        f"🔗 *ANALISI ANNUNCIO*\n",
        f"📦 {title}",
        f"🏪 {platform}: *€{price_eur:.2f}*",
        f"🔍 Match: {product.name}",
        f"{cond_emoji} Condizione: *{detected_condition}*\n",
    ]

    # Aggregated fair value
    lines.append(format_aggregated_prices(agg))

    # Comparison
    if fair_value > 0:
        diff = ((price_eur - fair_value) / fair_value) * 100
        lines.append("")
        if diff < -20:
            lines.append(f"✅ *{abs(diff):.0f}% SOTTO* il valore di mercato!")
        elif diff < -5:
            lines.append(f"🟢 {abs(diff):.0f}% sotto il valore di mercato")
        elif diff < 5:
            lines.append(f"🟡 Al prezzo di mercato")
        elif diff < 15:
            lines.append(f"🟠 {diff:.0f}% sopra il mercato")
        else:
            lines.append(f"🔴 *{diff:.0f}% SOPRA* il mercato")

    if analysis:
        emoji = SIGNAL_EMOJI.get(analysis.signal, "")
        lines.append(f"{emoji} Segnale tecnico: {analysis.signal.value}")

    # Max offer for resale
    if fair_value > 0:
        target_margin = 0.30
        max_offer = fair_value / (1 + target_margin)
        aggressive = max_offer * 0.80

        lines.append(f"\n🧮 *Offerta consigliata (30% margine):*")
        lines.append(f"   Parti da: *€{aggressive:.2f}*")
        lines.append(f"   Max: *€{max_offer:.2f}*")

        if price_eur <= aggressive:
            lines.append(f"\n✅ *AFFARE! Margine >50%*")
        elif price_eur <= max_offer:
            margin = ((fair_value - price_eur) / price_eur * 100)
            lines.append(f"\n✅ *Buon acquisto!* Margine ~{margin:.0f}%")
        elif price_eur <= fair_value * 0.95:
            margin = ((fair_value - price_eur) / price_eur * 100)
            lines.append(f"\n🟡 Margine {margin:.0f}% — positivo ma sotto il 30%")
        elif price_eur <= fair_value * 1.05:
            lines.append(f"\n🟠 *Prezzo di mercato* — nessun margine")
        else:
            lines.append(f"\n🔴 *Non conviene.* Offri max €{max_offer:.2f}")

    buy_links = get_buy_links(product.name, product.category, product.product_url)
    lines.append(f"\n{buy_links}")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


def _simplify_title(title: str) -> str:
    """Remove common noise from listing titles for better search."""
    # Remove common Vinted/eBay noise words
    noise = [
        "carta", "card", "carte", "cards", "pokemon", "pokémon",
        "near mint", "nm", "mint", "come nuovo", "come nuova",
        "spedizione", "inclusa", "gratuita", "gratis",
        "originale", "original", "autentico",
        "italiano", "inglese", "giapponese", "english", "japanese", "italian",
        "holo", "holographic", "reverse",
    ]
    result = title.lower()
    # Keep the first meaningful words
    for n in noise:
        result = result.replace(n, " ")
    # Clean up
    result = re.sub(r'[^\w\s]', ' ', result)
    result = re.sub(r'\s+', ' ', result).strip()
    # Take first 5 meaningful words
    words = [w for w in result.split() if len(w) > 1][:5]
    return " ".join(words) if words else title[:30]
