import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select, delete

from src.analysis.indicators import analyze, SIGNAL_EMOJI
from src.bot.handlers.signal import get_or_fetch_prices
from src.bot.keyboards import watchlist_item_keyboard
from src.db.database import async_session
from src.db.models import Product, WatchlistEntry

logger = logging.getLogger(__name__)


async def watch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add product to watchlist."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split(":")[1])
    user_id = query.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()
        if not product:
            await query.edit_message_text("Prodotto non trovato.")
            return

        existing = await session.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.telegram_user_id == user_id,
                WatchlistEntry.product_id == product_id,
            )
        )
        if existing.scalar_one_or_none():
            await query.edit_message_text(f"👁 *{product.name}* e' gia' nella tua watchlist.", parse_mode="Markdown")
            return

        session.add(WatchlistEntry(telegram_user_id=user_id, product_id=product_id))
        await session.commit()

    await query.edit_message_text(f"✅ *{product.name}* aggiunto alla watchlist!", parse_mode="Markdown")


async def unwatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove product from watchlist."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split(":")[1])
    user_id = query.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()
        if not product:
            await query.edit_message_text("Prodotto non trovato.")
            return

        await session.execute(
            delete(WatchlistEntry).where(
                WatchlistEntry.telegram_user_id == user_id,
                WatchlistEntry.product_id == product_id,
            )
        )
        await session.commit()

    await query.edit_message_text(f"❌ *{product.name}* rimosso dalla watchlist.", parse_mode="Markdown")


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /watchlist command - show all watched products with signals."""
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(WatchlistEntry, Product)
            .join(Product, WatchlistEntry.product_id == Product.id)
            .where(WatchlistEntry.telegram_user_id == user_id)
            .order_by(WatchlistEntry.added_at.desc())
        )
        entries = result.all()

    if not entries:
        await update.message.reply_text("La tua watchlist e' vuota. Usa /search per trovare prodotti da monitorare.")
        return

    # Send summary first
    await update.message.reply_text(f"📋 La tua watchlist ({len(entries)} prodotti):")

    # Send in batches to avoid flooding
    batch_lines = []
    for entry, product in entries:
        price_str = f"${product.current_price:.2f}" if product.current_price else "N/D"
        batch_lines.append(f"• {product.name} - {price_str}")

        if len(batch_lines) >= 20:
            await update.message.reply_text("\n".join(batch_lines))
            batch_lines = []

    if batch_lines:
        await update.message.reply_text("\n".join(batch_lines))

    await update.message.reply_text(
        "Per analizzare un prodotto specifico usa /signal <nome>\n"
        "Per rimuovere tutto: /unwatchall <nome>"
    )
