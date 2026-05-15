"""
Multi-source price aggregator.
Collects prices from all available sources and produces a weighted fair market value.
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SourcePrice:
    source: str
    price_eur: float
    weight: float  # Higher = more reliable
    note: str = ""


@dataclass
class AggregatedPrice:
    fair_value_eur: float
    sources: list[SourcePrice] = field(default_factory=list)
    confidence: str = "low"  # low, medium, high


def aggregate_prices(
    pricecharting_usd: float | None = None,
    cardmarket_trend_eur: float | None = None,
    cardmarket_avg_sell_eur: float | None = None,
    cardmarket_low_eur: float | None = None,
    tcgplayer_market_usd: float | None = None,
    vinted_avg_eur: float | None = None,
    vinted_min_eur: float | None = None,
    ebay_sold_avg_eur: float | None = None,
    ebay_sold_count: int = 0,
    retrogamingshop_avg_eur: float | None = None,
    usd_to_eur_rate: float = 0.92,
) -> AggregatedPrice:
    """
    Aggregate prices from multiple sources into a fair market value.

    Weight logic (for EU buyer):
    - eBay sold EU: highest weight (real EU sales, actual transactions)
    - Cardmarket avg sell: highest weight (real EU sales data)
    - Cardmarket trend: high weight (EU market indicator)
    - PriceCharting: medium weight (USA market, good history)
    - TCGPlayer market: medium weight (USA market)
    - Vinted avg: lower weight (asking prices, not sales)
    """
    sources = []

    # eBay sold (real transactions — best data)
    if ebay_sold_avg_eur and ebay_sold_avg_eur > 0:
        # Weight increases with more sales data
        weight = min(5.0, 3.0 + ebay_sold_count * 0.1)
        sources.append(SourcePrice(
            "eBay venduti (EU)", ebay_sold_avg_eur, weight,
            f"Media {ebay_sold_count} vendite reali"
        ))

    # EU sources (higher weight for EU buyer)
    if cardmarket_avg_sell_eur and cardmarket_avg_sell_eur > 0:
        sources.append(SourcePrice(
            "Cardmarket (media vendita)", cardmarket_avg_sell_eur, 5.0,
            "Prezzo medio di vendita effettivo EU"
        ))

    if cardmarket_trend_eur and cardmarket_trend_eur > 0:
        sources.append(SourcePrice(
            "Cardmarket (trend)", cardmarket_trend_eur, 4.0,
            "Trend price EU"
        ))

    if cardmarket_low_eur and cardmarket_low_eur > 0:
        sources.append(SourcePrice(
            "Cardmarket (minimo)", cardmarket_low_eur, 1.0,
            "Prezzo minimo disponibile"
        ))

    # USA sources (converted to EUR)
    if pricecharting_usd and pricecharting_usd > 0:
        pc_eur = pricecharting_usd * usd_to_eur_rate
        sources.append(SourcePrice(
            "PriceCharting", pc_eur, 3.0,
            f"${pricecharting_usd:.2f} (mercato USA)"
        ))

    if tcgplayer_market_usd and tcgplayer_market_usd > 0:
        tcg_eur = tcgplayer_market_usd * usd_to_eur_rate
        sources.append(SourcePrice(
            "TCGPlayer (market)", tcg_eur, 3.0,
            f"${tcgplayer_market_usd:.2f} (mercato USA)"
        ))

    # RetroGamingShop (EU retail — real store prices)
    if retrogamingshop_avg_eur and retrogamingshop_avg_eur > 0:
        sources.append(SourcePrice(
            "RetroGamingShop.it", retrogamingshop_avg_eur, 3.5,
            "Prezzo negozio retrogaming IT"
        ))

    # Vinted (lower weight — asking prices)
    if vinted_avg_eur and vinted_avg_eur > 0:
        sources.append(SourcePrice(
            "Vinted (media)", vinted_avg_eur, 2.0,
            "Media inserzioni attive (non vendite)"
        ))

    if not sources:
        return AggregatedPrice(fair_value_eur=0, sources=[], confidence="low")

    # Weighted average
    total_weight = sum(s.weight for s in sources)
    fair_value = sum(s.price_eur * s.weight for s in sources) / total_weight

    # Confidence based on number and quality of sources
    eu_sources = sum(1 for s in sources if "Cardmarket" in s.source)
    if eu_sources >= 2 and len(sources) >= 3:
        confidence = "high"
    elif len(sources) >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return AggregatedPrice(
        fair_value_eur=round(fair_value, 2),
        sources=sorted(sources, key=lambda s: s.weight, reverse=True),
        confidence=confidence,
    )


def format_aggregated_prices(agg: AggregatedPrice) -> str:
    """Format aggregated price for display."""
    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
    lines = [
        f"💎 *Valore di mercato stimato: €{agg.fair_value_eur:.2f}*",
        f"{conf_emoji.get(agg.confidence, '')} Confidenza: {agg.confidence}\n",
        "*Fonti:*",
    ]

    for s in agg.sources:
        weight_bar = "█" * int(s.weight) + "░" * (5 - int(s.weight))
        lines.append(f"  {weight_bar} €{s.price_eur:.2f} — {s.source}")
        if s.note:
            lines.append(f"        _{s.note}_")

    return "\n".join(lines)
