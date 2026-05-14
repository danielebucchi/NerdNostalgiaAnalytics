import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.collectors.pricecharting import PriceChartingCollector
from src.db.database import async_session
from src.db.models import Product, WatchlistEntry, Alert, SignalType

logger = logging.getLogger(__name__)
collector = PriceChartingCollector()


async def _save_products_batch(results):
    """Save search results to DB in batches. Returns list of (external_id, product_id, name, price)."""
    saved = []
    # Process in batches of 50
    for i in range(0, len(results), 50):
        batch = results[i:i + 50]
        async with async_session() as session:
            for r in batch:
                existing = await session.execute(
                    select(Product).where(
                        Product.external_id == r.external_id,
                        Product.source == r.source,
                    )
                )
                product = existing.scalar_one_or_none()
                if not product:
                    product = Product(
                        external_id=r.external_id, source=r.source, name=r.name,
                        category=r.category, set_name=r.set_name,
                        console_or_platform=r.console_or_platform,
                        image_url=r.image_url, product_url=r.product_url,
                        current_price=r.current_price,
                    )
                    session.add(product)
                    await session.flush()
                saved.append((r.external_id, product.id, r.name, r.current_price))
            await session.commit()
    return saved


async def _bulk_add_watchlist(user_id: int, product_ids: list[int]) -> tuple[int, int]:
    """Add products to watchlist in batch. Returns (added, already_existed)."""
    added = 0
    already = 0
    for i in range(0, len(product_ids), 50):
        batch = product_ids[i:i + 50]
        async with async_session() as session:
            for pid in batch:
                existing = await session.execute(
                    select(WatchlistEntry).where(
                        WatchlistEntry.telegram_user_id == user_id,
                        WatchlistEntry.product_id == pid,
                    )
                )
                if existing.scalar_one_or_none():
                    already += 1
                else:
                    session.add(WatchlistEntry(telegram_user_id=user_id, product_id=pid))
                    added += 1
            await session.commit()
    return added, already


async def watchall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /watchall <nome> - Cerca TUTTE le varianti di un prodotto e le aggiunge alla watchlist.
    """
    if not context.args:
        await update.message.reply_text(
            "Uso: /watchall <nome>\nEs: /watchall charizard"
        )
        return

    query = " ".join(context.args)
    user_id = update.message.from_user.id

    msg = await update.message.reply_text(f"🔍 Cerco tutte le varianti di '{query}'...")

    results = await collector.search_all(query)
    if not results:
        await msg.edit_text("Nessun risultato trovato.")
        return

    await msg.edit_text(f"📦 Trovate {len(results)} varianti. Salvataggio...")

    saved = await _save_products_batch(results)
    product_ids = [pid for _, pid, _, _ in saved]
    added, already = await _bulk_add_watchlist(user_id, product_ids)

    prices = [p for _, _, _, p in saved if p is not None and p > 0]
    price_info = ""
    if prices:
        price_info = (
            f"\n\n💵 Range: ${min(prices):.2f} - ${max(prices):.2f}"
            f"\n📊 Media: ${sum(prices) / len(prices):.2f}"
        )

    examples = "\n".join(
        f"  • {name}" + (f" - ${price:.2f}" if price else "")
        for _, _, name, price in saved[:5]
    )
    if len(saved) > 5:
        examples += f"\n  ... e altri {len(saved) - 5}"

    await msg.edit_text(
        f"✅ *Watchall '{query}'*\n\n"
        f"📦 Trovate: {len(results)}\n"
        f"👁 Aggiunte: {added}\n"
        f"⏭ Gia' presenti: {already}"
        f"{price_info}\n\n"
        f"*Esempi:*\n{examples}",
        parse_mode="Markdown",
    )


async def alertall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /alertall <nome> [buy|sell] - Alert su tutte le varianti.
    """
    if not context.args:
        await update.message.reply_text(
            "Uso: /alertall <nome> [buy|sell]\nEs: /alertall charizard\nEs: /alertall charizard sell"
        )
        return

    args = list(context.args)
    signal_type = SignalType.BUY
    signal_map = {
        "buy": SignalType.BUY, "sell": SignalType.SELL,
        "strong_buy": SignalType.STRONG_BUY, "strong_sell": SignalType.STRONG_SELL,
    }
    if args[-1].lower() in signal_map:
        signal_type = signal_map[args.pop().lower()]

    query = " ".join(args)
    user_id = update.message.from_user.id

    msg = await update.message.reply_text(
        f"🔍 Cerco varianti di '{query}' per alert {signal_type.value}..."
    )

    results = await collector.search_all(query)
    if not results:
        await msg.edit_text("Nessun risultato trovato.")
        return

    await msg.edit_text(f"📦 {len(results)} varianti. Impostazione alert...")

    saved = await _save_products_batch(results)

    # Add to watchlist + set alerts in batch
    created = 0
    already = 0
    for i in range(0, len(saved), 50):
        batch = saved[i:i + 50]
        async with async_session() as session:
            for _, product_id, _, _ in batch:
                # Watchlist
                existing_w = await session.execute(
                    select(WatchlistEntry).where(
                        WatchlistEntry.telegram_user_id == user_id,
                        WatchlistEntry.product_id == product_id,
                    )
                )
                if not existing_w.scalar_one_or_none():
                    session.add(WatchlistEntry(telegram_user_id=user_id, product_id=product_id))

                # Alert
                existing_a = await session.execute(
                    select(Alert).where(
                        Alert.telegram_user_id == user_id,
                        Alert.product_id == product_id,
                        Alert.signal_type == signal_type,
                        Alert.is_active == True,
                    )
                )
                if existing_a.scalar_one_or_none():
                    already += 1
                else:
                    session.add(Alert(
                        telegram_user_id=user_id,
                        product_id=product_id,
                        signal_type=signal_type,
                    ))
                    created += 1
            await session.commit()

    await msg.edit_text(
        f"🔔 *Alert {signal_type.value} per '{query}'*\n\n"
        f"📦 Varianti: {len(results)}\n"
        f"🔔 Alert creati: {created}\n"
        f"⏭ Gia' presenti: {already}",
        parse_mode="Markdown",
    )


async def unwatchall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /unwatchall <nome> - Rimuove dalla watchlist tutte le carte che matchano.
    """
    if not context.args:
        await update.message.reply_text("Uso: /unwatchall <nome>")
        return

    query = " ".join(context.args).lower()
    user_id = update.message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(WatchlistEntry, Product)
            .join(Product, WatchlistEntry.product_id == Product.id)
            .where(WatchlistEntry.telegram_user_id == user_id)
        )
        entries = result.all()

        removed = 0
        for entry, product in entries:
            if query in product.name.lower():
                await session.delete(entry)
                # Deactivate alerts too
                alerts_result = await session.execute(
                    select(Alert).where(
                        Alert.telegram_user_id == user_id,
                        Alert.product_id == product.id,
                        Alert.is_active == True,
                    )
                )
                for alert in alerts_result.scalars().all():
                    alert.is_active = False
                removed += 1
        await session.commit()

    if removed:
        await update.message.reply_text(f"✅ Rimossi {removed} prodotti con '{query}' + relativi alert.")
    else:
        await update.message.reply_text(f"Nessun prodotto con '{query}' nella watchlist.")
