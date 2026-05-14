from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def search_result_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    """products must have 'name', 'product_id' (DB int), optionally 'current_price'."""
    buttons = []
    for p in products:
        price_str = f" - ${p['current_price']:.2f}" if p.get("current_price") else ""
        text = f"{p['name'][:40]}{price_str}"
        buttons.append([InlineKeyboardButton(text, callback_data=f"sel:{p['product_id']}")])
    return InlineKeyboardMarkup(buttons)


def product_actions_keyboard(product_id: int) -> InlineKeyboardMarkup:
    pid = str(product_id)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Segnale", callback_data=f"sig:{pid}"),
            InlineKeyboardButton("📈 Grafico", callback_data=f"cht:{pid}"),
        ],
        [
            InlineKeyboardButton("👁 Watchlist", callback_data=f"wat:{pid}"),
            InlineKeyboardButton("🔔 Alert BUY", callback_data=f"abuy:{pid}"),
        ],
        [
            InlineKeyboardButton("💰 Aggiungi a Portfolio", callback_data=f"padd:{pid}"),
        ],
    ])


def watchlist_item_keyboard(product_id: int) -> InlineKeyboardMarkup:
    pid = str(product_id)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Segnale", callback_data=f"sig:{pid}"),
            InlineKeyboardButton("📈 Grafico", callback_data=f"cht:{pid}"),
        ],
        [
            InlineKeyboardButton("❌ Rimuovi", callback_data=f"uwat:{pid}"),
        ],
    ])
