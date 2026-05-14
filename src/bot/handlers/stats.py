"""
Sales stats, target price calculator, and backup commands.
"""
import io
import logging
import os
import shutil
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select, func

from src.db.database import async_session
from src.db.models import Product, PortfolioEntry

logger = logging.getLogger(__name__)

# Platform commission rates
COMMISSIONS = {
    "vinted": {"rate": 0.05, "fixed": 0.70, "name": "Vinted (5% + €0.70)"},
    "ebay": {"rate": 0.13, "fixed": 0.35, "name": "eBay (13% + €0.35)"},
    "cardmarket": {"rate": 0.05, "fixed": 0.0, "name": "Cardmarket (5%)"},
    "subito": {"rate": 0.0, "fixed": 0.0, "name": "Subito (gratis)"},
    "wallapop": {"rate": 0.0, "fixed": 0.0, "name": "Wallapop (gratis di persona)"},
}


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats - Statistiche vendite personali.
    """
    user_id = update.message.from_user.id

    async with async_session() as session:
        # Active positions
        active = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(PortfolioEntry.telegram_user_id == user_id, PortfolioEntry.sold == False)
        )
        active_entries = active.all()

        # Sold positions
        sold = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(PortfolioEntry.telegram_user_id == user_id, PortfolioEntry.sold == True)
        )
        sold_entries = sold.all()

    if not active_entries and not sold_entries:
        await update.message.reply_text("Nessun dato nel portfolio. Usa /search e aggiungi prodotti.")
        return

    lines = ["📊 *Statistiche Portfolio*\n"]

    # Active summary
    total_invested = sum(e.buy_price * e.quantity for e, p in active_entries)
    total_current = sum((p.current_price or e.buy_price) * e.quantity for e, p in active_entries)
    unrealized_pnl = total_current - total_invested

    lines.append(f"*Posizioni attive:* {len(active_entries)}")
    lines.append(f"Investito: €{total_invested:.2f}")
    lines.append(f"Valore: €{total_current:.2f}")
    pnl_pct = (unrealized_pnl / total_invested * 100) if total_invested > 0 else 0
    emoji = "📈" if unrealized_pnl >= 0 else "📉"
    lines.append(f"{emoji} P&L non realizzato: €{unrealized_pnl:+.2f} ({pnl_pct:+.1f}%)")

    if sold_entries:
        lines.append(f"\n*Vendite completate:* {len(sold_entries)}")
        total_buy = sum(e.buy_price * e.quantity for e, p in sold_entries)
        total_sell = sum((e.sell_price or 0) * e.quantity for e, p in sold_entries)
        realized_pnl = total_sell - total_buy
        realized_pct = (realized_pnl / total_buy * 100) if total_buy > 0 else 0
        emoji_r = "📈" if realized_pnl >= 0 else "📉"
        lines.append(f"Totale venduto: €{total_sell:.2f}")
        lines.append(f"{emoji_r} P&L realizzato: €{realized_pnl:+.2f} ({realized_pct:+.1f}%)")

        # Average margin
        margins = []
        for e, p in sold_entries:
            if e.sell_price and e.buy_price > 0:
                margin = (e.sell_price - e.buy_price) / e.buy_price * 100
                margins.append(margin)
        if margins:
            avg_margin = sum(margins) / len(margins)
            best_margin = max(margins)
            worst_margin = min(margins)
            lines.append(f"\nMargine medio: {avg_margin:+.1f}%")
            lines.append(f"Miglior margine: {best_margin:+.1f}%")
            lines.append(f"Peggior margine: {worst_margin:+.1f}%")

        # Average hold time
        hold_times = []
        for e, p in sold_entries:
            if e.sell_date and e.buy_date:
                days = (e.sell_date - e.buy_date).days
                hold_times.append(days)
        if hold_times:
            avg_days = sum(hold_times) / len(hold_times)
            lines.append(f"Tempo medio di vendita: {avg_days:.0f} giorni")

        # Most profitable products
        profits = []
        for e, p in sold_entries:
            if e.sell_price:
                profit = (e.sell_price - e.buy_price) * e.quantity
                profits.append((p.name, profit))
        profits.sort(key=lambda x: x[1], reverse=True)

        if profits:
            lines.append("\n*Top 3 piu' profittevoli:*")
            for name, profit in profits[:3]:
                lines.append(f"  {'📈' if profit >= 0 else '📉'} {name[:35]} → €{profit:+.2f}")

            lines.append("\n*Top 3 peggiori:*")
            for name, profit in profits[-3:]:
                lines.append(f"  {'📈' if profit >= 0 else '📉'} {name[:35]} → €{profit:+.2f}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def target_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /target <prezzo_acquisto> <margine%> - Calcola prezzo vendita per ogni piattaforma.
    Es: /target 50 30  (comprato a €50, voglio 30% margine)
    """
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: /target <prezzo\\_acquisto> <margine%>\n"
            "Es: /target 50 30\n"
            "→ Prezzo vendita su ogni piattaforma per avere 30% di margine su €50",
            parse_mode="Markdown",
        )
        return

    try:
        buy_price = float(args[0].replace(",", ".").replace("€", "").replace("$", ""))
        target_margin = float(args[1].replace(",", ".").replace("%", ""))
    except ValueError:
        await update.message.reply_text("Valori non validi. Uso: /target 50 30")
        return

    target_profit = buy_price * (target_margin / 100)
    target_net = buy_price + target_profit

    lines = [
        f"🎯 *Target Price Calculator*\n",
        f"💰 Prezzo acquisto: €{buy_price:.2f}",
        f"📊 Margine desiderato: {target_margin:.0f}%",
        f"💵 Profitto netto target: €{target_profit:.2f}\n",
        f"*Prezzo di vendita per piattaforma:*\n",
    ]

    for platform, comm in COMMISSIONS.items():
        rate = comm["rate"]
        fixed = comm["fixed"]
        # sell_price - (sell_price * rate + fixed) = target_net
        # sell_price * (1 - rate) = target_net + fixed
        if (1 - rate) > 0:
            sell_price = (target_net + fixed) / (1 - rate)
        else:
            sell_price = target_net + fixed

        commission = sell_price * rate + fixed
        lines.append(
            f"🏪 *{comm['name']}*\n"
            f"   Vendi a: *€{sell_price:.2f}*\n"
            f"   Commissione: €{commission:.2f} | Netto: €{sell_price - commission:.2f}"
        )

    lines.append(
        f"\n💡 Il prezzo piu' basso lo ottieni su Subito/Wallapop (no commissioni)."
    )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /backup - Invia il database come file.
    """
    db_path = "nerd_nostalgia.db"
    if not os.path.exists(db_path):
        await update.message.reply_text("Database non trovato.")
        return

    try:
        # Copy to temp file to avoid locking issues
        backup_path = f"/tmp/nerd_nostalgia_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(db_path, backup_path)

        with open(backup_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"nerd_nostalgia_{datetime.now().strftime('%Y%m%d')}.db",
                caption=f"💾 Backup database - {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            )

        os.remove(backup_path)
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        await update.message.reply_text(f"Errore backup: {e}")
