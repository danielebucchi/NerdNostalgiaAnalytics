import csv
import io
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

from sqlalchemy import select

from src.analysis.charts import generate_portfolio_chart
from src.db.database import async_session
from src.db.models import Product, PortfolioEntry

logger = logging.getLogger(__name__)

WAITING_BUY_PRICE = 1
WAITING_QUANTITY = 2
WAITING_SELL_PRICE = 3
WAITING_SELL_SELECT = 4


async def portfolio_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start portfolio add conversation."""
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
        return ConversationHandler.END

    context.user_data["portfolio_product_id"] = product.id
    context.user_data["portfolio_product_name"] = product.name

    await query.edit_message_text(
        f"💰 Aggiungi *{product.name}* al portfolio.\n\n"
        f"Inserisci il prezzo di acquisto (es: 45.50):",
        parse_mode="Markdown",
    )
    return WAITING_BUY_PRICE


async def portfolio_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.replace(",", ".").replace("€", "").replace("$", "").strip())
    except ValueError:
        await update.message.reply_text("Prezzo non valido. Inserisci un numero (es: 45.50):")
        return WAITING_BUY_PRICE

    context.user_data["portfolio_buy_price"] = price
    await update.message.reply_text(f"Prezzo: ${price:.2f}\nQuantita'? (default: 1)")
    return WAITING_QUANTITY


async def portfolio_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        quantity = int(text) if text else 1
    except ValueError:
        quantity = 1

    product_id = context.user_data["portfolio_product_id"]
    product_name = context.user_data["portfolio_product_name"]
    buy_price = context.user_data["portfolio_buy_price"]
    user_id = update.message.from_user.id

    async with async_session() as session:
        session.add(PortfolioEntry(
            telegram_user_id=user_id, product_id=product_id,
            buy_price=buy_price, quantity=quantity,
        ))
        await session.commit()

    total = buy_price * quantity
    await update.message.reply_text(
        f"✅ Aggiunto al portfolio!\n\n"
        f"🎴 {product_name}\n"
        f"💵 ${buy_price:.2f} x {quantity} = ${total:.2f}"
    )
    context.user_data.pop("portfolio_product_id", None)
    context.user_data.pop("portfolio_product_name", None)
    context.user_data.pop("portfolio_buy_price", None)
    return ConversationHandler.END


async def portfolio_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ("portfolio_product_id", "portfolio_product_name", "portfolio_buy_price",
                "sell_entries"):
        context.user_data.pop(key, None)
    await update.message.reply_text("Operazione annullata.")
    return ConversationHandler.END


async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show portfolio with P&L."""
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(PortfolioEntry.telegram_user_id == user_id)
            .order_by(PortfolioEntry.buy_date.desc())
        )
        entries = result.all()

    if not entries:
        await update.message.reply_text(
            "Il tuo portfolio e' vuoto.\n"
            "Usa /search, seleziona un prodotto e poi 💰 Aggiungi a Portfolio."
        )
        return

    active = [(e, p) for e, p in entries if not e.sold]
    sold = [(e, p) for e, p in entries if e.sold]

    total_invested = 0.0
    total_current = 0.0
    lines = ["💼 *Portfolio Attivo*\n"]

    for entry, product in active:
        invested = entry.buy_price * entry.quantity
        total_invested += invested
        current_val = (product.current_price or entry.buy_price) * entry.quantity
        total_current += current_val
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0
        emoji = "📈" if pnl >= 0 else "📉"
        current_str = f"${product.current_price:.2f}" if product.current_price else "N/D"
        lines.append(
            f"{emoji} *{product.name}*\n"
            f"   Acquisto: ${entry.buy_price:.2f} x{entry.quantity} | Attuale: {current_str}\n"
            f"   P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
        )

    total_pnl = total_current - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    emoji_t = "📈" if total_pnl >= 0 else "📉"

    lines.append(f"\n━━━━━━━━━━━━━━━━")
    lines.append(f"{emoji_t} *Investito:* ${total_invested:.2f}")
    lines.append(f"{emoji_t} *Valore:* ${total_current:.2f}")
    lines.append(f"{emoji_t} *P&L:* ${total_pnl:+.2f} ({total_pnl_pct:+.1f}%)")

    # Realized P&L from sold items
    if sold:
        realized_pnl = sum(
            (e.sell_price - e.buy_price) * e.quantity for e, p in sold if e.sell_price
        )
        lines.append(f"\n💰 *P&L realizzato (vendite):* ${realized_pnl:+.2f}")
        lines.append(f"📦 Vendite completate: {len(sold)}")

    lines.append(f"\n/sell - registra vendita | /export - esporta CSV")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /sell <nome> <prezzo> - Registra la vendita di un prodotto nel portfolio.
    """
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: /sell <nome prodotto> <prezzo vendita>\n"
            "Es: /sell charizard 500\n\n"
            "Vende il primo match trovato nel tuo portfolio."
        )
        return

    # Last arg is price
    try:
        sell_price = float(args[-1].replace(",", ".").replace("€", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("Prezzo non valido. Ultimo argomento deve essere il prezzo.")
        return

    search_term = " ".join(args[:-1]).lower()
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(
                PortfolioEntry.telegram_user_id == user_id,
                PortfolioEntry.sold == False,
            )
        )
        entries = result.all()

        # Find matching entry
        matched = None
        for entry, product in entries:
            if search_term in product.name.lower():
                matched = (entry, product)
                break

        if not matched:
            await update.message.reply_text(f"Nessun prodotto attivo con '{search_term}' nel portfolio.")
            return

        entry, product = matched
        entry.sold = True
        entry.sell_price = sell_price
        entry.sell_date = datetime.utcnow()
        await session.commit()

    pnl = (sell_price - entry.buy_price) * entry.quantity
    pnl_pct = ((sell_price - entry.buy_price) / entry.buy_price * 100) if entry.buy_price > 0 else 0
    emoji = "📈" if pnl >= 0 else "📉"

    await update.message.reply_text(
        f"💰 *Vendita registrata!*\n\n"
        f"🎴 *{product.name}*\n"
        f"   Acquisto: ${entry.buy_price:.2f} x{entry.quantity}\n"
        f"   Vendita: ${sell_price:.2f}\n"
        f"{emoji} P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
        parse_mode="Markdown",
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /export - Esporta il portfolio in CSV.
    """
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(PortfolioEntry.telegram_user_id == user_id)
            .order_by(PortfolioEntry.buy_date.desc())
        )
        entries = result.all()

    if not entries:
        await update.message.reply_text("Portfolio vuoto, niente da esportare.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Prodotto", "Categoria", "Prezzo Acquisto", "Quantita'",
        "Data Acquisto", "Venduto", "Prezzo Vendita", "Data Vendita",
        "Prezzo Attuale", "P&L", "P&L %", "Note",
    ])

    for entry, product in entries:
        current = product.current_price or 0
        if entry.sold and entry.sell_price:
            pnl = (entry.sell_price - entry.buy_price) * entry.quantity
            pnl_pct = ((entry.sell_price - entry.buy_price) / entry.buy_price * 100) if entry.buy_price > 0 else 0
        else:
            pnl = (current - entry.buy_price) * entry.quantity
            pnl_pct = ((current - entry.buy_price) / entry.buy_price * 100) if entry.buy_price > 0 else 0

        writer.writerow([
            product.name,
            product.category,
            f"{entry.buy_price:.2f}",
            entry.quantity,
            entry.buy_date.strftime("%Y-%m-%d") if entry.buy_date else "",
            "Si" if entry.sold else "No",
            f"{entry.sell_price:.2f}" if entry.sell_price else "",
            entry.sell_date.strftime("%Y-%m-%d") if entry.sell_date else "",
            f"{current:.2f}" if current else "",
            f"{pnl:.2f}",
            f"{pnl_pct:.1f}%",
            entry.notes or "",
        ])

    output.seek(0)
    await update.message.reply_document(
        document=io.BytesIO(output.getvalue().encode("utf-8")),
        filename=f"portfolio_{datetime.now().strftime('%Y%m%d')}.csv",
        caption=f"📄 Portfolio esportato: {len(entries)} voci",
    )


async def portfolio_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /portfoliochart - Grafico andamento portfolio.
    """
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(PortfolioEntry.telegram_user_id == user_id, PortfolioEntry.sold == False)
            .order_by(PortfolioEntry.buy_date.asc())
        )
        entries = result.all()

    if not entries:
        await update.message.reply_text("Portfolio vuoto.")
        return

    # Build cumulative investment/value timeline
    data_points = []
    cumulative_invested = 0.0
    cumulative_current = 0.0

    for entry, product in entries:
        cumulative_invested += entry.buy_price * entry.quantity
        cumulative_current += (product.current_price or entry.buy_price) * entry.quantity
        data_points.append({
            "date": entry.buy_date or datetime.utcnow(),
            "invested": cumulative_invested,
            "value": cumulative_current,
        })

    if len(data_points) < 2:
        await update.message.reply_text("Servono almeno 2 acquisti per il grafico.")
        return

    try:
        chart_bytes = generate_portfolio_chart(data_points)
        pnl = cumulative_current - cumulative_invested
        pnl_pct = (pnl / cumulative_invested * 100) if cumulative_invested > 0 else 0
        emoji = "📈" if pnl >= 0 else "📉"
        await update.message.reply_photo(
            photo=io.BytesIO(chart_bytes),
            caption=f"{emoji} Portfolio: ${cumulative_current:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
        )
    except Exception as e:
        logger.error(f"Portfolio chart failed: {e}")
        await update.message.reply_text(f"Errore generazione grafico: {e}")
