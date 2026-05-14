import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.analysis.indicators import analyze, SIGNAL_EMOJI, Signal
from src.bot.handlers.signal import get_or_fetch_prices
from src.db.database import async_session
from src.utils.buy_links import get_buy_links
from src.db.models import Product, WatchlistEntry

logger = logging.getLogger(__name__)


async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trending — mostra i maggiori rialzi e ribassi tra i prodotti in watchlist.
    """
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Product)
            .join(WatchlistEntry, WatchlistEntry.product_id == Product.id)
            .where(WatchlistEntry.telegram_user_id == user_id)
        )
        products = result.scalars().all()

    if not products:
        await update.message.reply_text(
            "Nessun prodotto in watchlist. Usa /watchall <nome> per aggiungere prodotti."
        )
        return

    msg = await update.message.reply_text(
        f"📊 Analizzo {len(products)} prodotti in watchlist..."
    )

    movers = []
    for product in products:
        try:
            df = await get_or_fetch_prices(product.id)
            if df is None or len(df) < 6:
                continue
            analysis = analyze(df)
            if not analysis:
                continue
            change = analysis.price_change_short
            if change is not None:
                movers.append((product, analysis, change))
        except Exception as e:
            logger.debug(f"Failed to analyze {product.name}: {e}")
            continue

    if not movers:
        await msg.edit_text("Dati insufficienti per calcolare i trend.")
        return

    # Sort by change
    movers.sort(key=lambda x: x[2], reverse=True)

    lines = ["📈 *TOP RIALZI*\n"]
    for product, analysis, change in movers[:10]:
        emoji = SIGNAL_EMOJI.get(analysis.signal, "")
        lines.append(
            f"{emoji} *{product.name}*\n"
            f"   ${analysis.current_price:.2f} ({change:+.1f}%) - {analysis.signal.value}"
        )

    lines.append("\n📉 *TOP RIBASSI*\n")
    for product, analysis, change in reversed(movers[-10:]):
        emoji = SIGNAL_EMOJI.get(analysis.signal, "")
        lines.append(
            f"{emoji} *{product.name}*\n"
            f"   ${analysis.current_price:.2f} ({change:+.1f}%) - {analysis.signal.value}"
        )

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def opportunities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /opportunities — mostra tutti i prodotti in watchlist con segnale BUY o STRONG BUY.
    """
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Product)
            .join(WatchlistEntry, WatchlistEntry.product_id == Product.id)
            .where(WatchlistEntry.telegram_user_id == user_id)
        )
        products = result.scalars().all()

    if not products:
        await update.message.reply_text(
            "Nessun prodotto in watchlist. Usa /watchall <nome> per aggiungere prodotti."
        )
        return

    msg = await update.message.reply_text(
        f"🔍 Cerco opportunita' tra {len(products)} prodotti..."
    )

    buy_signals = []
    for product in products:
        try:
            df = await get_or_fetch_prices(product.id)
            if df is None or len(df) < 6:
                continue
            analysis = analyze(df)
            if analysis and analysis.signal in (Signal.BUY, Signal.STRONG_BUY):
                buy_signals.append((product, analysis))
        except Exception as e:
            logger.debug(f"Failed to analyze {product.name}: {e}")
            continue

    if not buy_signals:
        await msg.edit_text(
            "🟡 Nessuna opportunita' di acquisto al momento.\n"
            "Tutti i prodotti monitorati sono in HOLD o SELL."
        )
        return

    # Sort by score descending
    buy_signals.sort(key=lambda x: x[1].score, reverse=True)

    lines = [f"🟢 *OPPORTUNITA' DI ACQUISTO ({len(buy_signals)})*\n"]

    for product, analysis in buy_signals:
        emoji = SIGNAL_EMOJI.get(analysis.signal, "")
        spike_warn = " ⚠ SPIKE" if analysis.is_spike else ""
        rsi_str = f" | RSI: {analysis.rsi:.1f}" if analysis.rsi else ""

        lines.append(
            f"{emoji} *{product.name}*{spike_warn}\n"
            f"   ${analysis.current_price:.2f} | Score: {analysis.score:+.0f}{rsi_str}"
        )

        # Top 3 reasons
        reasons = [d for d in analysis.details if d != analysis.details[0]][:3]
        for r in reasons:
            lines.append(f"   → {r}")
        links = get_buy_links(product.name, product.category, product.product_url)
        lines.append(f"   {links}")
        lines.append("")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")
