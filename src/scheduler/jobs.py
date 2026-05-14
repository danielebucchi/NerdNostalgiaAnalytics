import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from src.analysis.indicators import analyze, Signal, SIGNAL_EMOJI
from src.bot.handlers.signal import get_or_fetch_prices
from src.collectors.vinted import VintedCollector
from src.config import settings
from src.db.database import async_session
from src.db.models import Product, Alert, PriceAlert, VintedWatch, WatchlistEntry, SignalType
from src.utils.buy_links import get_buy_links

vinted_collector = VintedCollector()

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def update_watchlist_prices(app):
    """Periodically update prices for all watched products."""
    logger.info("Starting scheduled price update...")

    async with async_session() as session:
        result = await session.execute(
            select(Product.id, Product.external_id)
            .join(WatchlistEntry, WatchlistEntry.product_id == Product.id)
            .distinct()
        )
        products = result.all()

    logger.info(f"Updating prices for {len(products)} products")

    for product_id, external_id in products:
        try:
            df = await get_or_fetch_prices(product_id)
            if df is not None and len(df) > 0:
                latest_price = float(df["price"].iloc[-1])
                async with async_session() as session:
                    result = await session.execute(
                        select(Product).where(Product.id == product_id)
                    )
                    product = result.scalar_one_or_none()
                    if product:
                        product.current_price = latest_price
                        product.last_updated = datetime.utcnow()
                        await session.commit()
            await asyncio.sleep(settings.scrape_delay_seconds)
        except Exception as e:
            logger.error(f"Failed to update {external_id}: {e}")
            continue

    logger.info("Price update completed")


async def check_signal_alerts(app):
    """Check signal-based alerts (BUY/SELL)."""
    logger.info("Checking signal alerts...")

    async with async_session() as session:
        result = await session.execute(
            select(Alert, Product)
            .join(Product, Alert.product_id == Product.id)
            .where(Alert.is_active == True)
        )
        alerts = result.all()

    if not alerts:
        return

    logger.info(f"Checking {len(alerts)} signal alerts")

    for alert, product in alerts:
        try:
            df = await get_or_fetch_prices(product.id)
            if df is None or len(df) < 6:
                continue

            analysis = analyze(df)
            if not analysis:
                continue

            should_notify = False
            if alert.signal_type == SignalType.BUY:
                should_notify = analysis.signal in (Signal.BUY, Signal.STRONG_BUY)
            elif alert.signal_type == SignalType.SELL:
                should_notify = analysis.signal in (Signal.SELL, Signal.STRONG_SELL)
            elif alert.signal_type == SignalType.STRONG_BUY:
                should_notify = analysis.signal == Signal.STRONG_BUY
            elif alert.signal_type == SignalType.STRONG_SELL:
                should_notify = analysis.signal == Signal.STRONG_SELL

            if should_notify:
                emoji = SIGNAL_EMOJI.get(analysis.signal, "")
                rsi_str = f"\nRSI: {analysis.rsi:.1f}" if analysis.rsi else ""
                spike_str = "\n⚠ ATTENZIONE: spike anomalo" if analysis.is_spike else ""
                links = get_buy_links(product.name, product.category, product.product_url)
                text = (
                    f"🔔 *ALERT SEGNALE!*\n\n"
                    f"🎴 *{product.name}*\n"
                    f"{emoji} Segnale: *{analysis.signal.value}*\n"
                    f"💵 Prezzo: ${analysis.current_price:.2f}\n"
                    f"📊 Score: {analysis.score:+.0f}{rsi_str}{spike_str}\n\n"
                    f"Compra qui: {links}"
                )

                try:
                    await app.bot.send_message(
                        chat_id=alert.telegram_user_id, text=text, parse_mode="Markdown",
                    )
                    async with async_session() as session:
                        db_alert = (await session.execute(
                            select(Alert).where(Alert.id == alert.id)
                        )).scalar_one_or_none()
                        if db_alert:
                            db_alert.last_triggered = datetime.utcnow()
                            await session.commit()
                    logger.info(f"Signal alert sent for {product.name}")
                except Exception as e:
                    logger.error(f"Failed to send signal alert: {e}")

        except Exception as e:
            logger.error(f"Error checking signal alert for {product.name}: {e}")
            continue


async def check_price_alerts(app):
    """Check price-threshold alerts."""
    logger.info("Checking price alerts...")

    async with async_session() as session:
        result = await session.execute(
            select(PriceAlert, Product)
            .join(Product, PriceAlert.product_id == Product.id)
            .where(PriceAlert.is_active == True)
        )
        alerts = result.all()

    if not alerts:
        return

    logger.info(f"Checking {len(alerts)} price alerts")

    for alert, product in alerts:
        if product.current_price is None:
            continue

        triggered = False
        if alert.direction == "below" and product.current_price <= alert.target_price:
            triggered = True
        elif alert.direction == "above" and product.current_price >= alert.target_price:
            triggered = True

        if triggered:
            direction_str = "sceso sotto" if alert.direction == "below" else "salito sopra"
            links = get_buy_links(product.name, product.category, product.product_url)
            text = (
                f"🎯 *PRICE ALERT!*\n\n"
                f"🎴 *{product.name}*\n"
                f"💵 Prezzo attuale: *${product.current_price:.2f}*\n"
                f"📍 Target: ${alert.target_price:.2f}\n"
                f"Il prezzo e' {direction_str} la tua soglia!\n\n"
                f"Compra qui: {links}"
            )

            try:
                await app.bot.send_message(
                    chat_id=alert.telegram_user_id, text=text, parse_mode="Markdown",
                )
                async with async_session() as session:
                    db_alert = (await session.execute(
                        select(PriceAlert).where(PriceAlert.id == alert.id)
                    )).scalar_one_or_none()
                    if db_alert:
                        db_alert.last_triggered = datetime.utcnow()
                        db_alert.is_active = False  # One-shot: deactivate after trigger
                        await session.commit()
                logger.info(f"Price alert sent for {product.name}")
            except Exception as e:
                logger.error(f"Failed to send price alert: {e}")


def setup_scheduler(app):
    """Set up periodic jobs."""
    scheduler.add_job(
        update_watchlist_prices, "interval",
        hours=settings.price_update_interval_hours,
        args=[app], id="update_prices", name="Update watchlist prices",
        replace_existing=True,
    )

    scheduler.add_job(
        check_signal_alerts, "interval",
        minutes=settings.alert_check_interval_minutes,
        args=[app], id="check_signal_alerts", name="Check signal alerts",
        replace_existing=True,
    )

    scheduler.add_job(
        check_price_alerts, "interval",
        minutes=settings.alert_check_interval_minutes,
        args=[app], id="check_price_alerts", name="Check price alerts",
        replace_existing=True,
    )

    scheduler.add_job(
        check_vinted_watches, "interval",
        minutes=10,  # Check every 10 minutes for fast notifications
        args=[app], id="check_vinted", name="Check Vinted watches",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started: prices every {settings.price_update_interval_hours}h, "
        f"alerts every {settings.alert_check_interval_minutes}min, vinted every 10min"
    )


async def check_vinted_watches(app):
    """Check Vinted for new listings matching watches."""
    logger.info("Checking Vinted watches...")

    async with async_session() as session:
        result = await session.execute(
            select(VintedWatch).where(VintedWatch.is_active == True)
        )
        watches = result.scalars().all()

    if not watches:
        return

    logger.info(f"Checking {len(watches)} Vinted watches")

    for watch in watches:
        try:
            countries = [c.strip() for c in (watch.countries or "it").split(",")]
            min_price = watch.min_price_eur or 0.50

            if len(countries) > 1:
                listings = await vinted_collector.search_multi_country(
                    watch.search_query, countries, max_per_country=15, order="newest_first",
                )
            else:
                listings = await vinted_collector.search_listings(
                    watch.search_query, max_results=20, order="newest_first",
                    country=countries[0],
                )

            seen = set(watch.seen_urls.split(",")) if watch.seen_urls else set()
            new_listings = []

            for listing in listings:
                if listing.url in seen:
                    continue
                if listing.price_eur < min_price:
                    continue  # Anti-fake
                if listing.price_eur > watch.max_price_eur:
                    continue
                if vinted_collector.is_suspicious(listing, min_price):
                    continue
                if not vinted_collector._title_matches(listing.title, watch.search_query):
                    continue
                new_listings.append(listing)
                seen.add(listing.url)

            if new_listings:
                country_flags = {"it": "🇮🇹", "fr": "🇫🇷", "de": "🇩🇪", "es": "🇪🇸", "nl": "🇳🇱"}
                lines = [f"👗 *NUOVA INSERZIONE VINTED!*\n"]
                for l in new_listings[:5]:
                    flag = country_flags.get(l.country, "")
                    lines.append(
                        f"{flag} *€{l.price_eur:.2f}* — [{l.title[:45]}]({l.url})\n"
                        f"   Venditore: {l.seller}"
                    )
                if len(new_listings) > 5:
                    lines.append(f"\n... e altre {len(new_listings) - 5} inserzioni")
                lines.append(f"\n🔍 Ricerca: '{watch.search_query}' < €{watch.max_price_eur:.2f}")

                try:
                    await app.bot.send_message(
                        chat_id=watch.telegram_user_id,
                        text="\n".join(lines),
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                    )
                except Exception as e:
                    logger.error(f"Failed to send Vinted notification: {e}")

            # Update seen URLs
            async with async_session() as session:
                db_watch = (await session.execute(
                    select(VintedWatch).where(VintedWatch.id == watch.id)
                )).scalar_one_or_none()
                if db_watch:
                    db_watch.seen_urls = ",".join(list(seen)[-500:])  # Keep last 500
                    db_watch.last_checked = datetime.utcnow()
                    await session.commit()

            await asyncio.sleep(3)  # Rate limit

        except Exception as e:
            logger.error(f"Vinted watch check failed for '{watch.search_query}': {e}")
            continue

    logger.info("Vinted watch check completed")
