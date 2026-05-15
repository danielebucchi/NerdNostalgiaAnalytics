import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.vinted import VintedCollector
from src.db.database import async_session
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
    msg = await update.message.reply_text(f"🔍 Cerco affari su Vinted per '{query}'...")

    # Get market price from PriceCharting
    results = await pc_collector.search(query, max_results=1)
    if not results or not results[0].current_price:
        await msg.edit_text(
            f"Prezzo di mercato non trovato per '{query}'.\n"
            f"Provo comunque a cercare su Vinted..."
        )
        # Just show Vinted listings without deal comparison
        listings = await vinted.search_listings(query, max_results=20, order="price_low_to_high")
        filtered = [l for l in listings
                    if not vinted.is_suspicious(l)
                    and vinted._title_matches(l.title, query)]
        if not filtered:
            await msg.edit_text("Nessun risultato rilevante su Vinted.")
            return

        lines = [f"🛒 *Inserzioni Vinted per '{query}'*\n"]
        for l in filtered[:10]:
            lines.append(
                f"€{l.price_eur:.2f} — [{l.title[:50]}]({l.url})\n"
                f"   Venditore: {l.seller}"
            )
        await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)
        return

    product = results[0]
    market_price = product.current_price

    await msg.edit_text(
        f"💵 Prezzo mercato: ${market_price:.2f} (~€{market_price * 0.92:.2f})\n"
        f"🔍 Cerco affari su Vinted..."
    )

    deals = await vinted.find_deals(query, market_price, max_results=10)

    if not deals:
        # Show cheapest listings anyway
        listings = await vinted.search_listings(query, max_results=5, order="price_low_to_high")
        if listings:
            lines = [
                f"🛒 *{product.name}*\n"
                f"💵 Prezzo mercato: ${market_price:.2f} (~€{market_price * 0.92:.2f})\n\n"
                f"Nessun affare trovato sotto il prezzo di mercato.\n"
                f"Le inserzioni piu' economiche:\n"
            ]
            for l in listings:
                lines.append(f"€{l.price_eur:.2f} — [{l.title[:50]}]({l.url})")
            await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await msg.edit_text("Nessun risultato su Vinted per questa ricerca.")
        return

    lines = [
        f"🔥 *AFFARI trovati per '{product.name}'*\n"
        f"💵 Prezzo mercato: ${market_price:.2f} (~€{market_price * 0.92:.2f})\n"
    ]

    for listing, discount in deals:
        emoji = "🔥🔥" if discount > 50 else "🔥" if discount > 30 else "💰"
        lines.append(
            f"{emoji} *€{listing.price_eur:.2f}* (-{discount:.0f}%) — [{listing.title[:45]}]({listing.url})\n"
            f"   Venditore: {listing.seller}"
        )

    lines.append(f"\n⚠ Controlla sempre condizioni e foto prima di acquistare!")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def vinted_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /vinted <nome> - Cerca inserzioni su Vinted ordinate per prezzo.
    """
    if not context.args:
        await update.message.reply_text("Uso: /vinted <nome prodotto>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Cerco su Vinted '{query}'...")

    # Use relevance order to avoid wall of €1 accessories, then sort by price
    listings = await vinted.search_listings(query, max_results=50, order="relevance")

    # Filter: remove suspicious/catalog and irrelevant
    filtered = [l for l in listings
                if not vinted.is_suspicious(l)
                and vinted._title_matches(l.title, query)]

    # Sort filtered results by price
    filtered.sort(key=lambda l: l.price_eur)

    if not filtered:
        await msg.edit_text("Nessun risultato rilevante su Vinted.")
        return

    lines = [f"🛒 *Vinted: '{query}'* ({len(filtered)} risultati)\n"]
    for l in filtered[:15]:
        lines.append(f"€{l.price_eur:.2f} — [{l.title[:50]}]({l.url})")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)
