import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.vinted import VintedCollector
from src.db.database import async_session


def _esc(text: str) -> str:
    """Escape Markdown special characters in user-generated text."""
    for ch in ['*', '_', '`', '[', ']', '(', ')']:
        text = text.replace(ch, '')
    return text
from src.db.models import Product

logger = logging.getLogger(__name__)
pc_collector = PriceChartingCollector()
vinted = VintedCollector()


async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /deals <nome> - Cerca affari su Vinted: inserzioni sotto il prezzo di mercato.
    """
    if not context.args:
        await update.message.reply_text(
            "Uso: /deals <nome prodotto>\n"
            "Es: /deals charizard base set\n\n"
            "Cerca su Vinted inserzioni sotto il prezzo di mercato."
        )
        return

    query = " ".join(context.args)
    page = 1
    await _deals_search_page(update.message, query, page, context)


async def _deals_search_page(message, query: str, page: int, context):
    """Fetch and display a page of Vinted deals."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import hashlib

    msg = await message.reply_text(f"🔍 Cerco affari per '{query}' (pagina {page})...")

    # Get market price
    results = await pc_collector.search(query, max_results=1)
    market_price = results[0].current_price if results and results[0].current_price else None
    product_name = results[0].name if results else query

    # Fetch by relevance, filter, sort by price
    listings = await vinted.search_listings(query, max_results=96, order="relevance")
    filtered = [l for l in listings
                if not vinted.is_suspicious(l)
                and vinted._title_matches(l.title, query)]
    filtered.sort(key=lambda l: l.price_eur)

    if not filtered:
        await msg.edit_text("Nessun risultato rilevante su Vinted.")
        return

    # Paginate
    page_size = 10
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    if not page_items:
        await msg.edit_text("Nessun altro risultato.")
        return

    # Header
    if market_price:
        market_eur = market_price * 0.92
        lines = [f"🔥 *Deals: '{_esc(product_name)}'* (pag. {page})\n"
                 f"💵 Mercato: ${market_price:.2f} (~€{market_eur:.2f})\n"]
    else:
        market_eur = None
        lines = [f"🛒 *Vinted: '{query}'* (pag. {page}, {len(filtered)} totali)\n"]

    for l in page_items:
        if market_eur and l.price_eur < market_eur:
            discount = ((market_eur - l.price_eur) / market_eur) * 100
            emoji = "🔥🔥" if discount > 50 else "🔥" if discount > 30 else "💰"
            lines.append(f"{emoji} *€{l.price_eur:.2f}* (-{discount:.0f}%) — [{_esc(l.title[:45])}]({l.url})")
        else:
            lines.append(f"€{l.price_eur:.2f} — [{_esc(l.title[:45])}]({l.url})")

    # Store query for pagination
    query_hash = hashlib.md5(f"deals_{query}".encode()).hexdigest()[:8]
    context.bot_data[f"dq_{query_hash}"] = query

    # Buttons
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅ Precedente", callback_data=f"dp:{query_hash}:{page - 1}"))
    if start + page_size < len(filtered):
        buttons.append(InlineKeyboardButton("Carica altri ➡", callback_data=f"dp:{query_hash}:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    await msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=markup,
    )


async def deals_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deals pagination."""
    query_cb = update.callback_query
    await query_cb.answer()

    parts = query_cb.data.split(":")
    query_hash = parts[1]
    page = int(parts[2])

    query = context.bot_data.get(f"dq_{query_hash}", "")
    if not query:
        await query_cb.edit_message_text("Sessione scaduta. Rifai /deals <nome>.")
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import hashlib

    await query_cb.edit_message_text(f"🔍 Carico pagina {page}...")

    results = await pc_collector.search(query, max_results=1)
    market_price = results[0].current_price if results and results[0].current_price else None
    market_eur = market_price * 0.92 if market_price else None
    product_name = results[0].name if results else query

    listings = await vinted.search_listings(query, max_results=96, order="relevance")
    filtered = [l for l in listings
                if not vinted.is_suspicious(l)
                and vinted._title_matches(l.title, query)]
    filtered.sort(key=lambda l: l.price_eur)

    page_size = 10
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    if not page_items:
        await query_cb.edit_message_text("Nessun altro risultato.")
        return

    if market_eur:
        lines = [f"🔥 *Deals: '{_esc(product_name)}'* (pag. {page})\n"
                 f"💵 Mercato: ${market_price:.2f} (~€{market_eur:.2f})\n"]
    else:
        lines = [f"🛒 *Vinted: '{query}'* (pag. {page}, {len(filtered)} totali)\n"]

    for l in page_items:
        if market_eur and l.price_eur < market_eur:
            discount = ((market_eur - l.price_eur) / market_eur) * 100
            emoji = "🔥🔥" if discount > 50 else "🔥" if discount > 30 else "💰"
            lines.append(f"{emoji} *€{l.price_eur:.2f}* (-{discount:.0f}%) — [{_esc(l.title[:45])}]({l.url})")
        else:
            lines.append(f"€{l.price_eur:.2f} — [{_esc(l.title[:45])}]({l.url})")

    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅ Precedente", callback_data=f"dp:{query_hash}:{page - 1}"))
    if start + page_size < len(filtered):
        buttons.append(InlineKeyboardButton("Carica altri ➡", callback_data=f"dp:{query_hash}:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    await query_cb.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=markup,
    )


async def vinted_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /vinted <nome> - Cerca inserzioni su Vinted ordinate per prezzo.
    """
    if not context.args:
        await update.message.reply_text("Uso: /vinted <nome prodotto>")
        return

    query = " ".join(context.args)
    page = 1
    await _vinted_search_page(update.message, query, page, context)


async def _vinted_search_page(message, query: str, page: int, context):
    """Fetch and display a page of Vinted results."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    msg = await message.reply_text(f"🔍 Cerco su Vinted '{query}' (pagina {page})...")

    # Fetch more results per page to have enough after filtering
    per_page = 96
    listings = await vinted.search_listings(query, max_results=per_page, order="relevance")

    # Filter
    filtered = [l for l in listings
                if not vinted.is_suspicious(l)
                and vinted._title_matches(l.title, query)]
    filtered.sort(key=lambda l: l.price_eur)

    # Paginate: show 10 per page
    page_size = 10
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    if not page_items:
        if page == 1:
            await msg.edit_text("Nessun risultato rilevante su Vinted.")
        else:
            await msg.edit_text("Nessun altro risultato.")
        return

    lines = [f"🛒 *Vinted: '{query}'* (pag. {page}, {len(filtered)} totali)\n"]
    for l in page_items:
        lines.append(f"€{l.price_eur:.2f} — [{_esc(l.title[:50])}]({l.url})")

    # Store query in context for pagination
    # Use a short hash to keep callback_data under 64 bytes
    import hashlib
    query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
    context.bot_data[f"vq_{query_hash}"] = query

    # Buttons
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅ Precedente", callback_data=f"vp:{query_hash}:{page - 1}"))
    if start + page_size < len(filtered):
        buttons.append(InlineKeyboardButton("Carica altri ➡", callback_data=f"vp:{query_hash}:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    await msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=markup,
    )


async def vinted_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Vinted pagination button clicks."""
    query_cb = update.callback_query
    await query_cb.answer()

    parts = query_cb.data.split(":")
    query_hash = parts[1]
    page = int(parts[2])

    query = context.bot_data.get(f"vq_{query_hash}", "")
    if not query:
        await query_cb.edit_message_text("Sessione scaduta. Rifai /vinted <nome>.")
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    await query_cb.edit_message_text(f"🔍 Carico pagina {page}...")

    per_page = 96
    listings = await vinted.search_listings(query, max_results=per_page, order="relevance")
    filtered = [l for l in listings
                if not vinted.is_suspicious(l)
                and vinted._title_matches(l.title, query)]
    filtered.sort(key=lambda l: l.price_eur)

    page_size = 10
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    if not page_items:
        await query_cb.edit_message_text("Nessun altro risultato.")
        return

    lines = [f"🛒 *Vinted: '{query}'* (pag. {page}, {len(filtered)} totali)\n"]
    for l in page_items:
        lines.append(f"€{l.price_eur:.2f} — [{_esc(l.title[:50])}]({l.url})")

    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅ Precedente", callback_data=f"vp:{query_hash}:{page - 1}"))
    if start + page_size < len(filtered):
        buttons.append(InlineKeyboardButton("Carica altri ➡", callback_data=f"vp:{query_hash}:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    await query_cb.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=markup,
    )
