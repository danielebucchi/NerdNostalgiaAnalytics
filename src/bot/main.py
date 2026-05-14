import html
import logging
import traceback

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters, ContextTypes,
)

from src.config import settings
from src.db.database import init_db
from src.bot.handlers.search import search_command, select_product_callback
from src.bot.handlers.signal import signal_command, signal_callback
from src.bot.handlers.chart import chart_command, chart_callback
from src.bot.handlers.watchlist import watchlist_command, watch_callback, unwatch_callback
from src.bot.handlers.alert import alert_command, alert_buy_callback
from src.bot.handlers.portfolio import (
    portfolio_command, portfolio_add_callback, portfolio_buy_price,
    portfolio_quantity, portfolio_cancel, sell_command, export_command,
    portfolio_chart_command, WAITING_BUY_PRICE, WAITING_QUANTITY,
)
from src.bot.handlers.bulk import watchall_command, alertall_command, unwatchall_command
from src.bot.handlers.pricealert import pricealert_command
from src.bot.handlers.market import trending_command, opportunities_command
from src.bot.handlers.deals import deals_command, vinted_command
from src.bot.handlers.advanced import (
    predict_command, grading_command, hype_command,
    correlate_command, compare_command, watchvinted_command, photo_handler,
)
from src.bot.handlers.stats import stats_command, target_command, backup_command
from src.bot.handlers.evaluate import evaluate_command
from src.bot.handlers.offer import offer_command
from src.bot.handlers.link_analyzer import link_handler
from src.scheduler.jobs import setup_scheduler

logger = logging.getLogger(__name__)


HELP_TEXT = "🎴 *Nerd Nostalgia Analytics Bot*\nDigita /help\\_full per la guida completa."

HELP_FULL_TEXT = """
🎴 *Nerd Nostalgia Analytics Bot*
_Il tuo assistente per comprare e vendere carte e videogiochi al momento giusto._

━━━━━━━━━━━━━━━━━━━━━━

📌 *GUIDA RAPIDA*

Incolla un *link* Vinted/eBay/Subito/Cardmarket e il bot ti dice subito se conviene e quanto offrire.

Invia una *foto* di una carta e il bot la riconosce e ti da' il prezzo.

━━━━━━━━━━━━━━━━━━━━━━

🔍 *CERCA E ANALIZZA*

/search <nome>
  Cerca un prodotto su PriceCharting.
  _Es: /search charizard base set_

/signal <nome>
  Analisi tecnica completa con segnale BUY/SELL.
  Usa indicatori RSI, MACD, SMA, Bollinger Bands
  adattati al mercato del collezionismo.
  _Es: /signal pokemon emerald_

/chart <nome>
  Grafico professionale con 3 pannelli:
  prezzo + medie mobili, RSI, MACD.
  _Es: /chart zelda ocarina of time_

/grading <nome>
  Prezzi per ogni condizione:
  Ungraded, Graded PSA, New/Sealed, Complete in Box.
  Mostra il premium % del grading.
  _Es: /grading charizard base set_

/predict <nome>
  Previsione prezzo a 30, 60 e 90 giorni
  con range di confidenza (usa Facebook Prophet).
  _Es: /predict mewtwo base set_

/correlate <nome>
  Trova prodotti il cui prezzo si muove
  nella stessa direzione (utile per anticipare trend).
  _Es: /correlate blastoise base set_

/hype <nome>
  Livello di hype su Reddit (score 0-100).
  Mostra i post piu' popolari del mese.
  _Es: /hype charizard_

━━━━━━━━━━━━━━━━━━━━━━

💰 *COMPRA: VALUTA E OFFRI*

/evaluate <nome> <prezzo in €>
  Qualcuno ti offre qualcosa? Scopri se conviene.
  Analizza prezzo mercato, Vinted, segnale tecnico,
  previsione, hype e margini di rivendita.
  Verdetto: da AFFARE! a NON CONVIENE.
  _Es: /evaluate charizard base set 350_

/offer <nome> [margine%]
  Calcola quanto offrire al massimo.
  Considera i prezzi reali su Vinted e PriceCharting,
  le commissioni di ogni piattaforma e il trend.
  Ti da' un range: parti da X, sali fino a Y max.
  Default: 30% di margine.
  _Es: /offer charizard base set_
  _Es: /offer pokemon emerald 40_

🔗 *Incolla un link* di un annuncio
  Vinted, eBay, Subito, Cardmarket — il bot legge
  titolo e prezzo, li confronta col mercato e ti dice
  se conviene + quanto offrire.

━━━━━━━━━━━━━━━━━━━━━━

🛒 *VINTED DEAL FINDER*

/deals <nome>
  Cerca affari su Vinted: inserzioni sotto il prezzo
  di mercato, con percentuale di sconto.
  Filtra automaticamente fake e inserzioni sospette.
  _Es: /deals charizard_

/vinted <nome>
  Lista inserzioni Vinted ordinate per prezzo.
  _Es: /vinted pokemon emerald_

/watchvinted <nome> < prezzo [paesi]
  Monitoraggio real-time (ogni 10 min).
  Ti notifica appena esce un'inserzione sotto la soglia.
  Anti-fake integrato. Cerca in piu' paesi EU
  traducendo automaticamente i nomi (Charizard →
  Dracaufeu in Francia, Glurak in Germania).
  _Es: /watchvinted charizard < 100_
  _Es: /watchvinted charizard < 100 [it,fr,de,es]_
  /watchvinted — lista watch attivi
  /watchvinted off <nome> — disattiva

━━━━━━━━━━━━━━━━━━━━━━

💼 *VENDI: PORTFOLIO E BUSINESS*

/target <prezzo acquisto> <margine%>
  Calcola il prezzo di vendita su ogni piattaforma
  per ottenere il margine desiderato.
  Include commissioni: Vinted 5%+€0.70, eBay 13%,
  Cardmarket 5%, Subito/Wallapop gratis.
  _Es: /target 50 30  (comprato a €50, voglio 30%)_

/portfolio
  Il tuo portfolio con P&L per ogni prodotto.
  Mostra investito, valore attuale, guadagno/perdita.

/sell <nome> <prezzo vendita>
  Registra la vendita di un prodotto.
  _Es: /sell charizard 500_

/stats
  Statistiche complete delle tue vendite:
  margine medio, miglior/peggior trade,
  tempo medio di vendita, top 3 profittevoli.

/export — Esporta portfolio in CSV
/portfoliochart — Grafico andamento portfolio

━━━━━━━━━━━━━━━━━━━━━━

👁 *WATCHLIST E ALERT*

/watchall <nome>
  Monitora TUTTE le varianti di un prodotto.
  _Es: /watchall charizard (aggiunge 300+ varianti)_

/alertall <nome> [buy|sell]
  Alert su tutte le varianti.
  _Es: /alertall charizard_
  _Es: /alertall rayquaza sell_

/pricealert <nome> < prezzo
  Notifica quando il prezzo scende sotto una soglia.
  _Es: /pricealert charizard base set < 400_
  _Es: /pricealert pokemon emerald > 200_

/watchlist — vedi prodotti monitorati
/alert — vedi alert attivi
/unwatchall <nome> — rimuovi tutte le varianti

━━━━━━━━━━━━━━━━━━━━━━

📊 *PANORAMICA MERCATO*

/trending
  Top rialzi e ribassi tra i prodotti nella tua watchlist.

/opportunities
  Tutti i prodotti con segnale BUY adesso,
  con link diretti per acquistare.

/compare <nome>
  Confronto prezzi: PriceCharting vs Vinted
  con percentuale di risparmio.
  _Es: /compare charizard base set_

━━━━━━━━━━━━━━━━━━━━━━

🛠 *UTILITA'*

📷 Invia una *foto* di una carta → riconoscimento + prezzo
💾 /backup — scarica il database come file
/help — menu rapido
/help\\_full — questa guida completa

━━━━━━━━━━━━━━━━━━━━━━

💡 *Segnali: come leggerli*
🟢🟢 STRONG BUY — Compralo subito
🟢 BUY — Buon momento per comprare
🟡 HOLD — Aspetta
🔴 SELL — Valuta di vendere
🔴🔴 STRONG SELL — Vendi subito

Indicatori usati: SMA, EMA, RSI, MACD, Bollinger Bands
adattati automaticamente alla frequenza dei dati
(giornaliera, settimanale o mensile).
+ Rilevamento spike anomali (hype temporaneo)
+ Analisi stagionalita' (Natale, post-feste, estate)
+ Previsione ML con Facebook Prophet
"""


async def help_command(update, context):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def help_full_command(update, context):
    # Telegram has 4096 char limit per message, split if needed
    text = HELP_FULL_TEXT.strip()
    # Split by section divider
    sections = text.split("━━━━━━━━━━━━━━━━━━━━━━")

    current_msg = ""
    for section in sections:
        section = section.strip()
        if not section:
            continue
        candidate = current_msg + "\n━━━━━━━━━━━━━━━━━━━━━━\n" + section if current_msg else section
        if len(candidate) > 3800:
            if current_msg:
                await update.message.reply_text(current_msg, parse_mode="Markdown")
            current_msg = section
        else:
            current_msg = candidate

    if current_msg:
        await update.message.reply_text(current_msg, parse_mode="Markdown")


async def start_command(update, context):
    welcome = (
        f"Ciao {update.message.from_user.first_name}! 👋\n\n"
        f"🎴 Sono *Nerd Nostalgia Analytics*, il tuo assistente "
        f"per comprare e vendere carte collezionabili e videogiochi "
        f"al momento giusto.\n\n"
        f"*Come iniziare:*\n"
        f"🔍 /search charizard — cerca un prodotto\n"
        f"💰 /evaluate charizard base set 350 — ti offrono qualcosa? Valuta se conviene\n"
        f"🧮 /offer charizard base set — quanto offrire\n"
        f"🔗 Incolla un *link* Vinted/eBay/Subito — analisi automatica\n"
        f"📷 Manda una *foto* di una carta — riconoscimento\n\n"
        f"📖 /help\\_full per la guida completa di tutti i comandi"
    )
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler - logs errors and notifies user."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    # Try to notify the user
    if isinstance(update, Update):
        error_msg = str(context.error)
        # Simplify common errors
        if "TimedOut" in error_msg:
            user_msg = "⏳ Timeout nella richiesta. Riprova tra qualche secondo."
        elif "Button_data_invalid" in error_msg:
            user_msg = "⚠ Errore nei dati del pulsante. Usa il comando testuale."
        elif "Message is not modified" in error_msg:
            return  # Ignore this harmless error
        else:
            user_msg = f"⚠ Si e' verificato un errore. Riprova.\nDettaglio: {error_msg[:200]}"

        try:
            if update.callback_query:
                await update.callback_query.answer(user_msg[:200], show_alert=True)
            elif update.message:
                await update.message.reply_text(user_msg)
        except Exception:
            pass


async def post_init(application):
    """Initialize DB and scheduler after bot starts."""
    await init_db()
    setup_scheduler(application)
    logger.info("Bot initialized successfully")


def create_bot() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).post_init(post_init).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("help_full", help_full_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("alert", alert_command))
    app.add_handler(CommandHandler("portfolio", portfolio_command))
    app.add_handler(CommandHandler("watchall", watchall_command))
    app.add_handler(CommandHandler("alertall", alertall_command))
    app.add_handler(CommandHandler("unwatchall", unwatchall_command))
    app.add_handler(CommandHandler("pricealert", pricealert_command))
    app.add_handler(CommandHandler("trending", trending_command))
    app.add_handler(CommandHandler("opportunities", opportunities_command))
    app.add_handler(CommandHandler("sell", sell_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("portfoliochart", portfolio_chart_command))
    app.add_handler(CommandHandler("deals", deals_command))
    app.add_handler(CommandHandler("vinted", vinted_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("grading", grading_command))
    app.add_handler(CommandHandler("hype", hype_command))
    app.add_handler(CommandHandler("correlate", correlate_command))
    app.add_handler(CommandHandler("compare", compare_command))
    app.add_handler(CommandHandler("watchvinted", watchvinted_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("target", target_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("evaluate", evaluate_command))
    app.add_handler(CommandHandler("offer", offer_command))

    # Photo handler for card recognition
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # Link handler for marketplace URL analysis (must be after conversation handler)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r'https?://\S+'),
        link_handler,
    ))

    # Portfolio conversation handler
    portfolio_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(portfolio_add_callback, pattern=r"^padd:")],
        states={
            WAITING_BUY_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, portfolio_buy_price)],
            WAITING_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, portfolio_quantity)],
        },
        fallbacks=[CommandHandler("cancel", portfolio_cancel)],
    )
    app.add_handler(portfolio_conv)

    # Callback query handlers
    app.add_handler(CallbackQueryHandler(select_product_callback, pattern=r"^sel:"))
    app.add_handler(CallbackQueryHandler(signal_callback, pattern=r"^sig:"))
    app.add_handler(CallbackQueryHandler(chart_callback, pattern=r"^cht:"))
    app.add_handler(CallbackQueryHandler(watch_callback, pattern=r"^wat:"))
    app.add_handler(CallbackQueryHandler(unwatch_callback, pattern=r"^uwat:"))
    app.add_handler(CallbackQueryHandler(alert_buy_callback, pattern=r"^abuy:"))

    # Cancel callback
    async def cancel_callback(update, context):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Operazione annullata.")

    app.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel$"))

    # Global error handler
    app.add_error_handler(error_handler)

    return app
