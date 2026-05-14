"""
Advanced handlers: /predict, /grading, /hype, /correlate, /compare, /watchvinted, /photo
"""
import io
import logging

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from src.bot.handlers.signal import get_or_fetch_prices
from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.reddit import search_hype, calculate_hype_score
from src.collectors.vinted import VintedCollector
from src.analysis.prediction import predict_prices, format_prediction
from src.analysis.correlation import find_correlated_products
from src.db.database import async_session
from src.db.models import Product, VintedWatch
from src.utils.currency import get_exchange_rates, format_price, usd_to_eur
from src.utils.buy_links import get_buy_links

logger = logging.getLogger(__name__)
pc = PriceChartingCollector()
vinted = VintedCollector()


# --- /predict ---
async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/predict <nome> - Previsione prezzo a 30/60/90 giorni."""
    if not context.args:
        await update.message.reply_text("Uso: /predict <nome prodotto>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"🔮 Calcolo previsione per '{query}'...")

    results = await pc.search(query, max_results=1)
    if not results:
        await msg.edit_text("Prodotto non trovato.")
        return

    product_result = results[0]
    async with async_session() as session:
        existing = await session.execute(
            select(Product).where(
                Product.external_id == product_result.external_id,
                Product.source == product_result.source,
            )
        )
        product = existing.scalar_one_or_none()
        if not product:
            product = Product(
                external_id=product_result.external_id, source=product_result.source,
                name=product_result.name, category=product_result.category,
                set_name=product_result.set_name, product_url=product_result.product_url,
                current_price=product_result.current_price,
            )
            session.add(product)
            await session.commit()
            await session.refresh(product)

    df = await get_or_fetch_prices(product.id)
    if df is None or len(df) < 10:
        await msg.edit_text("Dati insufficienti per la previsione (minimo 10 data points).")
        return

    pred = predict_prices(df)
    if not pred:
        await msg.edit_text("Impossibile calcolare la previsione.")
        return

    text = f"🔮 *Previsione: {product.name}*\n\n```\n{format_prediction(pred)}\n```"
    text += "\n\n⚠ _Le previsioni sono basate su dati storici e non garantiscono risultati futuri._"
    await msg.edit_text(text, parse_mode="Markdown")


# --- /grading ---
async def grading_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/grading <nome> - Prezzi per condizione (ungraded, graded, sealed, ecc.)."""
    if not context.args:
        await update.message.reply_text("Uso: /grading <nome prodotto>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"📋 Cerco prezzi per condizione di '{query}'...")

    results = await pc.search(query, max_results=1)
    if not results:
        await msg.edit_text("Prodotto non trovato.")
        return

    product = results[0]
    conditions = await pc.get_all_conditions(product.external_id)

    if not conditions:
        await msg.edit_text("Dati per condizione non disponibili per questo prodotto.")
        return

    rates = await get_exchange_rates()

    lines = [f"📋 *{product.name}*\n", "*Prezzi per condizione:*\n"]
    for condition, prices in conditions.items():
        if prices:
            latest = prices[-1].price
            # Show trend
            if len(prices) >= 3:
                prev = prices[-3].price
                change = ((latest - prev) / prev * 100) if prev > 0 else 0
                trend = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                lines.append(
                    f"{trend} *{condition}*: {format_price(latest, rates)} ({change:+.1f}%)"
                )
            else:
                lines.append(f"  *{condition}*: {format_price(latest, rates)}")

    # Show premium for grading
    ungraded_key = next((k for k in conditions if "Ungraded" in k), None)
    graded_key = next((k for k in conditions if "Graded" in k), None)
    if ungraded_key and graded_key:
        ug_price = conditions[ungraded_key][-1].price
        g_price = conditions[graded_key][-1].price
        if ug_price > 0:
            premium = ((g_price - ug_price) / ug_price) * 100
            lines.append(f"\n💎 Premium grading: {premium:+.0f}%")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


# --- /hype ---
async def hype_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hype <nome> - Controlla l'hype su Reddit."""
    if not context.args:
        await update.message.reply_text("Uso: /hype <nome prodotto>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Cerco hype su Reddit per '{query}'...")

    posts = await search_hype(query)
    hype_score, description = calculate_hype_score(posts)

    lines = [
        f"📡 *Hype Monitor: '{query}'*\n",
        f"Score: *{hype_score}/100*",
        f"{description}\n",
    ]

    if posts:
        lines.append(f"*Top post recenti ({len(posts)}):*\n")
        for post in sorted(posts, key=lambda p: p.score, reverse=True)[:7]:
            date_str = post.created_utc.strftime("%d/%m")
            lines.append(
                f"⬆ {post.score} | 💬 {post.num_comments} | r/{post.subreddit}\n"
                f"  [{post.title[:55]}]({post.url}) ({date_str})"
            )
    else:
        lines.append("Nessun post trovato nell'ultimo mese.")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


# --- /correlate ---
async def correlate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/correlate <nome> - Trova prodotti con prezzo correlato."""
    if not context.args:
        await update.message.reply_text("Uso: /correlate <nome prodotto>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"🔗 Cerco correlazioni per '{query}'...")

    results = await pc.search(query, max_results=1)
    if not results:
        await msg.edit_text("Prodotto non trovato.")
        return

    product_result = results[0]
    async with async_session() as session:
        existing = await session.execute(
            select(Product).where(
                Product.external_id == product_result.external_id,
                Product.source == product_result.source,
            )
        )
        product = existing.scalar_one_or_none()

    if not product:
        await msg.edit_text("Prodotto non ancora nel database. Usa prima /signal per caricarlo.")
        return

    correlations = await find_correlated_products(product.id)

    if not correlations:
        await msg.edit_text(
            f"Nessuna correlazione trovata per *{product.name}*.\n"
            f"Serve avere piu' prodotti nella watchlist con storico prezzi.",
            parse_mode="Markdown",
        )
        return

    lines = [f"🔗 *Correlazioni con {product.name}*\n"]
    for corr in correlations:
        emoji = "🟢" if corr.correlation > 0 else "🔴"
        price_str = f" - ${corr.current_price:.2f}" if corr.current_price else ""
        lines.append(
            f"{emoji} *{corr.product_name}*{price_str}\n"
            f"   Correlazione: {corr.correlation:.2f}"
        )

    lines.append(
        "\n_Correlazione positiva = si muovono insieme._\n"
        "_Correlazione negativa = si muovono in direzioni opposte._"
    )
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


# --- /compare ---
async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/compare <nome> - Confronta prezzi su tutte le piattaforme."""
    if not context.args:
        await update.message.reply_text("Uso: /compare <nome prodotto>")
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Confronto prezzi per '{query}'...")

    rates = await get_exchange_rates()

    # PriceCharting
    pc_results = await pc.search(query, max_results=1)
    pc_price = None
    product_name = query
    if pc_results:
        pc_price = pc_results[0].current_price
        product_name = pc_results[0].name

    # Vinted (cheapest)
    vinted_listings = await vinted.search_listings(query, max_results=10, order="price_low_to_high")
    vinted_relevant = [l for l in vinted_listings if vinted._title_matches(l.title, query)]
    vinted_min = vinted_relevant[0].price_eur if vinted_relevant else None
    vinted_avg = (sum(l.price_eur for l in vinted_relevant[:5]) / min(5, len(vinted_relevant))
                  if vinted_relevant else None)

    lines = [f"📊 *Confronto prezzi: {product_name}*\n"]

    if pc_price:
        lines.append(f"📈 *PriceCharting* (mercato USA): {format_price(pc_price, rates)}")

    if vinted_min is not None:
        lines.append(f"👗 *Vinted* (minimo): €{vinted_min:.2f}")
        if vinted_avg:
            lines.append(f"👗 *Vinted* (media top 5): €{vinted_avg:.2f}")

    if pc_price and vinted_min:
        pc_eur = usd_to_eur(pc_price, rates)
        if pc_eur > 0:
            savings = ((pc_eur - vinted_min) / pc_eur) * 100
            if savings > 0:
                lines.append(f"\n💰 *Risparmio Vinted vs mercato: {savings:.0f}%*")
            else:
                lines.append(f"\n⚠ Vinted costa {abs(savings):.0f}% in piu' del mercato")

    # Buy links
    category = pc_results[0].category if pc_results else "other"
    product_url = pc_results[0].product_url if pc_results else None
    links = get_buy_links(product_name, category, product_url)
    lines.append(f"\n{links}")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


# --- /watchvinted ---
async def watchvinted_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /watchvinted <nome> < prezzo — monitora Vinted e notifica inserzioni sotto soglia.
    /watchvinted — mostra watch attivi.
    /watchvinted off <nome> — disattiva.
    """
    import re
    args = context.args or []

    if not args:
        await _list_vinted_watches(update)
        return

    if args[0].lower() == "off":
        await _deactivate_vinted_watch(update, " ".join(args[1:]))
        return

    text = " ".join(args)
    # Parse: charizard < 100 [it,fr,de]
    match = re.match(r'^(.+?)\s*<\s*(\d+\.?\d*)\s*(\[[\w,]+\])?$', text)
    if not match:
        await update.message.reply_text(
            "Uso:\n"
            "  /watchvinted charizard < 100\n"
            "  /watchvinted charizard < 100 [it,fr,de]\n"
            "  /watchvinted off charizard\n"
            "  /watchvinted (lista attivi)\n\n"
            "Paesi: it, fr, de, es, nl, be, pt, pl"
        )
        return

    search_query = match.group(1).strip()
    max_price = float(match.group(2))
    countries_str = match.group(3)
    countries = "it"
    if countries_str:
        countries = countries_str.strip("[]").strip()

    # Auto min-price: 10% of max or €0.50, whichever is higher (anti-fake)
    min_price = max(0.50, max_price * 0.10)
    user_id = update.message.from_user.id

    async with async_session() as session:
        session.add(VintedWatch(
            telegram_user_id=user_id,
            search_query=search_query,
            max_price_eur=max_price,
            min_price_eur=min_price,
            countries=countries,
        ))
        await session.commit()

    country_list = countries.split(",")
    country_flags = {"it": "🇮🇹", "fr": "🇫🇷", "de": "🇩🇪", "es": "🇪🇸", "nl": "🇳🇱", "be": "🇧🇪", "pt": "🇵🇹", "pl": "🇵🇱"}
    flags = " ".join(country_flags.get(c.strip(), c) for c in country_list)

    await update.message.reply_text(
        f"👁 *Vinted Watch attivato!*\n\n"
        f"🔍 Cerco: '{search_query}'\n"
        f"💰 Range: €{min_price:.2f} - €{max_price:.2f}\n"
        f"🌍 Paesi: {flags}\n"
        f"🛡 Anti-fake: inserzioni sotto €{min_price:.2f} ignorate\n\n"
        f"Controllo ogni 10 minuti. Notifica appena esce un'inserzione.",
        parse_mode="Markdown",
    )


async def _list_vinted_watches(update: Update):
    user_id = update.message.from_user.id
    async with async_session() as session:
        result = await session.execute(
            select(VintedWatch).where(
                VintedWatch.telegram_user_id == user_id,
                VintedWatch.is_active == True,
            ).order_by(VintedWatch.created_at.desc())
        )
        watches = result.scalars().all()

    if not watches:
        await update.message.reply_text(
            "Nessun Vinted watch attivo.\n"
            "Uso: /watchvinted charizard < 100"
        )
        return

    lines = [f"👁 *Vinted Watch attivi ({len(watches)}):*\n"]
    for w in watches:
        lines.append(f"• {w.search_query} < €{w.max_price_eur:.2f}")
    lines.append("\nPer disattivare: /watchvinted off <nome>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _deactivate_vinted_watch(update: Update, search_term: str):
    if not search_term:
        await update.message.reply_text("Uso: /watchvinted off <nome>")
        return

    user_id = update.message.from_user.id
    search_lower = search_term.lower()

    async with async_session() as session:
        result = await session.execute(
            select(VintedWatch).where(
                VintedWatch.telegram_user_id == user_id,
                VintedWatch.is_active == True,
            )
        )
        watches = result.scalars().all()

        deactivated = 0
        for w in watches:
            if search_lower in w.search_query.lower():
                w.is_active = False
                deactivated += 1
        await session.commit()

    if deactivated:
        await update.message.reply_text(f"✅ {deactivated} Vinted watch disattivato/i.")
    else:
        await update.message.reply_text(f"Nessun watch trovato per '{search_term}'.")


# --- /photo (card recognition) ---

# All known Pokemon names for matching against OCR garbage
KNOWN_POKEMON = [
    "bulbasaur", "ivysaur", "venusaur", "charmander", "charmeleon", "charizard",
    "squirtle", "wartortle", "blastoise", "caterpie", "metapod", "butterfree",
    "weedle", "kakuna", "beedrill", "pidgey", "pidgeotto", "pidgeot",
    "rattata", "raticate", "spearow", "fearow", "ekans", "arbok",
    "pikachu", "raichu", "sandshrew", "sandslash", "nidoran", "nidorina",
    "nidoqueen", "nidorino", "nidoking", "clefairy", "clefable", "vulpix",
    "ninetales", "jigglypuff", "wigglytuff", "zubat", "golbat", "oddish",
    "gloom", "vileplume", "paras", "parasect", "venonat", "venomoth",
    "diglett", "dugtrio", "meowth", "persian", "psyduck", "golduck",
    "mankey", "primeape", "growlithe", "arcanine", "poliwag", "poliwhirl",
    "poliwrath", "abra", "kadabra", "alakazam", "machop", "machoke",
    "machamp", "bellsprout", "weepinbell", "victreebel", "tentacool",
    "tentacruel", "geodude", "graveler", "golem", "ponyta", "rapidash",
    "slowpoke", "slowbro", "magnemite", "magneton", "farfetch", "doduo",
    "dodrio", "seel", "dewgong", "grimer", "muk", "shellder", "cloyster",
    "gastly", "haunter", "gengar", "onix", "drowzee", "hypno", "krabby",
    "kingler", "voltorb", "electrode", "exeggcute", "exeggutor", "cubone",
    "marowak", "hitmonlee", "hitmonchan", "lickitung", "koffing", "weezing",
    "rhyhorn", "rhydon", "chansey", "tangela", "kangaskhan", "horsea",
    "seadra", "goldeen", "seaking", "staryu", "starmie", "mr. mime",
    "scyther", "jynx", "electabuzz", "magmar", "pinsir", "tauros",
    "magikarp", "gyarados", "lapras", "ditto", "eevee", "vaporeon",
    "jolteon", "flareon", "porygon", "omanyte", "omastar", "kabuto",
    "kabutops", "aerodactyl", "snorlax", "articuno", "zapdos", "moltres",
    "dratini", "dragonair", "dragonite", "mewtwo", "mew",
    # Gen 2
    "chikorita", "bayleef", "meganium", "cyndaquil", "quilava", "typhlosion",
    "totodile", "croconaw", "feraligatr", "lugia", "ho-oh", "celebi",
    "espeon", "umbreon", "tyranitar", "scizor", "heracross", "skarmory",
    # Gen 3
    "treecko", "grovyle", "sceptile", "torchic", "combusken", "blaziken",
    "mudkip", "marshtomp", "swampert", "gardevoir", "aggron", "absol",
    "salamence", "metagross", "latias", "latios", "kyogre", "groudon",
    "rayquaza", "deoxys", "jirachi",
    # Gen 4+
    "lucario", "garchomp", "dialga", "palkia", "giratina", "darkrai",
    "arceus", "reshiram", "zekrom", "kyurem", "xerneas", "yveltal",
    "zygarde", "solgaleo", "lunala", "necrozma", "zacian", "zamazenta",
    "eternatus", "calyrex", "miraidon", "koraidon",
    # Common card types
    "ex", "gx", "vmax", "vstar", "v", "mega", "prime", "lv.x",
    "tag team", "rainbow", "gold star", "shiny", "full art",
    "alt art", "secret rare", "illustration rare",
]


def _detect_pokemon_in_text(text: str) -> list[str]:
    """Find known Pokemon names in OCR text using fuzzy matching."""
    from rapidfuzz import fuzz, process

    text_lower = text.lower()
    found = []

    # Direct substring match first
    for name in KNOWN_POKEMON:
        if len(name) >= 4 and name in text_lower:
            found.append(name)

    # Fuzzy match each word against Pokemon names
    words = [w for w in text_lower.split() if len(w) >= 4]
    pokemon_names = [p for p in KNOWN_POKEMON if len(p) >= 4]

    for word in words:
        matches = process.extract(word, pokemon_names, scorer=fuzz.ratio, limit=1)
        if matches and matches[0][1] >= 80:  # 80% similarity threshold
            found.append(matches[0][0])

    # Deduplicate keeping order
    seen = set()
    unique = []
    for name in found:
        if name not in seen and name not in ("ex", "gx", "v", "mega", "prime"):
            seen.add(name)
            unique.append(name)
    return unique


def _google_lens_url(photo_url: str) -> str:
    from urllib.parse import quote_plus
    return f"https://lens.google.com/uploadbyurl?url={quote_plus(photo_url)}"


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle photo messages — card recognition.

    Strategy priority:
    1. Photo caption (user typed the name) → best
    2. OCR + Pokemon name matching → decent
    3. Google Lens fallback → always works
    """
    if not update.message.photo:
        return

    # --- Strategy 1: Caption ---
    caption = (update.message.caption or "").strip()
    if caption and len(caption) >= 3:
        msg = await update.message.reply_text(f"🔍 Cerco '{caption}'...")
        results = await pc.search(caption, max_results=5)
        if results:
            from src.bot.keyboards import search_result_keyboard
            from src.bot.handlers.search import _save_and_get_product

            products_data = []
            for r in results[:5]:
                product = await _save_and_get_product(r)
                products_data.append({
                    "name": r.name, "product_id": product.id, "current_price": r.current_price,
                })

            price_str = f" - ${results[0].current_price:.2f}" if results[0].current_price else ""
            await msg.edit_text(
                f"📷 *{results[0].name}*{price_str}\n\n"
                f"Trovati {len(results)} risultati:",
                parse_mode="Markdown",
            )
            await update.message.reply_text(
                "Seleziona:", reply_markup=search_result_keyboard(products_data),
            )
            return
        await msg.edit_text(f"Nessun risultato per '{caption}'. Prova con un nome diverso.")
        return

    # --- Strategy 2: OCR + Pokemon matching ---
    msg = await update.message.reply_text(
        "📷 Analizzo la carta...\n"
        "💡 _Suggerimento: la prossima volta scrivi il nome nella didascalia della foto!_",
        parse_mode="Markdown",
    )

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    file_url = file.file_path

    try:
        from PIL import Image, ImageEnhance, ImageFilter
        import pytesseract

        img = Image.open(io.BytesIO(photo_bytes))
        w, h = img.size

        # Multi-crop OCR
        crops = [
            img.crop((0, 0, w, int(h * 0.25))),  # Top 25% (name)
            img.crop((0, int(h * 0.80), w, h)),   # Bottom 20% (set/number)
            img,                                    # Full image
        ]

        all_text = ""
        for crop in crops:
            if crop.width < 600:
                ratio = 600 / crop.width
                crop = crop.resize((int(crop.width * ratio), int(crop.height * ratio)), Image.LANCZOS)

            for contrast in [1.5, 2.5]:
                gray = crop.convert("L")
                enhanced = ImageEnhance.Contrast(gray).enhance(contrast)
                sharp = enhanced.filter(ImageFilter.SHARPEN)

                for threshold in [120, 160]:
                    binary = sharp.point(lambda x, t=threshold: 255 if x > t else 0)
                    for psm in ["7", "6"]:
                        try:
                            text = pytesseract.image_to_string(binary, config=f"--psm {psm}")
                            all_text += " " + text
                        except Exception:
                            continue

                    # Also inverted
                    inv = sharp.point(lambda x, t=threshold: 255 if x < t else 0)
                    try:
                        text = pytesseract.image_to_string(inv, config="--psm 6")
                        all_text += " " + text
                    except Exception:
                        continue

        # Find Pokemon names in OCR text
        detected = _detect_pokemon_in_text(all_text)

        if detected:
            pokemon_name = detected[0]
            await msg.edit_text(f"🔍 Riconosciuto: *{pokemon_name.title()}*\nCerco...", parse_mode="Markdown")

            # Search with the Pokemon name
            search_queries = [pokemon_name]
            # Add card type modifiers if detected in text
            text_lower = all_text.lower()
            for modifier in ["gold star", "ex", "gx", "vmax", "vstar", "v", "mega", "full art", "alt art"]:
                if modifier in text_lower:
                    search_queries.insert(0, f"{pokemon_name} {modifier}")

            results = []
            for q in search_queries:
                results = await pc.search(q, max_results=5)
                if results:
                    break

            if results:
                from src.bot.keyboards import search_result_keyboard
                from src.bot.handlers.search import _save_and_get_product

                products_data = []
                for r in results[:5]:
                    product = await _save_and_get_product(r)
                    products_data.append({
                        "name": r.name, "product_id": product.id, "current_price": r.current_price,
                    })

                await msg.edit_text(
                    f"📷 Riconosciuto: *{pokemon_name.title()}*\n"
                    f"Trovati {len(results)} risultati:",
                    parse_mode="Markdown",
                )
                await update.message.reply_text(
                    "Seleziona:", reply_markup=search_result_keyboard(products_data),
                )
                return

        # --- Strategy 3: Google Lens fallback ---
        lens_url = _google_lens_url(file_url) if file_url else ""
        ocr_preview = all_text.strip().replace("\n", " ")[:80] if all_text.strip() else "nessuno"

        await msg.edit_text(
            f"📷 Non sono riuscito a identificare la carta.\n"
            f"_OCR: {ocr_preview}_\n\n"
            f"*Prova cosi':*\n"
            f"1. 🔍 [Apri Google Lens]({lens_url}) — riconosce quasi tutto\n"
            f"2. 📷 Rimanda la foto scrivendo il nome nella *didascalia*\n"
            f"3. 📝 /search mewtwo gold star",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    except ImportError:
        lens_url = _google_lens_url(file_url) if file_url else ""
        await msg.edit_text(
            f"🔍 [Cerca con Google Lens]({lens_url})\n"
            "📝 Oppure /search <nome carta>\n\n"
            "💡 Rimanda la foto con il nome nella didascalia!",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Photo recognition failed: {e}")
        await msg.edit_text(
            f"📷 Errore riconoscimento.\n\n"
            f"💡 Rimanda la foto scrivendo il nome nella didascalia!\n"
            f"📝 Oppure /search <nome carta>"
        )
