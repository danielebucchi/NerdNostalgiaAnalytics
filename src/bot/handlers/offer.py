"""
/offer <nome> [margine%] — Calcola quanto offrire per un prodotto.
Tiene conto di: prezzo mercato, Vinted, trend, commissioni rivendita.
Default margine: 30%.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.analysis.indicators import analyze, Signal
from src.analysis.prediction import predict_prices
from src.bot.handlers.signal import get_or_fetch_prices
from src.bot.handlers.stats import COMMISSIONS
from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.vinted import VintedCollector
from src.utils.search_match import best_match_with_confidence, confidence_emoji
from src.db.database import async_session
from src.db.models import Product, ProductCategory
from src.utils.condition import (
    CardCondition,
    card_condition_emoji,
    detect_card_condition,
)
from src.utils.currency import get_exchange_rates, usd_to_eur
from src.utils.query_parser import parse_card_query
from src.utils.llm_parser import parse_with_llm_fallback
from src.services.users import get_preference

logger = logging.getLogger(__name__)
pc = PriceChartingCollector()
vinted = VintedCollector()


async def offer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /offer <nome> [margine%]
    Es: /offer charizard base set        (default 30%)
    Es: /offer pokemon emerald 40        (40% margine)
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Uso: /offer <nome prodotto> [condizione] [margine%]\n\n"
            "Es: /offer charizard base set\n"
            "Es: /offer pokemon emerald 40\n"
            "Es: /offer charizard psa10 40\n"
            "Es: /offer charizard nm\n\n"
            "Condizioni carte: psa10, bgs9.5, nm, ex, gd, lp, pl, po\n"
            "Calcola il prezzo massimo da offrire per avere il margine desiderato (default 30%)."
        )
        return

    # Per-user default margin from /settings, falling back to 30% when no user
    # context is available (e.g. middleware skipped this update for some reason).
    user = context.user_data.get("user") if context.user_data else None
    target_margin = float(get_preference(user, "default_margin_pct")) if user else 30.0
    product_args = list(args)

    # Pop trailing margin if it's a plain number 1–90 and not part of a grade.
    # "psa 10" must NOT have the 10 swallowed as margin: protect by checking
    # the preceding token isn't a grading company.
    grading_companies = {
        "psa", "bgs", "cgc", "sgc", "hga", "gma", "ace", "tag", "beckett",
    }
    if product_args:
        try:
            maybe_margin = float(product_args[-1].replace("%", "").replace(",", "."))
            prev = product_args[-2].lower() if len(product_args) >= 2 else ""
            if 1 <= maybe_margin <= 90 and prev not in grading_companies:
                target_margin = maybe_margin
                product_args = product_args[:-1]
        except ValueError:
            pass

    # Pop trailing card condition. Try a 2-token match first ("psa 10",
    # "near mint", "light played"), then fall back to single token.
    forced_card_cond: CardCondition | None = None
    if len(product_args) >= 2:
        cc = detect_card_condition(" ".join(product_args[-2:]))
        if cc.is_known:
            forced_card_cond = cc
            product_args = product_args[:-2]
    if forced_card_cond is None and product_args:
        cc = detect_card_condition(product_args[-1])
        if cc.is_known:
            forced_card_cond = cc
            product_args = product_args[:-1]

    if not product_args:
        await update.message.reply_text("Specifica il nome del prodotto.")
        return

    query = " ".join(product_args)

    # Detect set hint and rewrite the search query — "ex rubino zaffiro charizard"
    # becomes "charizard EX Ruby & Sapphire" so PriceCharting matches better.
    # LLM fallback kicks in when rule-based confidence is low.
    parsed = await parse_with_llm_fallback(query)
    pc_query = query
    if parsed.expansion:
        bits = []
        if parsed.name:
            bits.append(parsed.name)
        bits.append(parsed.expansion.name_en)
        pc_query = " ".join(bits)
    elif parsed.name and parsed.confidence > 0.5:
        pc_query = parsed.name

    msg = await update.message.reply_text(
        f"🧮 Calcolo offerta per '{query}' (margine {target_margin:.0f}%)..."
    )

    rates = await get_exchange_rates()

    # 1. Market price
    results = await pc.search(pc_query, max_results=10)
    if not results and pc_query != query:
        results = await pc.search(query, max_results=10)
    if not results:
        await msg.edit_text(f"Prodotto '{query}' non trovato.")
        return

    best_idx, match_confidence = best_match_with_confidence(pc_query, results)
    product_result = results[best_idx]
    market_usd = product_result.current_price or 0
    market_eur = usd_to_eur(market_usd, rates) if market_usd else 0

    # Save to DB
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

    # 2. Vinted prices (actual EU selling prices)
    vinted_listings = await vinted.search_listings(query, max_results=20, order="price_low_to_high")
    vinted_relevant = [l for l in vinted_listings
                       if vinted._title_matches(l.title, query)
                       and not vinted.is_suspicious(l)]
    vinted_prices = [l.price_eur for l in vinted_relevant]
    vinted_avg = sum(vinted_prices[:10]) / min(10, len(vinted_prices)) if vinted_prices else None

    # 3. Technical analysis + prediction
    df = await get_or_fetch_prices(product.id)
    analysis = analyze(df) if df is not None and len(df) >= 6 else None
    prediction = predict_prices(df) if df is not None and len(df) >= 10 else None

    # If the matched product is a card, default to the user's saved raw grade
    # (from /settings → default_card_condition) unless the user forced one in
    # the command. For non-card products keep no card_cond.
    is_card = product.category in (
        ProductCategory.POKEMON, ProductCategory.MAGIC, ProductCategory.YUGIOH,
    )
    card_cond: CardCondition | None = None
    if forced_card_cond is not None:
        card_cond = forced_card_cond
    elif is_card:
        default_grade = get_preference(user, "default_card_condition") if user else "NM"
        card_cond = CardCondition(raw_grade=default_grade)

    # --- CALCULATE OFFER PRICES ---
    # Base resale price: best estimate of what we can actually sell for, scaled
    # down for lower-grade raw cards.
    resale_eur = _estimate_resale_price(market_eur, vinted_avg, analysis, prediction, card_cond)

    if resale_eur <= 0:
        await msg.edit_text("Dati insufficienti per calcolare un'offerta.")
        return

    margin_multiplier = target_margin / 100

    match_em = confidence_emoji(match_confidence)
    lines = [
        f"🧮 *OFFERTA CONSIGLIATA*",
        f"{match_em} Match: *{product.name}* _(confidence {match_confidence:.0%})_",
        f"🎯 Margine target: *{target_margin:.0f}%*\n",
    ]
    if match_confidence < 0.35:
        lines.insert(2, "⚠ _Match incerto — manda più dettagli (set, numero, lingua)._\n")
    if card_cond is not None:
        lines.append(f"{card_condition_emoji(card_cond)} Condizione: *{card_cond.display}*\n")

    # Calculate max offer per platform
    lines.append("*Prezzo max da offrire (per piattaforma di rivendita):*\n")

    offers = {}
    for platform, comm in COMMISSIONS.items():
        # net_after_commission = resale * (1 - rate) - fixed
        # profit = net - buy_price
        # We want: profit / buy_price >= margin
        # So: net - buy >= buy * margin
        # buy * (1 + margin) <= net
        # buy <= net / (1 + margin)
        net = resale_eur * (1 - comm["rate"]) - comm["fixed"]
        max_buy = net / (1 + margin_multiplier)
        max_buy = max(0, max_buy)
        offers[platform] = max_buy

        profit = net - max_buy
        lines.append(
            f"  🏪 *Rivendi su {comm['name'].split('(')[0].strip()}*\n"
            f"     Offri max: *€{max_buy:.2f}*\n"
            f"     Rivendi a: €{resale_eur:.2f} → netto €{net:.2f} → profitto €{profit:.2f}"
        )

    # Best offer (highest, meaning cheapest platform to resell)
    best_platform = max(offers, key=offers.get)
    best_offer = offers[best_platform]

    # Aggressive offer (50% more margin for negotiation)
    aggressive_offer = best_offer * 0.80  # Start 20% lower for negotiation room

    lines.insert(2,
        f"\n💰 *Offri: €{aggressive_offer:.2f} - €{best_offer:.2f}*\n"
        f"   (Parti da €{aggressive_offer:.2f}, sali fino a €{best_offer:.2f} max)\n"
    )

    # Context
    lines.append("\n*Prezzi di riferimento:*")
    if market_eur > 0:
        lines.append(f"  📈 Mercato USA: €{market_eur:.2f}")
    if vinted_avg:
        lines.append(f"  👗 Media Vinted: €{vinted_avg:.2f}")

    # Trend
    if analysis:
        emoji = "📈" if analysis.signal in (Signal.BUY, Signal.STRONG_BUY) else "📉" if analysis.signal in (Signal.SELL, Signal.STRONG_SELL) else "➡️"
        lines.append(f"  {emoji} Segnale: {analysis.signal.value}")

    if prediction:
        change = ((prediction.pred_90d - prediction.current_price) / prediction.current_price * 100
                  if prediction.current_price > 0 else 0)
        lines.append(f"  🔮 Previsione 90gg: {change:+.1f}%")
        if prediction.trend == "bullish":
            lines.append(f"  💡 _Trend in salita: puoi permetterti di offrire di piu'_")
        elif prediction.trend == "bearish":
            lines.append(f"  💡 _Trend in discesa: stai basso con l'offerta_")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


# How much a raw card is worth as a fraction of NM-equivalent price.
# Graded cards keep their full priced multiplier (premium baked in upstream).
_RAW_GRADE_MULTIPLIER = {
    "NM": 1.00,
    "EX": 0.75,
    "GO": 0.55,
    "LP": 0.45,
    "PL": 0.30,
    "PO": 0.15,
}


def _estimate_resale_price(
    market_eur: float,
    vinted_avg: float | None,
    analysis=None,
    prediction=None,
    card_cond: CardCondition | None = None,
) -> float:
    """
    Estimate realistic resale price combining multiple sources.
    Vinted average is weighted more because it's the actual EU selling price.
    For raw cards below NM, apply a condition multiplier to the result
    (reference prices assume NM-equivalent grade).
    """
    prices = []
    weights = []

    if vinted_avg and vinted_avg > 0:
        prices.append(vinted_avg)
        weights.append(3)  # Most reliable for EU resale

    if market_eur > 0:
        prices.append(market_eur)
        weights.append(2)

    if not prices:
        return 0

    # Weighted average
    resale = sum(p * w for p, w in zip(prices, weights)) / sum(weights)

    # Adjust for trend
    if prediction:
        if prediction.trend == "bullish":
            resale *= 1.05  # Can expect slightly more
        elif prediction.trend == "bearish":
            resale *= 0.90  # Be conservative

    # Adjust for signal
    if analysis:
        if analysis.signal == Signal.STRONG_SELL:
            resale *= 0.85
        elif analysis.signal == Signal.SELL:
            resale *= 0.92

    # Card condition: scale down for lower-grade raw. Graded passes through.
    if card_cond and not card_cond.is_graded and card_cond.raw_grade:
        resale *= _RAW_GRADE_MULTIPLIER.get(card_cond.raw_grade, 1.0)

    return resale
