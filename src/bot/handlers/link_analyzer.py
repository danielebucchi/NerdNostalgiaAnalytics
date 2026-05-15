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
from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.vinted import VintedCollector
from src.db.database import async_session
from src.db.models import Product
from src.utils.condition import detect_condition, get_condition_price, CONDITION_EMOJI
from src.utils.currency import get_exchange_rates, usd_to_eur
from src.utils.buy_links import get_buy_links

logger = logging.getLogger(__name__)
pc = PriceChartingCollector()
vinted = VintedCollector()


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

    # --- DETECT CONDITION from title + description ---
    listing_text = f"{title} {listing.get('description', '')}"
    detected_condition = detect_condition(listing_text)
    cond_emoji = CONDITION_EMOJI.get(detected_condition, "")

    # --- GET CORRECT PRICE FOR THIS CONDITION ---
    conditions = await pc.get_all_conditions(product_result.external_id)
    market_usd, condition_used = get_condition_price(conditions, detected_condition)
    market_usd = market_usd or product_result.current_price or 0
    market_eur = usd_to_eur(market_usd, rates) if market_usd else 0

    # Analysis
    df = await get_or_fetch_prices(product.id)
    analysis = analyze(df) if df is not None and len(df) >= 6 else None
    prediction = predict_prices(df) if df is not None and len(df) >= 10 else None

    # --- VERDICT ---
    lines = [
        f"🔗 *ANALISI ANNUNCIO*\n",
        f"📦 {title}",
        f"🏪 {platform}: *€{price_eur:.2f}*",
        f"🔍 Match: {product.name}",
        f"{cond_emoji} Condizione: *{detected_condition}*\n",
    ]

    # Show all condition prices for reference
    if conditions:
        lines.append("*Prezzi mercato per condizione:*")
        for cond_name, cond_prices in conditions.items():
            if cond_prices and cond_name not in ("Box Only", "Manual Only"):
                p = cond_prices[-1].price
                p_eur = usd_to_eur(p, rates)
                marker = " ← *confronto*" if cond_name == condition_used else ""
                lines.append(f"  {cond_name}: €{p_eur:.2f}{marker}")
        lines.append("")

    # Price comparison against CORRECT condition
    if market_eur > 0:
        diff = ((price_eur - market_eur) / market_eur) * 100
        if diff < -20:
            lines.append(f"✅ *{abs(diff):.0f}% sotto* il mercato {condition_used} (€{market_eur:.2f})")
        elif diff < -5:
            lines.append(f"🟢 {abs(diff):.0f}% sotto mercato {condition_used} (€{market_eur:.2f})")
        elif diff < 5:
            lines.append(f"🟡 Al prezzo di mercato {condition_used} (€{market_eur:.2f})")
        elif diff < 15:
            lines.append(f"🟠 {diff:.0f}% sopra mercato {condition_used} (€{market_eur:.2f})")
        else:
            lines.append(f"🔴 *{diff:.0f}% sopra* il mercato {condition_used} (€{market_eur:.2f})")

    if analysis:
        emoji = SIGNAL_EMOJI.get(analysis.signal, "")
        lines.append(f"{emoji} Segnale: {analysis.signal.value}")

    if prediction:
        change = ((prediction.pred_90d - prediction.current_price) / prediction.current_price * 100
                  if prediction.current_price > 0 else 0)
        trend_emoji = "📈" if prediction.trend == "bullish" else "📉" if prediction.trend == "bearish" else "➡️"
        lines.append(f"{trend_emoji} Previsione 90gg: {change:+.1f}%")

    # Max offer for resale
    if market_eur > 0:
        target_margin = 0.30
        max_offer = market_eur / (1 + target_margin)
        aggressive = max_offer * 0.80

        lines.append(f"\n🧮 *Offerta consigliata (per 30% margine):*")
        lines.append(f"   Parti da: *€{aggressive:.2f}*")
        lines.append(f"   Max: *€{max_offer:.2f}*")

        if price_eur <= aggressive:
            lines.append(f"\n✅ *AFFARE! A €{price_eur:.2f} hai margine >50%*")
        elif price_eur <= max_offer:
            margin = ((market_eur - price_eur) / price_eur * 100)
            lines.append(f"\n✅ *Buon acquisto!* Margine ~{margin:.0f}%")
        elif price_eur <= market_eur * 0.95:
            margin = ((market_eur - price_eur) / price_eur * 100)
            lines.append(f"\n🟡 Margine {margin:.0f}% — sotto il target 30% ma comunque positivo")
        elif price_eur <= market_eur * 1.05:
            lines.append(f"\n🟠 *Prezzo di mercato* — nessun margine di guadagno")
        else:
            lines.append(f"\n🔴 *Troppo caro.* Offri max €{max_offer:.2f}")

    # Links
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
