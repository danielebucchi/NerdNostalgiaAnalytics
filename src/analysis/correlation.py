import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select

from src.db.database import async_session
from src.db.models import Product, PriceHistory

logger = logging.getLogger(__name__)


@dataclass
class CorrelationPair:
    product_name: str
    product_id: int
    correlation: float  # -1 to 1
    current_price: float | None


async def find_correlated_products(
    product_id: int, min_correlation: float = 0.7, max_results: int = 10,
) -> list[CorrelationPair]:
    """
    Find products whose price movements correlate with the given product.
    Looks at products in the same set or category.
    """
    async with async_session() as session:
        # Get the target product
        result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        target = result.scalar_one_or_none()
        if not target:
            return []

        # Get target's price history
        target_prices = await _get_price_series(session, product_id)
        if target_prices is None or len(target_prices) < 10:
            return []

        # Find related products (same category, or name overlap)
        name_parts = target.name.lower().split()
        # Get products with similar names or same set
        all_products = await session.execute(
            select(Product).where(
                Product.id != product_id,
                Product.category == target.category,
            ).limit(200)
        )
        candidates = all_products.scalars().all()

    correlations = []
    for candidate in candidates:
        async with async_session() as session:
            candidate_prices = await _get_price_series(session, candidate.id)
        if candidate_prices is None or len(candidate_prices) < 10:
            continue

        corr = _calculate_correlation(target_prices, candidate_prices)
        if corr is not None and abs(corr) >= min_correlation:
            correlations.append(CorrelationPair(
                product_name=candidate.name,
                product_id=candidate.id,
                correlation=corr,
                current_price=candidate.current_price,
            ))

    correlations.sort(key=lambda x: abs(x.correlation), reverse=True)
    return correlations[:max_results]


async def _get_price_series(session, product_id: int) -> pd.Series | None:
    result = await session.execute(
        select(PriceHistory.date, PriceHistory.price)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.date.asc())
    )
    rows = result.all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "price"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")["price"].resample("MS").last().dropna()
    return df


def _calculate_correlation(s1: pd.Series, s2: pd.Series) -> float | None:
    """Calculate Pearson correlation between two price series, aligned by date."""
    # Align on common dates
    combined = pd.DataFrame({"a": s1, "b": s2}).dropna()
    if len(combined) < 5:
        return None
    # Use returns instead of raw prices for better correlation measure
    returns_a = combined["a"].pct_change().dropna()
    returns_b = combined["b"].pct_change().dropna()
    if len(returns_a) < 5:
        return None
    return float(returns_a.corr(returns_b))
