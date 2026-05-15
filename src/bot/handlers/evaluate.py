"""
/evaluate <nome> <prezzo_offerto> — Analisi completa se conviene acquistare.
Combina: prezzo di mercato, segnale tecnico, previsione, hype, margini di rivendita.
"""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.analysis.indicators import analyze, Signal, SIGNAL_EMOJI
from src.analysis.prediction import predict_prices
from src.bot.handlers.signal import get_or_fetch_prices
from src.bot.handlers.stats import COMMISSIONS
from src.collectors.pricecharting import PriceChartingCollector
from src.utils.condition import detect_condition, get_condition_price, CONDITION_EMOJI
from src.collectors.reddit import search_hype, calculate_hype_score
from src.collectors.vinted import VintedCollector
from src.db.database import async_session
from src.db.models import Product
from src.utils.currency import get_exchange_rates, usd_to_eur, eur_to_usd
from src.utils.buy_links import get_buy_links

logger = logging.getLogger(__name__)
pc = PriceChartingCollector()
vinted = VintedCollector()


async def evaluate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /evaluate <nome prodotto> <prezzo in euro>
    Es: /evaluate charizard base set 350
    """
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: /evaluate <nome> <prezzo€> [condizione]\n\n"
            "Es: /evaluate charizard base set 350\n"
            "Es: /evaluate pokemon emerald 25 loose\n"
            "Es: /evaluate metroid fusion 50 cib\n\n"
            "Condizioni: loose, cib, sealed, graded\n"
            "Default: loose (solo cartuccia/carta)"
        )
        return

    # Last arg is the price, second-to-last might be condition
    condition_map = {
        "loose": "Ungraded", "sfuso": "Ungraded", "cartuccia": "Ungraded",
        "cib": "Complete in Box", "completo": "Complete in Box", "boxed": "Complete in Box",
        "sealed": "New/Sealed", "sigillato": "New/Sealed", "nuovo": "New/Sealed",
        "graded": "Graded (PSA)", "psa": "Graded (PSA)",
    }
    forced_condition = None

    try:
        offered_eur = float(args[-1].replace(",", ".").replace("€", "").replace("$", ""))
        query_args = args[:-1]
    except ValueError:
        await update.message.reply_text("L'ultimo argomento deve essere il prezzo in €.")
        return

    # Check if there's a condition keyword before the price
    if len(query_args) > 1 and query_args[-1].lower() in condition_map:
        forced_condition = condition_map[query_args[-1].lower()]
        query_args = query_args[:-1]

    query = " ".join(query_args)

    msg = await update.message.reply_text(
        f"🔍 Valuto se *{query}* a *€{offered_eur:.2f}* conviene...\n"
        f"Raccolgo dati da tutte le fonti...",
        parse_mode="Markdown",
    )

    rates = await get_exchange_rates()

    # 1. Market price from PriceCharting
    results = await pc.search(query, max_results=1)
    if not results:
        await msg.edit_text(f"Prodotto '{query}' non trovato su PriceCharting.")
        return

    product_result = results[0]

    # Get prices by condition
    conditions = await pc.get_all_conditions(product_result.external_id)
    detected_condition = forced_condition or "Ungraded"  # Default to loose
    market_usd, condition_used = get_condition_price(conditions, detected_condition)
    market_usd = market_usd or product_result.current_price or 0
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

    # 2. Technical analysis
    df = await get_or_fetch_prices(product.id)
    analysis = analyze(df) if df is not None and len(df) >= 6 else None

    # 3. Price prediction
    prediction = predict_prices(df) if df is not None and len(df) >= 10 else None

    # 4. Vinted prices (what it actually sells for in EU)
    vinted_listings = await vinted.search_listings(query, max_results=20, order="price_low_to_high")
    vinted_relevant = [l for l in vinted_listings
                       if vinted._title_matches(l.title, query)
                       and not vinted.is_suspicious(l)]
    vinted_prices = [l.price_eur for l in vinted_relevant]
    vinted_min = min(vinted_prices) if vinted_prices else None
    vinted_avg = sum(vinted_prices[:10]) / min(10, len(vinted_prices)) if vinted_prices else None

    # 5. Hype check
    hype_posts = await search_hype(query)
    hype_score, hype_desc = calculate_hype_score(hype_posts)

    # --- BUILD VERDICT ---
    score = 0  # -100 (pessimo affare) to +100 (affare incredibile)
    reasons = []

    # Price vs market
    if market_eur > 0:
        discount_market = ((market_eur - offered_eur) / market_eur) * 100
        if discount_market > 30:
            score += 30
            reasons.append(f"✅ {discount_market:.0f}% sotto il prezzo di mercato (${market_usd:.2f})")
        elif discount_market > 10:
            score += 15
            reasons.append(f"✅ {discount_market:.0f}% sotto mercato")
        elif discount_market > 0:
            score += 5
            reasons.append(f"🟡 Leggermente sotto mercato ({discount_market:.0f}%)")
        elif discount_market > -10:
            score -= 5
            reasons.append(f"🟡 Al prezzo di mercato circa")
        else:
            score -= 20
            reasons.append(f"❌ {abs(discount_market):.0f}% sopra il prezzo di mercato")

    # Price vs Vinted (real market in EU)
    if vinted_avg:
        discount_vinted = ((vinted_avg - offered_eur) / vinted_avg) * 100
        if discount_vinted > 20:
            score += 20
            reasons.append(f"✅ {discount_vinted:.0f}% sotto media Vinted (€{vinted_avg:.2f})")
        elif discount_vinted > 0:
            score += 5
            reasons.append(f"🟡 Sotto media Vinted (€{vinted_avg:.2f})")
        else:
            score -= 15
            reasons.append(f"❌ Sopra media Vinted (€{vinted_avg:.2f})")

    if vinted_min and offered_eur > vinted_min:
        reasons.append(f"⚠ Su Vinted si trova a partire da €{vinted_min:.2f}")

    # Technical signal
    if analysis:
        if analysis.signal in (Signal.BUY, Signal.STRONG_BUY):
            score += 20
            reasons.append(f"✅ Segnale tecnico: {analysis.signal.value} (score: {analysis.score:+.0f})")
        elif analysis.signal == Signal.HOLD:
            reasons.append(f"🟡 Segnale tecnico: HOLD")
        else:
            score -= 15
            reasons.append(f"❌ Segnale tecnico: {analysis.signal.value} — non e' il momento")

        if analysis.is_spike:
            score -= 20
            reasons.append(f"⚠ SPIKE anomalo rilevato — prezzo potrebbe riscendere")

    # Prediction
    if prediction:
        change_90d = ((prediction.pred_90d - prediction.current_price) / prediction.current_price * 100
                      if prediction.current_price > 0 else 0)
        if prediction.trend == "bullish":
            score += 15
            reasons.append(f"✅ Trend previsto: RIALZO ({change_90d:+.1f}% a 90gg)")
        elif prediction.trend == "bearish":
            score -= 15
            reasons.append(f"❌ Trend previsto: RIBASSO ({change_90d:+.1f}% a 90gg)")
        else:
            reasons.append(f"🟡 Trend previsto: laterale ({change_90d:+.1f}% a 90gg)")

    # Hype
    if hype_score >= 50:
        score += 10
        reasons.append(f"🔥 Hype alto ({hype_score}/100) — domanda forte")
    elif hype_score >= 20:
        reasons.append(f"💬 Hype moderato ({hype_score}/100)")
    else:
        score -= 5
        reasons.append(f"😴 Nessun hype ({hype_score}/100) — potrebbe essere difficile rivendere")

    # Resale margins
    resale_info = _calculate_resale(offered_eur, market_eur, vinted_avg)
    if resale_info:
        reasons.append(resale_info)

    # --- FINAL VERDICT ---
    score = max(-100, min(100, score))

    if score >= 40:
        verdict = "🟢🟢 *AFFARE!* Compralo subito!"
    elif score >= 20:
        verdict = "🟢 *BUON ACQUISTO* — prezzo giusto, buone prospettive"
    elif score >= 0:
        verdict = "🟡 *NELLA MEDIA* — non un affare ma nemmeno una fregatura"
    elif score >= -20:
        verdict = "🟠 *CARO* — potresti trovare di meglio"
    else:
        verdict = "🔴 *NON CONVIENE* — prezzo troppo alto o momento sbagliato"

    # --- FORMAT OUTPUT ---
    cond_emoji = CONDITION_EMOJI.get(condition_used, "")
    lines = [
        f"💰 *VALUTAZIONE: {product.name}*",
        f"🏷 Prezzo offerto: *€{offered_eur:.2f}*",
        f"{cond_emoji} Condizione: *{condition_used}*\n",
        f"{verdict}",
        f"📊 Score: *{score:+d}/100*\n",
        "━━━━━━━━━━━━━━━━",
        "*Analisi dettagliata:*\n",
    ]

    for reason in reasons:
        lines.append(f"  {reason}")

    # Reference prices by condition
    lines.append("\n*Prezzi mercato per condizione:*")
    if conditions:
        for cond_name, cond_prices in conditions.items():
            if cond_prices and cond_name not in ("Box Only", "Manual Only"):
                p_eur = usd_to_eur(cond_prices[-1].price, rates)
                marker = " ← *confronto*" if cond_name == condition_used else ""
                lines.append(f"  {cond_name}: €{p_eur:.2f}{marker}")
    if vinted_min:
        lines.append(f"  👗 Vinted minimo: €{vinted_min:.2f}")
    if vinted_avg:
        lines.append(f"  👗 Vinted media: €{vinted_avg:.2f}")

    # Buy links
    links = get_buy_links(product.name, product.category, product.product_url)
    lines.append(f"\n{links}")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


def _calculate_resale(buy_eur: float, market_eur: float, vinted_avg: float | None) -> str | None:
    """Calculate potential resale margins."""
    # Estimate resale price: use vinted_avg if available, otherwise market_eur
    resale_price = vinted_avg or market_eur
    if resale_price <= 0:
        return None

    lines = []
    for platform in ["vinted", "ebay", "cardmarket", "subito"]:
        comm = COMMISSIONS[platform]
        net = resale_price * (1 - comm["rate"]) - comm["fixed"]
        profit = net - buy_eur
        margin = (profit / buy_eur * 100) if buy_eur > 0 else 0

        if margin > 0:
            emoji = "💰"
        else:
            emoji = "📉"

        if platform in ("vinted", "subito"):  # Show only main platforms
            lines.append(f"{emoji} Rivendi su {comm['name'].split('(')[0].strip()}: "
                         f"€{profit:+.2f} ({margin:+.0f}%)")

    if lines:
        return "Margini rivendita stimati:\n    " + "\n    ".join(lines)
    return None
