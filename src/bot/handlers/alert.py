import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.db.database import async_session
from src.db.models import Product, Alert, SignalType

logger = logging.getLogger(__name__)


async def alert_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a BUY alert for a product."""
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
            select(Alert).where(
                Alert.telegram_user_id == user_id,
                Alert.product_id == product_id,
                Alert.signal_type == SignalType.BUY,
                Alert.is_active == True,
            )
        )
        if existing.scalar_one_or_none():
            await query.edit_message_text(
                f"🔔 Hai gia' un alert BUY attivo per *{product.name}*.",
                parse_mode="Markdown",
            )
            return

        session.add(Alert(
            telegram_user_id=user_id,
            product_id=product_id,
            signal_type=SignalType.BUY,
        ))
        await session.commit()

    await query.edit_message_text(
        f"🔔 Alert BUY attivato per *{product.name}*!\n"
        f"Riceverai una notifica quando il segnale diventa BUY o STRONG BUY.",
        parse_mode="Markdown",
    )


async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /alert command - manage alerts."""
    user_id = update.message.from_user.id
    args = context.args or []

    if not args:
        async with async_session() as session:
            result = await session.execute(
                select(Alert, Product)
                .join(Product, Alert.product_id == Product.id)
                .where(Alert.telegram_user_id == user_id, Alert.is_active == True)
                .order_by(Alert.created_at.desc())
            )
            alerts = result.all()

        if not alerts:
            await update.message.reply_text(
                "Non hai alert attivi.\n"
                "Usa /search per trovare un prodotto e poi il pulsante 🔔 Alert BUY.\n"
                "Oppure /alertall <nome> per alert su tutte le varianti."
            )
            return

        lines = [f"🔔 *I tuoi alert attivi ({len(alerts)}):*\n"]
        for alert, product in alerts[:30]:
            price_str = f"${product.current_price:.2f}" if product.current_price else "N/D"
            lines.append(f"• {product.name} - {alert.signal_type.value} ({price_str})")
        if len(alerts) > 30:
            lines.append(f"\n... e altri {len(alerts) - 30}")
        lines.append("\nPer disattivare: /alert off <nome prodotto>")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if args[0].lower() == "off" and len(args) > 1:
        search_term = " ".join(args[1:]).lower()
        async with async_session() as session:
            result = await session.execute(
                select(Alert, Product)
                .join(Product, Alert.product_id == Product.id)
                .where(Alert.telegram_user_id == user_id, Alert.is_active == True)
            )
            alerts = result.all()

            deactivated = 0
            for alert, product in alerts:
                if search_term in product.name.lower():
                    alert.is_active = False
                    deactivated += 1

            await session.commit()

        if deactivated:
            await update.message.reply_text(f"✅ {deactivated} alert disattivato/i.")
        else:
            await update.message.reply_text("Nessun alert trovato con quel nome.")
