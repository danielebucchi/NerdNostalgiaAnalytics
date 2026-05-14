import io
import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.analysis.charts import generate_chart
from src.bot.handlers.signal import get_or_fetch_prices
from src.collectors.pricecharting import PriceChartingCollector
from src.db.database import async_session
from src.db.models import Product

logger = logging.getLogger(__name__)
collector = PriceChartingCollector()


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /chart <query> command."""
    if not context.args:
        await update.message.reply_text("Uso: /chart <nome prodotto>\nEs: /chart charizard base set")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"📈 Genero grafico per '{query}'...")

    results = await collector.search(query)
    if not results:
        await update.message.reply_text("Prodotto non trovato.")
        return

    product_result = results[0]

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

    df = await get_or_fetch_prices(product.id)
    if df is None or len(df) < 5:
        await update.message.reply_text("Dati insufficienti per generare il grafico.")
        return

    try:
        chart_bytes = generate_chart(df, product.name)
        caption = f"📈 {product.name}"
        if product.current_price:
            caption += f" - ${product.current_price:.2f}"
        await update.message.reply_photo(photo=io.BytesIO(chart_bytes), caption=caption)
    except Exception as e:
        logger.error(f"Chart generation failed: {e}")
        await update.message.reply_text(f"Errore nella generazione del grafico: {e}")


async def chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle chart button callback."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split(":")[1])

    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()

    if not product:
        await query.edit_message_text("Prodotto non trovato.")
        return

    await query.edit_message_text(f"⏳ Genero grafico per {product.name}...")

    df = await get_or_fetch_prices(product.id)
    if df is None or len(df) < 5:
        await query.edit_message_text("Dati insufficienti per il grafico.")
        return

    try:
        chart_bytes = generate_chart(df, product.name)
        caption = f"📈 {product.name}"
        if product.current_price:
            caption += f" - ${product.current_price:.2f}"
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=io.BytesIO(chart_bytes),
            caption=caption,
        )
    except Exception as e:
        logger.error(f"Chart generation failed: {e}")
        await query.edit_message_text(f"Errore: {e}")
