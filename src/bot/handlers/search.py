import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import search_result_keyboard, product_actions_keyboard
from src.collectors.pricecharting import PriceChartingCollector
from src.db.database import async_session
from src.db.models import Product

from sqlalchemy import select

logger = logging.getLogger(__name__)
collector = PriceChartingCollector()


async def _save_and_get_product(r) -> Product:
    """Save a search result to DB and return the Product with its DB id."""
    async with async_session() as session:
        existing = await session.execute(
            select(Product).where(Product.external_id == r.external_id, Product.source == r.source)
        )
        product = existing.scalar_one_or_none()
        if not product:
            product = Product(
                external_id=r.external_id,
                source=r.source,
                name=r.name,
                category=r.category,
                set_name=r.set_name,
                console_or_platform=r.console_or_platform,
                image_url=r.image_url,
                product_url=r.product_url,
                current_price=r.current_price,
            )
            session.add(product)
            await session.commit()
            await session.refresh(product)
        return product


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search <query> command."""
    if not context.args:
        await update.message.reply_text("Uso: /search <nome prodotto>\nEs: /search charizard base set")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Cerco '{query}'...")

    results = await collector.search(query)

    if not results:
        await update.message.reply_text("Nessun risultato trovato. Prova con un termine diverso.")
        return

    # Save products to DB and collect their IDs
    products_data = []
    for r in results[:10]:
        product = await _save_and_get_product(r)
        products_data.append({
            "name": r.name,
            "product_id": product.id,
            "current_price": r.current_price,
        })

    text = f"Trovati {len(results)} risultati per '{query}'.\nSeleziona un prodotto:"
    await update.message.reply_text(text, reply_markup=search_result_keyboard(products_data))


async def select_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product selection from search results."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split(":")[1])

    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()

    if not product:
        await query.edit_message_text("Prodotto non trovato nel database.")
        return

    price_str = f"${product.current_price:.2f}" if product.current_price else "N/D"
    set_str = f"\n📦 Set: {product.set_name}" if product.set_name else ""
    url_str = f"\n🔗 {product.product_url}" if product.product_url else ""

    text = (
        f"🎴 *{product.name}*{set_str}\n"
        f"💵 Prezzo: {price_str}\n"
        f"📂 Categoria: {product.category}{url_str}"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=product_actions_keyboard(product.id),
    )
