import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import search_result_keyboard, product_actions_keyboard
from src.collectors.pricecharting import PriceChartingCollector
from src.db.database import async_session
from src.db.models import Product
from src.utils.buy_links import get_buy_links
from src.utils.currency import get_exchange_rates, format_price
from src.utils.llm_parser import is_configured as llm_configured, parse_with_llm_fallback
from src.utils.query_parser import parse_card_query

from sqlalchemy import select

logger = logging.getLogger(__name__)
collector = PriceChartingCollector()


def _set_banner(parsed) -> str:
    """One-line header announcing the matched expansion. Empty string when
    no expansion was detected."""
    if not parsed.expansion:
        return ""
    exp = parsed.expansion
    bits = [f"📦 *Set rilevato:* {exp.name_en}"]
    if exp.name_it and exp.name_it != exp.name_en:
        bits.append(f"_({exp.name_it})_")
    if exp.release_date:
        bits.append(f"· {exp.release_date}")
    if exp.total_cards:
        bits.append(f"· {exp.total_cards} carte")
    return " ".join(bits)


def _refined_query(parsed, fallback: str) -> str:
    """Rewrite the raw user query into something PriceCharting matches better.
    If the user typed an expansion in Italian (or via alias), we substitute the
    English name; otherwise we pass the original query through."""
    if not parsed.expansion:
        return fallback
    parts = []
    if parsed.name:
        parts.append(parsed.name)
    parts.append(parsed.expansion.name_en)
    return " ".join(parts)


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
    """Handle /search <query> command.

    Parses the query for set + condition + variant hints; if a known expansion
    is mentioned (in IT or EN), rewrites the search to use the canonical English
    set name so PriceCharting matches better. When the query is JUST a set name
    (e.g. "/search ex rubino zaffiro"), surfaces the set itself + top cards
    from it. Falls back to the LLM only when the rule-based parser is unsure
    and GEMINI_API_KEY is configured."""
    if not context.args:
        await update.message.reply_text(
            "Uso: /search <nome prodotto o set>\n"
            "Es: /search charizard base set\n"
            "Es: /search ex rubino zaffiro  (mostra il set + carte top)"
        )
        return

    raw_query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Cerco '{raw_query}'...")

    # Parse query — rule-based first, LLM fallback only when the rule-based
    # extracts almost nothing AND Gemini is configured.
    parsed = parse_card_query(raw_query)
    if parsed.confidence < 0.4 and llm_configured():
        try:
            parsed = await parse_with_llm_fallback(raw_query)
        except Exception as e:
            logger.warning(f"LLM fallback failed for {raw_query!r}: {e}")

    pc_query = _refined_query(parsed, raw_query)
    results = await collector.search(pc_query)

    if not results:
        # If the refined query failed but we DID detect an expansion, retry
        # with just the English set name — broader net.
        if parsed.expansion and pc_query != parsed.expansion.name_en:
            results = await collector.search(parsed.expansion.name_en)

    if not results:
        msg = "Nessun risultato trovato."
        if parsed.expansion:
            msg += f"\nSet rilevato: {parsed.expansion.name_en} (codice `{parsed.expansion.code}`)."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    products_data = []
    for r in results[:10]:
        product = await _save_and_get_product(r)
        products_data.append({
            "name": r.name,
            "product_id": product.id,
            "current_price": r.current_price,
        })

    # Build header. When the user typed only a set name, frame it as
    # "carte del set"; otherwise frame as a normal search result.
    header_lines = []
    banner = _set_banner(parsed)
    if banner:
        header_lines.append(banner)
    if parsed.is_pure_set_query:
        header_lines.append(f"\nTop {len(products_data)} carte del set:")
    else:
        header_lines.append(f"\nTrovati {len(results)} risultati per '{raw_query}'.\nSeleziona un prodotto:")

    await update.message.reply_text(
        "\n".join(header_lines),
        parse_mode="Markdown",
        reply_markup=search_result_keyboard(products_data),
    )


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

    rates = await get_exchange_rates()
    price_str = format_price(product.current_price, rates) if product.current_price else "N/D"
    set_str = f"\n📦 Set: {product.set_name}" if product.set_name else ""
    links = get_buy_links(product.name, product.category, product.product_url)

    text = (
        f"🎴 *{product.name}*{set_str}\n"
        f"💵 Prezzo: {price_str}\n"
        f"📂 Categoria: {product.category}\n\n"
        f"{links}"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=product_actions_keyboard(product.id),
        disable_web_page_preview=True,
    )
