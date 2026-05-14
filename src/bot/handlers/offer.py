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
from src.db.database import async_session
from src.db.models import Product
from src.utils.currency import get_exchange_rates, usd_to_eur

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
            "Uso: /offer <nome prodotto> [margine%]\n\n"
            "Es: /offer charizard base set\n"
            "Es: /offer pokemon emerald 40\n\n"
            "Calcola il prezzo massimo da offrire per avere il margine desiderato (default 30%)."
        )
        return

    # Check if last arg is a number (margin)
    target_margin = 30.0
    product_args = list(args)
    try:
        maybe_margin = float(product_args[-1].replace("%", "").replace(",", "."))
        if 1 <= maybe_margin <= 90:
            target_margin = maybe_margin
            product_args = product_args[:-1]
    except ValueError:
        pass

    if not product_args:
        await update.message.reply_text("Specifica il nome del prodotto.")
        return

    query = " ".join(product_args)

    msg = await update.message.reply_text(
        f"🧮 Calcolo offerta per '{query}' (margine {target_margin:.0f}%)..."
    )

    rates = await get_exchange_rates()

    # 1. Market price
    results = await pc.search(query, max_results=1)
    if not results:
        await msg.edit_text(f"Prodotto '{query}' non trovato.")
        return

    product_result = results[0]
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

    # --- CALCULATE OFFER PRICES ---
    # Base resale price: best estimate of what we can actually sell for
    resale_eur = _estimate_resale_price(market_eur, vinted_avg, analysis, prediction)

    if resale_eur <= 0:
        await msg.edit_text("Dati insufficienti per calcolare un'offerta.")
        return

    margin_multiplier = target_margin / 100

    lines = [
        f"🧮 *OFFERTA CONSIGLIATA: {product.name}*\n",
        f"🎯 Margine target: *{target_margin:.0f}%*\n",
    ]

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


def _estimate_resale_price(
    market_eur: float,
    vinted_avg: float | None,
    analysis=None,
    prediction=None,
) -> float:
    """
    Estimate realistic resale price combining multiple sources.
    Vinted average is weighted more because it's the actual EU selling price.
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

    return resale
