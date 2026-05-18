"""
Nerd Nostalgia Lite — Minimal price alert bot.
Only watchlist + price drop notifications.

Commands:
  /search <name>         — Find a product
  /watch <name> <price>  — Watch a product, alert when below €price
  /watchlist             — Show your watchlist
  /unwatch <name>        — Remove from watchlist
  /help                  — Show commands
"""
import asyncio
import logging
import os
import re
import sys

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src import scraper, db

load_dotenv()

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
scheduler = AsyncIOScheduler()


# --- Commands ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Ciao {update.message.from_user.first_name}! 👋\n\n"
        "🎴 *Nerd Nostalgia Lite*\n"
        "_Monitora prezzi di carte e videogiochi._\n"
        "_Ti avviso quando scendono sotto la soglia che imposti._\n\n"
        "Scrivi /help per la guida completa.",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎴 *Nerd Nostalgia Lite — Guida*\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 *CERCA*\n\n"
        "/search <nome>\n"
        "  Cerca un prodotto su PriceCharting.\n"
        "  _Es: /search charizard base set_\n"
        "  _Es: /search pokemon emerald_\n"
        "  _Es: /search nintendo switch oled_\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👁 *MONITORA*\n\n"
        "/watch <nome> <prezzo soglia $>\n"
        "  Aggiunge alla watchlist. Ti avviso quando\n"
        "  il prezzo scende sotto la soglia.\n"
        "  _Es: /watch charizard base set 300_\n"
        "  _Es: /watch pokemon emerald 40_\n\n"
        "  Se il prodotto e' gia' in watchlist,\n"
        "  aggiorna la soglia.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 *GESTISCI*\n\n"
        "/watchlist\n"
        "  Mostra la tua lista con:\n"
        "  🟢 = sotto soglia (compralo!)\n"
        "  🟡 = vicino alla soglia (<10%)\n"
        "  ⚪ = sopra soglia (aspetta)\n\n"
        "/unwatch <nome>\n"
        "  Rimuove dalla watchlist.\n"
        "  _Es: /unwatch charizard_\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔔 *NOTIFICHE*\n\n"
        f"  Controllo prezzi ogni *{CHECK_INTERVAL} minuti*.\n"
        "  Quando un prodotto scende sotto soglia\n"
        "  ricevi un messaggio automatico.\n"
        "  Se risale e poi riscende, ti avviso di nuovo.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Nota: i prezzi sono in $ (dollari USA)*\n"
        "  PriceCharting usa il mercato americano.\n"
        "  I prezzi EU possono variare.",
        parse_mode="Markdown",
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /search <nome>\nEs: /search charizard base set")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Cerco '{query}'...")

    results = await scraper.search(query, max_results=8)
    if not results:
        await update.message.reply_text("Nessun risultato.")
        return

    buttons = []
    for r in results:
        price_str = f" - ${r['price']:.2f}" if r.get("price") else ""
        label = f"{r['name'][:40]}{price_str}"
        # Store external_id in callback (max 64 bytes)
        cb_id = r["external_id"][:55]
        buttons.append([InlineKeyboardButton(label, callback_data=f"s:{cb_id}")])

    await update.message.reply_text(
        f"Trovati {len(results)} risultati. Seleziona:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    external_id = query.data.replace("s:", "")

    # Store for /watch
    context.user_data["last_product"] = external_id

    # Get price
    price = await scraper.get_current_price(external_id)
    price_str = f"${price:.2f}" if price else "N/D"
    name = external_id.split("/")[-1].replace("-", " ").title()

    await query.edit_message_text(
        f"🎴 *{name}*\n"
        f"💵 Prezzo attuale: {price_str}\n\n"
        f"Per monitorare, scrivi:\n"
        f"`/watch {name.lower()} <prezzo soglia in $>`\n\n"
        f"Es: `/watch {name.lower()} {int(price * 0.8) if price else 50}`",
        parse_mode="Markdown",
    )


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Uso: /watch <nome prodotto> <prezzo soglia $>\n"
            "Es: /watch charizard base set 300\n"
            "Es: /watch pokemon emerald 40\n\n"
            "Ti avviso quando il prezzo scende sotto la soglia."
        )
        return

    # Last arg is price
    try:
        target = float(context.args[-1].replace(",", ".").replace("€", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("L'ultimo argomento deve essere il prezzo soglia.")
        return

    query = " ".join(context.args[:-1])
    user_id = update.message.from_user.id

    await update.message.reply_text(f"🔍 Cerco '{query}'...")

    results = await scraper.search(query, max_results=1)
    if not results:
        await update.message.reply_text("Prodotto non trovato.")
        return

    product = results[0]
    current = await scraper.get_current_price(product["external_id"])

    status = db.add_item(
        user_id=user_id,
        name=product["name"],
        external_id=product["external_id"],
        url=product["url"],
        target_price=target,
        current_price=current,
    )

    current_str = f"${current:.2f}" if current else "N/D"
    action = "aggiornato" if status == "updated" else "aggiunto"

    below = ""
    if current and current <= target:
        below = "\n\n⚠ *Il prezzo e' GIA' sotto la soglia!*"

    await update.message.reply_text(
        f"✅ *{product['name']}* {action} alla watchlist!\n\n"
        f"💵 Prezzo attuale: {current_str}\n"
        f"🎯 Soglia alert: ${target:.2f}\n"
        f"🔔 Ti avviso quando scende sotto ${target:.2f}{below}",
        parse_mode="Markdown",
    )


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    items = db.get_watchlist(user_id)

    if not items:
        await update.message.reply_text(
            "Watchlist vuota.\nUsa /watch <nome> <prezzo> per aggiungere."
        )
        return

    lines = [f"👁 *La tua watchlist ({len(items)})*\n"]
    for item in items:
        current = item.get("current_price")
        target = item["target_price"]
        current_str = f"${current:.2f}" if current else "N/D"

        if current and current <= target:
            emoji = "🟢"
            status = "SOTTO SOGLIA!"
        elif current and current <= target * 1.1:
            emoji = "🟡"
            status = "vicino"
        else:
            emoji = "⚪"
            status = ""

        lines.append(
            f"{emoji} *{item['name']}*\n"
            f"   Attuale: {current_str} | Soglia: ${target:.2f} {status}"
        )

    lines.append(f"\n/unwatch <nome> per rimuovere")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /unwatch <nome prodotto>")
        return

    query = " ".join(context.args)
    user_id = update.message.from_user.id
    removed = db.remove_item(user_id, query)

    if removed:
        await update.message.reply_text(f"✅ Rimossi {removed} prodotti dalla watchlist.")
    else:
        await update.message.reply_text(f"Nessun prodotto con '{query}' nella watchlist.")


# --- Price checker ---

async def check_prices(app):
    """Periodic job: check prices and send alerts."""
    items = db.get_all_items()
    if not items:
        return

    # Deduplicate by external_id
    seen = set()
    unique_ids = []
    for item in items:
        if item["external_id"] not in seen:
            seen.add(item["external_id"])
            unique_ids.append(item["external_id"])

    logger.info(f"Checking prices for {len(unique_ids)} products...")

    for ext_id in unique_ids:
        try:
            price = await scraper.get_current_price(ext_id)
            if price:
                db.update_price(ext_id, price)

                # Check alerts for all users watching this product
                for item in items:
                    if item["external_id"] == ext_id and price <= item["target_price"]:
                        if not item.get("notified"):
                            try:
                                await app.bot.send_message(
                                    chat_id=item["user_id"],
                                    text=(
                                        f"🔔 *PREZZO SCESO!*\n\n"
                                        f"🎴 *{item['name']}*\n"
                                        f"💵 Prezzo: *${price:.2f}*\n"
                                        f"🎯 Soglia: ${item['target_price']:.2f}\n\n"
                                        f"🔗 {item['url']}"
                                    ),
                                    parse_mode="Markdown",
                                )
                                db.mark_notified(item["user_id"], ext_id, True)
                                logger.info(f"Alert sent: {item['name']} at ${price:.2f}")
                            except Exception as e:
                                logger.error(f"Failed to send alert: {e}")

                    # Reset notification if price goes back above threshold
                    elif item["external_id"] == ext_id and price > item["target_price"]:
                        if item.get("notified"):
                            db.mark_notified(item["user_id"], ext_id, False)

            await asyncio.sleep(2)  # Rate limit
        except Exception as e:
            logger.error(f"Price check failed for {ext_id}: {e}")

    logger.info("Price check completed")


# --- Main ---

def main():
    if not TOKEN:
        print("Set TELEGRAM_BOT_TOKEN in .env")
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("unwatch", unwatch_cmd))
    app.add_handler(CallbackQueryHandler(select_callback, pattern=r"^s:"))

    # Schedule price checks
    scheduler.add_job(
        check_prices, "interval", minutes=CHECK_INTERVAL,
        args=[app], id="price_check", replace_existing=True,
    )
    scheduler.start()

    print(f"🎴 Nerd Nostalgia Lite — checking every {CHECK_INTERVAL}min")
    print("Press Ctrl+C to stop")
    app.run_polling()


if __name__ == "__main__":
    main()
