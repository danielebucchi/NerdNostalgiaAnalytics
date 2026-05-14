import logging

import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.analysis.indicators import analyze, format_analysis, Signal
from src.collectors.pricecharting import PriceChartingCollector
from src.db.database import async_session
from src.db.models import Product, PriceHistory
from src.utils.buy_links import get_buy_links

logger = logging.getLogger(__name__)
collector = PriceChartingCollector()


async def get_or_fetch_prices(product_id: int) -> pd.DataFrame | None:
    """Get price history from DB, or fetch from source if not available."""
    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()
        if not product:
            return None

        # Check if we have prices in DB
        prices_result = await session.execute(
            select(PriceHistory)
            .where(PriceHistory.product_id == product.id)
            .order_by(PriceHistory.date.asc())
        )
        prices = prices_result.scalars().all()

        if len(prices) >= 14:
            return pd.DataFrame([{"date": p.date, "price": p.price, "volume": p.volume} for p in prices])

        external_id = product.external_id
        source = product.source

    # Fetch from source
    history = await collector.get_price_history(external_id)
    if not history.prices:
        return None

    # Save to DB
    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()
        if product:
            for pp in history.prices:
                existing = await session.execute(
                    select(PriceHistory).where(
                        PriceHistory.product_id == product.id,
                        PriceHistory.date == pp.date,
                        PriceHistory.source == source,
                    )
                )
                if not existing.scalar_one_or_none():
                    session.add(PriceHistory(
                        product_id=product.id,
                        date=pp.date,
                        price=pp.price,
                        volume=pp.volume,
                        source=source,
                    ))
            if history.prices:
                product.current_price = history.prices[-1].price
            await session.commit()

    return pd.DataFrame([{"date": p.date, "price": p.price, "volume": p.volume} for p in history.prices])


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /signal <query> command."""
    if not context.args:
        await update.message.reply_text("Uso: /signal <nome prodotto>\nEs: /signal charizard base set")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Analizzo '{query}'...")

    results = await collector.search(query)
    if not results:
        await update.message.reply_text("Prodotto non trovato.")
        return

    product_result = results[0]

    # Save to DB and get ID
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
    if df is None or len(df) < 14:
        count = len(df) if df is not None else 0
        await update.message.reply_text(
            f"📊 *{product.name}*\n\n"
            f"Dati insufficienti per l'analisi ({count} data points, minimo 14).\n"
            f"Il prezzo verra' monitorato d'ora in poi.",
            parse_mode="Markdown",
        )
        return

    analysis = analyze(df)
    if not analysis:
        await update.message.reply_text("Impossibile analizzare questo prodotto.")
        return

    text = f"📊 *{product.name}*\n\n```\n{format_analysis(analysis)}\n```"
    if analysis.signal in (Signal.BUY, Signal.STRONG_BUY):
        links = get_buy_links(product.name, product.category, product.product_url)
        text += f"\n\nCompra qui: {links}"
    await update.message.reply_text(text, parse_mode="Markdown")


async def signal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle signal button callback."""
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

    await query.edit_message_text(f"⏳ Analizzo {product.name}...")

    df = await get_or_fetch_prices(product.id)
    if df is None or len(df) < 14:
        count = len(df) if df is not None else 0
        await query.edit_message_text(
            f"📊 *{product.name}*\n\nDati insufficienti ({count} data points, minimo 14).",
            parse_mode="Markdown",
        )
        return

    analysis = analyze(df)
    if not analysis:
        await query.edit_message_text("Impossibile analizzare.")
        return

    text = f"📊 *{product.name}*\n\n```\n{format_analysis(analysis)}\n```"
    await query.edit_message_text(text, parse_mode="Markdown")
