"""
/evaluate <nome> <prezzo_offerto> — Analisi completa se conviene acquistare.
Combina: prezzo di mercato, segnale tecnico, previsione, hype, margini di rivendita.
"""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.picker import (
    build_picker_keyboard,
    discard_picker_state,
    parse_picker_callback,
    retrieve_picker_state,
    stash_picker_state,
)

from sqlalchemy import select

from src.analysis.indicators import analyze, Signal, SIGNAL_EMOJI
from src.analysis.prediction import predict_prices
from src.bot.handlers.signal import get_or_fetch_prices
from src.bot.handlers.stats import COMMISSIONS
from src.collectors.pricecharting import PriceChartingCollector
from src.utils.condition import (
    CONDITION_EMOJI,
    CardCondition,
    card_condition_emoji,
    card_condition_to_pc_bucket,
    detect_card_condition,
    detect_condition,
    get_condition_price,
)
from src.utils.query_parser import parse_card_query
from src.utils.llm_parser import (
    detect_bundle,
    enrich_hype_with_sentiment,
    parse_with_llm_fallback,
)
from src.services.users import get_preference
from src.utils.search_match import best_match_with_confidence, confidence_emoji
from src.collectors.reddit import search_hype, calculate_hype_score
from src.collectors.vinted import VintedCollector
from src.db.database import async_session
from src.db.models import Product, ProductCategory
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
            "Es: /evaluate charizard 200 psa10\n"
            "Es: /evaluate charizard 80 nm\n"
            "Es: /evaluate pokemon emerald 25 loose\n"
            "Es: /evaluate metroid fusion 50 cib\n"
            "Es: /evaluate zelda majora 80 senza_manuale\n"
            "Es: /evaluate mario sunshine 20 solo_custodia\n\n"
            "Condizioni videogame: loose/solo_disco, cib, senza_manuale,\n"
            "  solo_custodia, solo_manuale, sealed, graded/wata\n"
            "Condizioni carte: psa10, bgs9.5, nm, ex, gd, lp, pl, po\n"
            "Default: Ungraded / raw NM"
        )
        return

    # Last arg is the price, preceding tokens might be a condition keyword.
    # Single-token shortcuts → canonical PriceCharting bucket label.
    condition_map = {
        # Loose / disc-only
        "loose": "Ungraded", "sfuso": "Ungraded", "cartuccia": "Ungraded",
        "solo_disco": "Ungraded", "solo_cartuccia": "Ungraded",
        "disc_only": "Ungraded",
        # Complete in box
        "cib": "Complete in Box", "completo": "Complete in Box",
        "boxed": "Complete in Box",
        # Missing manual
        "senza_manuale": "Missing Manual", "no_manual": "Missing Manual",
        "missing_manual": "Missing Manual",
        # Box only
        "solo_custodia": "Box Only", "solo_scatola": "Box Only",
        "box_only": "Box Only", "case_only": "Box Only",
        # Manual only
        "solo_manuale": "Manual Only", "manual_only": "Manual Only",
        # Sealed
        "sealed": "New/Sealed", "sigillato": "New/Sealed", "nuovo": "New/Sealed",
        # Graded (videogame grading)
        "graded": "Graded (PSA)", "psa": "Graded (PSA)",
        "wata": "Graded (PSA)", "vga": "Graded (PSA)", "cgc": "Graded (PSA)",
    }
    forced_condition = None
    forced_card_cond: CardCondition | None = None

    try:
        offered_eur = float(args[-1].replace(",", ".").replace("€", "").replace("$", ""))
        query_args = args[:-1]
    except ValueError:
        await update.message.reply_text("L'ultimo argomento deve essere il prezzo in €.")
        return

    # Try to interpret trailing tokens (up to 2) as a card condition first.
    # That captures both single-token forms ("psa10", "nm") and two-token forms
    # ("psa 10", "near mint", "light played").
    if len(query_args) >= 2:
        cc = detect_card_condition(" ".join(query_args[-2:]))
        if cc.is_known:
            forced_card_cond = cc
            query_args = query_args[:-2]
    if forced_card_cond is None and query_args:
        cc = detect_card_condition(query_args[-1])
        if cc.is_known:
            forced_card_cond = cc
            query_args = query_args[:-1]

    # Fallback to the videogame keyword map. Try a 2-token tail first
    # ("senza manuale", "solo custodia", "solo disco") then a single token.
    if forced_card_cond is None and len(query_args) >= 3:
        tail2 = "_".join(query_args[-2:]).lower()
        if tail2 in condition_map:
            forced_condition = condition_map[tail2]
            query_args = query_args[:-2]
    if forced_card_cond is None and forced_condition is None \
            and len(query_args) > 1 and query_args[-1].lower() in condition_map:
        forced_condition = condition_map[query_args[-1].lower()]
        query_args = query_args[:-1]

    if forced_card_cond is not None:
        forced_condition = card_condition_to_pc_bucket(forced_card_cond)

    query = " ".join(query_args)

    # Detect a set hint in the remaining query and rewrite the PriceCharting
    # query to use the canonical English set name. Solves "ex rubino zaffiro
    # charizard" → "Charizard EX Ruby & Sapphire". When the rule-based parser
    # has low confidence (no recognized set, noisy text), we escalate to the
    # Groq LLM so things like "mew wizards of the coast promo" get correctly
    # mapped to Wizards Black Star Promos.
    parsed = await parse_with_llm_fallback(query)
    pc_query = query
    if parsed.expansion:
        bits = []
        if parsed.name:
            bits.append(parsed.name)
        bits.append(parsed.expansion.name_en)
        pc_query = " ".join(bits)
    elif parsed.name and parsed.confidence > 0.5:
        # LLM cleaned the name even without a set match → use that for search.
        pc_query = parsed.name

    msg = await update.message.reply_text(
        f"🔍 Valuto se *{query}* a *€{offered_eur:.2f}* conviene...\n"
        f"Raccolgo dati da tutte le fonti...",
        parse_mode="Markdown",
    )

    # Bundle/lot detection — if the user is asking about a lot, single-item
    # comparisons are misleading. We still run the rest of the analysis but
    # prefix the verdict with a warning and cap the verdict score.
    bundle = await detect_bundle(query)

    # 1. Market price from PriceCharting — get multiple results and pick best match
    results = await pc.search(pc_query, max_results=10)
    if not results and pc_query != query:
        # Refined query failed — retry with the raw user input.
        results = await pc.search(query, max_results=10)
    if not results:
        await msg.edit_text(f"Prodotto '{query}' non trovato su PriceCharting.")
        return

    # Use the refined query for best-match — it discriminates set variants better.
    best_idx, match_confidence = best_match_with_confidence(pc_query, results)

    # If the match is genuinely ambiguous (low confidence + multiple candidates
    # that look similar), let the user pick which product to evaluate rather
    # than silently guessing. Threshold aligned with the 🟡-or-worse emoji
    # band — any non-🟢 match prompts the picker.
    if match_confidence < 0.6 and len(results) >= 2:
        await _show_picker_keyboard(
            msg, context, results[:5], best_idx,
            offered_eur=offered_eur,
            forced_card_cond=forced_card_cond,
            forced_condition=forced_condition,
            query=query,
            pc_query=pc_query,
            bundle=bundle,
        )
        return

    await _finish_evaluate(
        msg, context, results, best_idx, match_confidence,
        offered_eur=offered_eur,
        forced_card_cond=forced_card_cond,
        forced_condition=forced_condition,
        query=query,
        bundle=bundle,
    )


async def _finish_evaluate(
    msg, context, results, best_idx, match_confidence,
    *, offered_eur, forced_card_cond, forced_condition, query, bundle,
):
    """Continue /evaluate with a chosen product. Reused by both the auto-pick
    path (high-confidence best_match) and the user-pick callback."""
    rates = await get_exchange_rates()
    product_result = results[best_idx]

    # Determine the card-vs-game flavour now that we have the product back.
    is_card = product_result.category in (
        ProductCategory.POKEMON, ProductCategory.MAGIC, ProductCategory.YUGIOH,
    )

    # If the product is a card and no card condition was forced, fall back to
    # the user's saved default (default_card_condition in /settings), then to
    # raw NM if no user context is available.
    card_cond: CardCondition | None = None
    if is_card:
        if forced_card_cond is not None:
            card_cond = forced_card_cond
        else:
            user = context.user_data.get("user") if context.user_data else None
            default_grade = get_preference(user, "default_card_condition") if user else "NM"
            card_cond = CardCondition(raw_grade=default_grade)
        detected_condition = card_condition_to_pc_bucket(card_cond)
    else:
        detected_condition = forced_condition or "Ungraded"

    # Get prices by condition
    conditions = await pc.get_all_conditions(product_result.external_id)
    if conditions:
        market_usd, condition_used = get_condition_price(conditions, detected_condition)
    else:
        market_usd = product_result.current_price
        condition_used = "Ungraded"
    market_usd = market_usd or product_result.current_price or 0
    market_eur = usd_to_eur(market_usd, rates) if market_usd else 0

    # Sanity check
    if market_eur > offered_eur * 5 and condition_used != "Ungraded":
        if conditions and "Ungraded" in conditions and conditions["Ungraded"]:
            market_usd = conditions["Ungraded"][-1].price
            condition_used = "Ungraded"
            market_eur = usd_to_eur(market_usd, rates)

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

    # 5. Hype check — raw rule-based score then LLM sentiment enrichment.
    hype_posts = await search_hype(query)
    hype_raw, hype_raw_desc = calculate_hype_score(hype_posts)
    hype = await enrich_hype_with_sentiment(hype_posts, hype_raw, hype_raw_desc)
    hype_score = hype.score

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

    # Hype (sentiment-adjusted via LLM when enough posts are available)
    if hype_score >= 50:
        score += 10
        reasons.append(f"🔥 Hype alto ({hype_score}/100) — domanda forte")
    elif hype_score >= 20:
        reasons.append(f"💬 Hype moderato ({hype_score}/100)")
    else:
        score -= 5
        reasons.append(f"😴 Nessun hype ({hype_score}/100) — potrebbe essere difficile rivendere")
    if hype.has_sentiment:
        # Show sentiment as a separate line so the user knows volume vs vibe.
        if hype.sentiment >= 0.3:
            arrow = "📈"
        elif hype.sentiment <= -0.3:
            arrow = "📉"
        else:
            arrow = "➡️"
        reasons.append(f"  {arrow} _Sentiment: {hype.summary}_")

    # Resale margins
    resale_info = _calculate_resale(offered_eur, market_eur, vinted_avg)
    if resale_info:
        reasons.append(resale_info)

    # --- FINAL VERDICT ---
    # Bundle/lot caveat: single-item comparisons (PriceCharting, Vinted avg,
    # technical signals) all break for lots. Cap the verdict and prepend a
    # warning so the user doesn't trust an over-confident BUY/SELL.
    if bundle.is_bundle:
        score = max(-15, min(15, score))
        reasons.insert(0, f"📦 _Lotto/bundle rilevato: {bundle.display_summary or bundle.notes or ''}_")
        reasons.insert(1, "⚠ Le metriche per-pezzo non sono affidabili sui lotti — usa come ordine di grandezza.")

    score = max(-100, min(100, score))

    if bundle.is_bundle:
        verdict = "📦 *LOTTO/BUNDLE* — valutazione indicativa, non singolo pezzo"
    elif score >= 40:
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
    if card_cond is not None:
        cond_emoji = card_condition_emoji(card_cond)
        cond_display = card_cond.display
    else:
        cond_emoji = CONDITION_EMOJI.get(condition_used, "")
        cond_display = condition_used
    match_em = confidence_emoji(match_confidence)
    match_line = f"{match_em} Match: *{product.name}* _(confidence {match_confidence:.0%})_"
    if match_confidence < 0.35:
        match_line += "\n⚠ _Match incerto — manda più dettagli (set, numero, lingua)._"
    lines = [
        f"💰 *VALUTAZIONE*",
        match_line,
        f"🏷 Prezzo offerto: *€{offered_eur:.2f}*",
        f"{cond_emoji} Condizione: *{cond_display}*\n",
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
        # Show Box Only / Manual Only only when relevant (selected, or no
        # other condition matched) — they clutter the output otherwise.
        niche = {"Box Only", "Manual Only"}
        for cond_name, cond_prices in conditions.items():
            if not cond_prices:
                continue
            if cond_name in niche and cond_name != condition_used:
                continue
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


# ─────────────────────────────────────────────────────────────────────────────
# Top-N picker. State management lives in src/bot/picker.py — this module
# just wires the /evaluate flow through it.
# ─────────────────────────────────────────────────────────────────────────────

_PICKER_NAMESPACE = "evaluate"
_PICKER_PREFIX = "eval_pick"


async def _show_picker_keyboard(
    msg, context, candidates, suggested_idx,
    *, offered_eur, forced_card_cond, forced_condition, query, pc_query, bundle,
):
    """Render the inline keyboard for the top candidates and stash the state."""
    token = stash_picker_state(context, _PICKER_NAMESPACE, {
        "results": candidates,
        "offered_eur": offered_eur,
        "forced_card_cond": forced_card_cond,
        "forced_condition": forced_condition,
        "query": query,
        "pc_query": pc_query,
        "bundle": bundle,
    })
    kb = build_picker_keyboard(candidates, suggested_idx, _PICKER_PREFIX, token)
    await msg.edit_text(
        f"❓ *Match incerto per '{query}'*\n\n"
        f"Trovati più candidati — scegli quello giusto:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def evaluate_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CallbackQueryHandler entry-point for `eval_pick:<token>:<idx|cancel>`."""
    q = update.callback_query
    await q.answer()
    token, choice = parse_picker_callback(q.data)
    if not token or choice is None:
        await q.edit_message_text("⚠ Callback malformato.")
        return

    if choice == "cancel":
        await q.edit_message_text("❌ Valutazione annullata.")
        discard_picker_state(context, _PICKER_NAMESPACE, token)
        return

    state = retrieve_picker_state(context, _PICKER_NAMESPACE, token)
    if state is None:
        await q.edit_message_text("⏱ Sessione picker scaduta — rilancia /evaluate.")
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
        f"🔍 Valuto *{results[chosen_idx].name}*...",
        parse_mode="Markdown",
    )
    # User-confirmed pick → confidence is effectively 1.0 from here on.
    await _finish_evaluate(
        q.message, context, results, chosen_idx, 1.0,
        offered_eur=state["offered_eur"],
        forced_card_cond=state["forced_card_cond"],
        forced_condition=state["forced_condition"],
        query=state["query"],
        bundle=state["bundle"],
    )
