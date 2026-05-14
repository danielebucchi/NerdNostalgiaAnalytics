import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.collectors.pricecharting import PriceChartingCollector
from src.db.database import async_session
from src.db.models import Product, PriceAlert

logger = logging.getLogger(__name__)
collector = PriceChartingCollector()


async def pricealert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pricealert <nome> < prezzo  — avvisa quando scende sotto
    /pricealert <nome> > prezzo  — avvisa quando sale sopra
    /pricealert                  — mostra alert attivi
    /pricealert off <nome>       — disattiva price alert
    """
    args = context.args or []

    if not args:
        await _list_price_alerts(update)
        return

    if args[0].lower() == "off":
        await _deactivate_price_alerts(update, " ".join(args[1:]))
        return

    # Parse: /pricealert charizard base set < 400
    text = " ".join(args)
    match = re.match(r'^(.+?)\s*([<>])\s*(\d+\.?\d*)$', text)
    if not match:
        await update.message.reply_text(
            "Uso:\n"
            "  /pricealert charizard base set < 400\n"
            "  /pricealert rayquaza gold star > 200\n"
            "  /pricealert off charizard\n"
            "  /pricealert  (mostra attivi)"
        )
        return

    query = match.group(1).strip()
    direction = "below" if match.group(2) == "<" else "above"
    target_price = float(match.group(3))
    user_id = update.message.from_user.id

    # Search product
    results = await collector.search(query)
    if not results:
        await update.message.reply_text(f"Prodotto '{query}' non trovato.")
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

        session.add(PriceAlert(
            telegram_user_id=user_id,
            product_id=product.id,
            direction=direction,
            target_price=target_price,
        ))
        await session.commit()

    direction_str = "scende sotto" if direction == "below" else "sale sopra"
    current_str = f" (attuale: ${product.current_price:.2f})" if product.current_price else ""

    await update.message.reply_text(
        f"🎯 Price alert impostato!\n\n"
        f"🎴 *{product.name}*{current_str}\n"
        f"📍 Notifica quando {direction_str} *${target_price:.2f}*",
        parse_mode="Markdown",
    )


async def _list_price_alerts(update: Update):
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(PriceAlert, Product)
            .join(Product, PriceAlert.product_id == Product.id)
            .where(PriceAlert.telegram_user_id == user_id, PriceAlert.is_active == True)
            .order_by(PriceAlert.created_at.desc())
        )
        alerts = result.all()

    if not alerts:
        await update.message.reply_text(
            "Nessun price alert attivo.\n"
            "Uso: /pricealert charizard base set < 400"
        )
        return

    lines = [f"🎯 *I tuoi price alert ({len(alerts)}):*\n"]
    for alert, product in alerts:
        symbol = "<" if alert.direction == "below" else ">"
        current = f" (ora: ${product.current_price:.2f})" if product.current_price else ""
        lines.append(f"• {product.name} {symbol} ${alert.target_price:.2f}{current}")

    lines.append("\nPer disattivare: /pricealert off <nome>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _deactivate_price_alerts(update: Update, search_term: str):
    if not search_term:
        await update.message.reply_text("Uso: /pricealert off <nome prodotto>")
        return

    user_id = update.message.from_user.id
    search_lower = search_term.lower()

    async with async_session() as session:
        result = await session.execute(
            select(PriceAlert, Product)
            .join(Product, PriceAlert.product_id == Product.id)
            .where(PriceAlert.telegram_user_id == user_id, PriceAlert.is_active == True)
        )
        alerts = result.all()

        deactivated = 0
        for alert, product in alerts:
            if search_lower in product.name.lower():
                alert.is_active = False
                deactivated += 1
        await session.commit()

    if deactivated:
        await update.message.reply_text(f"✅ {deactivated} price alert disattivato/i.")
    else:
        await update.message.reply_text(f"Nessun price alert trovato per '{search_term}'.")
