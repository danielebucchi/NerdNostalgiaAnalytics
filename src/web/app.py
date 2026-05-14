import json
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

from src.analysis.indicators import analyze, SIGNAL_EMOJI, Signal
from src.analysis.prediction import predict_prices
from src.bot.handlers.signal import get_or_fetch_prices
from src.collectors.pricecharting import PriceChartingCollector
from src.collectors.vinted import VintedCollector
from src.collectors.reddit import search_hype, calculate_hype_score
from src.db.database import async_session, init_db
from src.db.models import Product, WatchlistEntry, PortfolioEntry, Alert, PriceHistory
from src.utils.currency import get_exchange_rates, usd_to_eur

BASE_DIR = Path(__file__).parent
pc = PriceChartingCollector()
vinted = VintedCollector()

app = FastAPI(title="Nerd Nostalgia Analytics")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
TEMPLATE_DIR = BASE_DIR / "templates"


@app.on_event("startup")
async def startup():
    await init_db()


# --- PAGES ---

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_file = TEMPLATE_DIR / "dashboard.html"
    return HTMLResponse(content=html_file.read_text())


# --- API ---

@app.get("/api/portfolio")
async def api_portfolio():
    async with async_session() as session:
        result = await session.execute(
            select(PortfolioEntry, Product)
            .join(Product, PortfolioEntry.product_id == Product.id)
            .where(PortfolioEntry.sold == False)
            .order_by(PortfolioEntry.buy_date.desc())
        )
        entries = result.all()

    items = []
    total_invested = 0
    total_current = 0
    for entry, product in entries:
        invested = entry.buy_price * entry.quantity
        current = (product.current_price or entry.buy_price) * entry.quantity
        pnl = current - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0
        total_invested += invested
        total_current += current
        items.append({
            "name": product.name, "category": product.category,
            "buy_price": entry.buy_price, "quantity": entry.quantity,
            "current_price": product.current_price,
            "invested": round(invested, 2), "current_value": round(current, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 1),
        })

    return {
        "items": items,
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_pnl": round(total_current - total_invested, 2),
        "total_pnl_pct": round((total_current - total_invested) / total_invested * 100, 1) if total_invested > 0 else 0,
    }


@app.get("/api/watchlist")
async def api_watchlist():
    async with async_session() as session:
        result = await session.execute(
            select(Product)
            .join(WatchlistEntry, WatchlistEntry.product_id == Product.id)
            .distinct()
            .order_by(Product.name)
        )
        products = result.scalars().all()

    items = []
    for p in products:
        items.append({
            "id": p.id, "name": p.name, "category": p.category,
            "current_price": p.current_price, "source": p.source,
            "product_url": p.product_url,
        })
    return {"items": items, "total": len(items)}


@app.get("/api/signals")
async def api_signals(limit: int = Query(20)):
    """Get signals for all watched products."""
    async with async_session() as session:
        result = await session.execute(
            select(Product)
            .join(WatchlistEntry, WatchlistEntry.product_id == Product.id)
            .distinct().limit(limit)
        )
        products = result.scalars().all()

    signals = []
    for product in products:
        df = await get_or_fetch_prices(product.id)
        if df is None or len(df) < 6:
            continue
        analysis = analyze(df)
        if not analysis:
            continue
        signals.append({
            "id": product.id, "name": product.name,
            "price": analysis.current_price,
            "signal": analysis.signal.value,
            "score": analysis.score,
            "rsi": round(analysis.rsi, 1) if analysis.rsi else None,
            "is_spike": analysis.is_spike,
            "change_short": round(analysis.price_change_short, 1) if analysis.price_change_short else None,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return {"signals": signals}


@app.get("/api/chart/{product_id}")
async def api_chart(product_id: int):
    """Get chart data for Plotly."""
    df = await get_or_fetch_prices(product_id)
    if df is None or len(df) < 3:
        return {"error": "No data"}

    async with async_session() as session:
        result = await session.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    data = {
        "name": product.name if product else "",
        "dates": df["date"].dt.strftime("%Y-%m-%d").tolist(),
        "prices": df["price"].astype(float).tolist(),
    }

    # Add indicators
    if len(df) >= 6:
        data["sma3"] = df["price"].rolling(3).mean().tolist()
        data["sma6"] = df["price"].rolling(6).mean().tolist()
    if len(df) >= 12:
        data["sma12"] = df["price"].rolling(12).mean().tolist()

    return data


@app.get("/api/search")
async def api_search(q: str = Query(...)):
    results = await pc.search(q, max_results=10)
    return {"results": [
        {"name": r.name, "external_id": r.external_id, "price": r.current_price,
         "category": r.category, "url": r.product_url}
        for r in results
    ]}


@app.get("/api/predict/{product_id}")
async def api_predict(product_id: int):
    df = await get_or_fetch_prices(product_id)
    if df is None or len(df) < 10:
        return {"error": "Insufficient data"}

    pred = predict_prices(df)
    if not pred:
        return {"error": "Prediction failed"}

    return {
        "current": pred.current_price, "trend": pred.trend,
        "pred_30d": round(pred.pred_30d, 2), "pred_60d": round(pred.pred_60d, 2),
        "pred_90d": round(pred.pred_90d, 2),
        "pred_30d_range": [round(pred.pred_30d_lower, 2), round(pred.pred_30d_upper, 2)],
        "pred_60d_range": [round(pred.pred_60d_lower, 2), round(pred.pred_60d_upper, 2)],
        "pred_90d_range": [round(pred.pred_90d_lower, 2), round(pred.pred_90d_upper, 2)],
    }


@app.get("/api/deals")
async def api_deals(q: str = Query(...)):
    results = await pc.search(q, max_results=1)
    if not results or not results[0].current_price:
        return {"deals": [], "market_price": None}

    market = results[0].current_price
    deals = await vinted.find_deals(q, market, max_results=10)
    return {
        "market_price_usd": market,
        "deals": [{"title": l.title, "price": l.price_eur, "url": l.url,
                    "discount": round(d, 1), "seller": l.seller, "country": l.country}
                  for l, d in deals],
    }


@app.get("/api/hype")
async def api_hype(q: str = Query(...)):
    posts = await search_hype(q)
    score, desc = calculate_hype_score(posts)
    return {
        "score": score, "description": desc,
        "posts": [{"title": p.title, "subreddit": p.subreddit, "score": p.score,
                    "comments": p.num_comments, "url": p.url,
                    "date": p.created_utc.strftime("%Y-%m-%d")}
                  for p in sorted(posts, key=lambda x: x.score, reverse=True)[:10]],
    }


@app.get("/api/stats")
async def api_stats():
    async with async_session() as session:
        active_count = (await session.execute(
            select(func.count(PortfolioEntry.id)).where(PortfolioEntry.sold == False)
        )).scalar() or 0

        sold_result = await session.execute(
            select(PortfolioEntry).where(PortfolioEntry.sold == True)
        )
        sold_entries = sold_result.scalars().all()

        watchlist_count = (await session.execute(
            select(func.count(WatchlistEntry.id))
        )).scalar() or 0

        alert_count = (await session.execute(
            select(func.count(Alert.id)).where(Alert.is_active == True)
        )).scalar() or 0

    realized_pnl = sum((e.sell_price - e.buy_price) * e.quantity for e in sold_entries if e.sell_price)
    margins = [(e.sell_price - e.buy_price) / e.buy_price * 100
               for e in sold_entries if e.sell_price and e.buy_price > 0]

    return {
        "active_positions": active_count,
        "sold_count": len(sold_entries),
        "watchlist_count": watchlist_count,
        "alert_count": alert_count,
        "realized_pnl": round(realized_pnl, 2),
        "avg_margin": round(sum(margins) / len(margins), 1) if margins else 0,
    }
