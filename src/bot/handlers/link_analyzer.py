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
from src.collectors.cardtrader import cardtrader
from src.collectors.ebay import EbayCollector
from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.pokemontcg_api import search_card_prices
from src.collectors.retrogaming import search_retrogamingshop
from src.collectors.twentysixbits import get_26bits_price
from src.collectors.vinted import VintedCollector
from src.db.database import async_session
from src.db.models import Product, ProductCategory
from src.utils.condition import (
    CONDITION_EMOJI,
    CardCondition,
    card_condition_emoji,
    card_condition_to_pc_bucket,
    detect_card_condition,
    detect_condition,
    detect_videogame_condition,
    get_condition_price,
)
from src.utils.llm_parser import (
    detect_bundle,
    detect_videogame_condition_with_llm_fallback,
)
from src.utils.query_parser import parse_card_query
from src.utils.search_match import best_match_with_confidence, confidence_emoji
from src.bot.picker import (
    build_picker_keyboard,
    discard_picker_state,
    parse_picker_callback,
    retrieve_picker_state,
    stash_picker_state,
)
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

    # Fallback: HTML parsing (newer Vinted pages don't have JSON-LD)
    title_el = soup.select_one("h1")
    if not title_el:
        return None

    title = title_el.get_text(strip=True)
    price = None

    # Try itemprop="price"
    price_el = soup.select_one('[itemprop="price"]')
    if price_el:
        price_text = price_el.get("content") or price_el.get_text(strip=True)
        match = re.search(r'([\d]+[.,]?\d*)', price_text.replace(".", "").replace(",", "."))
        if match:
            price = float(match.group(1))

    # Try class-based price selectors
    if price is None:
        for sel in ['[class*="price"]', 'p', 'div', 'span']:
            for el in soup.select(sel):
                txt = el.get_text(strip=True)
                match = re.match(r'^([\d]+[,.][\d]{2})\s*€$', txt)
                if match:
                    price = float(match.group(1).replace(".", "").replace(",", "."))
                    break
            if price:
                break

    # Get description from itemprop
    if not description:
        desc_el = soup.select_one('[itemprop="description"]')
        if desc_el:
            description = desc_el.get_text(strip=True)

    if title and price:
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

    # Parse the listing title for set / condition / variant hints. The set is
    # the most useful signal here — it lets us narrow PriceCharting and feed
    # CardTrader an exact `expansion_code` for the cached fast path.
    title_parsed = parse_card_query(title)
    expansion_code: str | None = title_parsed.expansion.code if title_parsed.expansion else None

    # Search on PriceCharting. Prefer a query refined with the canonical EN set
    # name when we detected one; fall back to the noise-stripped title.
    if title_parsed.expansion:
        bits = []
        if title_parsed.name:
            bits.append(title_parsed.name)
        bits.append(title_parsed.expansion.name_en)
        search_query = " ".join(bits)
    else:
        search_query = _simplify_title(title)

    # Ask for 5 results so the picker has enough candidates when confidence
    # is borderline. best_match still picks the top one for the auto-path.
    results = await pc.search(search_query, max_results=5)

    if not results:
        # Try with shorter query
        words = search_query.split()[:3]
        results = await pc.search(" ".join(words), max_results=5)

    if not results and title_parsed.expansion:
        # Last resort: search by set alone, then let best_match disambiguate.
        results = await pc.search(title_parsed.expansion.name_en, max_results=5)

    if not results:
        await msg.edit_text(
            f"📦 *{title}*\n"
            f"💰 {platform}: €{price_eur:.2f}\n\n"
            f"⚠ Prodotto non trovato su PriceCharting.\n"
            f"Prova /evaluate <nome specifico> {price_eur:.0f}",
            parse_mode="Markdown",
        )
        return

    # Match the listing against the result list — same disambiguation as
    # /evaluate (card number bonus, extra-token penalty, etc.).
    search_text = title
    best_idx, match_confidence = best_match_with_confidence(search_text, results)

    # Picker: same threshold as /evaluate and /offer.
    if match_confidence < 0.6 and len(results) >= 2:
        await _show_link_picker(
            msg, context, results[:5], best_idx,
            listing=listing,
            expansion_code=expansion_code,
        )
        return

    await _finish_link(
        msg, context, results, best_idx, match_confidence,
        listing=listing,
        expansion_code=expansion_code,
    )


async def _finish_link(
    msg, context, results, best_idx, match_confidence,
    *, listing, expansion_code,
):
    """Continue link analysis with a chosen product. Reused by auto-pick + picker."""
    title = listing["title"]
    price_eur = listing["price_eur"]
    platform = listing["platform"]
    rates = await get_exchange_rates()
    product_result = results[best_idx]

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
    is_videogame = product.category == ProductCategory.VIDEOGAME
    is_card = product.category in (
        ProductCategory.POKEMON, ProductCategory.MAGIC, ProductCategory.YUGIOH,
    )

    # Bundle/lot pre-check on the full listing text — used to warn the user
    # when the listing is a collection, since per-item comparisons are noise.
    bundle = await detect_bundle(listing_text)

    # Cards use the TCG-specific scale (graded PSA/BGS/... or raw NM→PO).
    card_cond: CardCondition | None = None
    if is_card:
        card_cond = detect_card_condition(listing_text)
        if not card_cond.is_known:
            # No signal on a card listing → assume raw Near Mint (the typical
            # default sellers don't bother mentioning).
            card_cond = CardCondition(raw_grade="NM")
        detected_condition = card_condition_to_pc_bucket(card_cond)
        cond_emoji = card_condition_emoji(card_cond)
        cond_display = card_cond.display
    else:
        # Rule-based first, LLM fallback when title + description don't match
        # any keyword (idiomatic phrasing like "ho perso il libretto").
        vg_cond = await detect_videogame_condition_with_llm_fallback(listing_text)
        detected_condition = vg_cond.label
        # On Vinted/Subito a missing signal almost always means a used/loose copy
        # — the seller wouldn't mention "cartuccia" for their only N64 game.
        if detected_condition == "Unknown" and (is_videogame or platform in ("Vinted", "Subito")):
            detected_condition = "Ungraded"
        cond_emoji = CONDITION_EMOJI.get(detected_condition, "")
        cond_display = vg_cond.display if vg_cond.is_known else detected_condition

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
    ct_median = ct_nm_min = None
    ct_offers = 0
    if is_card:
        tcg_cards = await search_card_prices(search_query, max_results=1)
        if tcg_cards:
            tcg_card = tcg_cards[0]
            cm_trend = tcg_card.cm_trend
            cm_avg_sell = tcg_card.cm_avg_sell
            cm_low = tcg_card.cm_low
            tcg_market = tcg_card.tcg_market

        # CardTrader (real EU marketplace)
        if cardtrader.is_configured:
            game_name = "pokemon"
            if product.category == ProductCategory.MAGIC:
                game_name = "magic"
            elif product.category == ProductCategory.YUGIOH:
                game_name = "yugioh"
            try:
                ct_data = await cardtrader.get_prices(
                    search_query, game=game_name,
                    set_name=product.set_name,
                    # Skip CardTrader's /expansions lookup when we already
                    # know the TCG-API code → the registry hits the cached
                    # cardtrader_id directly.
                    expansion_code=expansion_code or (product.set_name and None),
                )
                if ct_data:
                    ct_offers = ct_data.total_offers
                    # If we know the card condition, narrow the median/min to
                    # offers matching that condition or better. Otherwise keep
                    # the cross-condition aggregates.
                    if card_cond and card_cond.is_known:
                        ct_median = ct_data.median_for_condition(card_cond) or ct_data.median_price_eur
                        ct_nm_min = ct_data.min_for_condition(card_cond) or ct_data.near_mint_min_eur
                    else:
                        ct_median = ct_data.median_price_eur
                        ct_nm_min = ct_data.near_mint_min_eur
            except Exception as e:
                logger.error(f"CardTrader fetch failed: {e}")

    # 3. eBay (if API configured)
    ebay_avg = None
    ebay_count = 0
    if ebay.is_configured:
        ebay_data = await ebay.get_sold_prices(search_query, marketplace="it")
        ebay_avg = ebay_data.get("avg")
        ebay_count = ebay_data.get("count", 0)

        # Sanity: eBay Browse API returns active listings, not sold.
        # If avg is way above PriceCharting, it's inflated — discard.
        pc_eur = (pc_usd or 0) * (rates.get("EUR", 0.92) if rates else 0.92)
        if ebay_avg and pc_eur > 0 and ebay_avg > pc_eur * 3:
            logger.info(f"eBay avg €{ebay_avg:.0f} too high vs PriceCharting €{pc_eur:.0f}, discarding")
            ebay_avg = None
            ebay_count = 0

    # 4. Italian retrogaming stores (for video games)
    rgs_avg = None
    bits26_avg = None
    if is_videogame:
        rgs_listings = await search_retrogamingshop(search_query, max_results=5)
        if rgs_listings:
            rgs_prices = [l.price_eur for l in rgs_listings]
            rgs_avg = sum(rgs_prices) / len(rgs_prices)

        bits26_avg = await get_26bits_price(search_query)

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
        twentysixbits_avg_eur=bits26_avg,
        cardtrader_median_eur=ct_median,
        cardtrader_nm_min_eur=ct_nm_min,
        cardtrader_offers=ct_offers,
        usd_to_eur_rate=eur_rate,
    )
    fair_value = agg.fair_value_eur

    # Analysis
    df = await get_or_fetch_prices(product.id)
    analysis = analyze(df) if df is not None and len(df) >= 6 else None

    # --- VERDICT ---
    match_em = confidence_emoji(match_confidence)
    lines = [
        f"🔗 *ANALISI ANNUNCIO*\n",
        f"📦 {title}",
        f"🏪 {platform}: *€{price_eur:.2f}*",
        f"{match_em} Match: *{product.name}* _(confidence {match_confidence:.0%})_",
        f"{cond_emoji} Condizione: *{cond_display}*\n",
    ]
    if match_confidence < 0.35:
        lines.insert(4, "⚠ _Match incerto — il prodotto sopra potrebbe non corrispondere all'annuncio._")

    # Bundle/lot warning — the per-item comparisons below are unreliable.
    if bundle.is_bundle:
        bundle_line = f"📦 *LOTTO/BUNDLE rilevato:* {bundle.display_summary or bundle.notes or 'multipli pezzi'}"
        lines.append(bundle_line)
        lines.append("⚠ Le metriche per-pezzo sono indicative — confronta manualmente.\n")

    # Aggregated fair value
    lines.append(format_aggregated_prices(agg))

    # Low confidence warning
    is_low_confidence = agg.confidence == "low"
    only_usa_source = len(agg.sources) <= 1 and all("PriceCharting" in s.source or "TCGPlayer" in s.source for s in agg.sources)

    if is_low_confidence and only_usa_source and is_videogame:
        lines.append(
            "\n⚠ *ATTENZIONE: prezzo basato solo sul mercato USA.*\n"
            "I videogiochi PAL/EU hanno spesso prezzi diversi.\n"
            "Confronta manualmente su RetroGamingShop, eBay.it venduti, Subito.\n"
        )

    # Comparison
    if fair_value > 0:
        diff = ((price_eur - fair_value) / fair_value) * 100
        lines.append("")

        if is_low_confidence and only_usa_source:
            # Don't give strong buy/sell verdicts with only USA data
            if diff < -20:
                lines.append(f"🟡 {abs(diff):.0f}% sotto il mercato USA (potrebbe non riflettere i prezzi EU)")
            elif diff < 5:
                lines.append(f"🟡 Circa al prezzo del mercato USA")
            else:
                lines.append(f"🟠 {diff:.0f}% sopra il mercato USA")
        else:
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

    # Max offer for resale — only with sufficient confidence
    if fair_value > 0 and not (is_low_confidence and only_usa_source):
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


# ─────────────────────────────────────────────────────────────────────────────
# Picker for the /link analyzer — same UX as /evaluate and /offer.
# ─────────────────────────────────────────────────────────────────────────────

_PICKER_NAMESPACE = "link"
_PICKER_PREFIX = "link_pick"


async def _show_link_picker(
    msg, context, candidates, suggested_idx,
    *, listing, expansion_code,
):
    token = stash_picker_state(context, _PICKER_NAMESPACE, {
        "results": candidates,
        "listing": listing,
        "expansion_code": expansion_code,
    })
    kb = build_picker_keyboard(candidates, suggested_idx, _PICKER_PREFIX, token)
    title = listing.get("title", "annuncio")[:60]
    await msg.edit_text(
        f"❓ *Match incerto per l'annuncio*\n_{title}_\n\n"
        f"Trovati più candidati su PriceCharting — scegli quello giusto:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def link_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CallbackQueryHandler entry-point for `link_pick:<token>:<idx|cancel>`."""
    q = update.callback_query
    await q.answer()
    token, choice = parse_picker_callback(q.data)
    if not token or choice is None:
        await q.edit_message_text("⚠ Callback malformato.")
        return

    if choice == "cancel":
        await q.edit_message_text("❌ Analisi annullata.")
        discard_picker_state(context, _PICKER_NAMESPACE, token)
        return

    state = retrieve_picker_state(context, _PICKER_NAMESPACE, token)
    if state is None:
        await q.edit_message_text("⏱ Sessione picker scaduta — rimanda il link.")
        return

    try:
        chosen_idx = int(choice)
    except ValueError:
        await q.edit_message_text("⚠ Indice non valido.")
        return

    results = state["results"]
    if not (0 <= chosen_idx < len(results)):
        await q.edit_message_text("⚠ Indice fuori range.")
        return

    await q.edit_message_text(
        f"🔗 Analizzo *{results[chosen_idx].name}*...",
        parse_mode="Markdown",
    )
    await _finish_link(
        q.message, context, results, chosen_idx, 1.0,
        listing=state["listing"],
        expansion_code=state["expansion_code"],
    )
