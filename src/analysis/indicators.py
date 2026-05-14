import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


class Signal(str, Enum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG SELL"


@dataclass
class AnalysisResult:
    signal: Signal
    score: float  # -100 (strong sell) to +100 (strong buy)
    current_price: float
    sma_3: float | None
    sma_6: float | None
    sma_12: float | None
    ema_6: float | None
    ema_12: float | None
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    price_change_short: float | None  # percentage (3 periods)
    price_change_mid: float | None  # percentage (6 periods)
    price_change_long: float | None  # percentage (12 periods)
    volume_trend: str | None
    is_spike: bool
    seasonality_note: str | None
    details: list[str] = field(default_factory=list)


def _detect_data_frequency(df: pd.DataFrame) -> str:
    """Detect if data is daily, weekly, or monthly based on median gap between dates."""
    if len(df) < 3:
        return "monthly"
    gaps = df["date"].diff().dropna().dt.days
    median_gap = gaps.median()
    if median_gap <= 2:
        return "daily"
    if median_gap <= 10:
        return "weekly"
    return "monthly"


def _get_periods(frequency: str) -> dict:
    """Return indicator periods adapted to data frequency."""
    if frequency == "daily":
        return {
            "sma_short": 7, "sma_mid": 30, "sma_long": 90,
            "ema_fast": 12, "ema_slow": 26,
            "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_sign": 9,
            "bb": 20,
            "change_short": 7, "change_mid": 30, "change_long": 90,
        }
    if frequency == "weekly":
        return {
            "sma_short": 4, "sma_mid": 12, "sma_long": 26,
            "ema_fast": 6, "ema_slow": 12,
            "rsi": 10, "macd_fast": 6, "macd_slow": 12, "macd_sign": 4,
            "bb": 10,
            "change_short": 4, "change_mid": 12, "change_long": 26,
        }
    # monthly (collectibles typical)
    return {
        "sma_short": 3, "sma_mid": 6, "sma_long": 12,
        "ema_fast": 3, "ema_slow": 6,
        "rsi": 6, "macd_fast": 3, "macd_slow": 6, "macd_sign": 3,
        "bb": 6,
        "change_short": 3, "change_mid": 6, "change_long": 12,
    }


def _detect_spike(df: pd.DataFrame, periods: int = 3) -> tuple[bool, float]:
    """
    Detect if recent price movement is an anomalous spike (hype-driven, not a real trend).
    Returns (is_spike, spike_magnitude_as_z_score).
    """
    if len(df) < 10:
        return False, 0.0

    returns = df["price"].pct_change().dropna()
    if len(returns) < 5:
        return False, 0.0

    # Recent return vs historical volatility
    recent_return = df["price"].iloc[-1] / df["price"].iloc[-min(periods, len(df))] - 1
    historical_std = returns[:-periods].std() if len(returns) > periods else returns.std()

    if historical_std == 0:
        return False, 0.0

    z_score = abs(recent_return) / historical_std

    # A z-score > 2.5 suggests anomalous movement
    return z_score > 2.5, z_score


def _check_seasonality(current_date: datetime | None = None) -> str | None:
    """Check if current period has known seasonal patterns for collectibles."""
    if current_date is None:
        current_date = datetime.now()

    month = current_date.month

    # Known seasonal patterns in collectibles market
    if month in (11, 12):
        return "Periodo natalizio: domanda alta, prezzi tendenzialmente in salita"
    if month in (1, 2):
        return "Post-feste: possibili cali, buon momento per acquisti"
    if month in (8, 9):
        return "Fine estate / back-to-school: nuovi set in uscita, rotazione prezzi"
    if month in (6, 7):
        return "Periodo estivo: mercato piu' lento, possibili opportunita'"
    return None


def analyze(prices_df: pd.DataFrame, min_points: int = 6) -> AnalysisResult | None:
    """
    Analyze price history and return technical indicators + signal.
    Auto-detects data frequency and adapts indicator periods accordingly.
    """
    if prices_df is None or len(prices_df) < min_points:
        return None

    df = prices_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["price"] = df["price"].astype(float)

    frequency = _detect_data_frequency(df)
    p = _get_periods(frequency)
    current_price = df["price"].iloc[-1]
    details = [f"Frequenza dati: {frequency} ({len(df)} punti)"]
    score = 0.0

    # --- Moving Averages ---
    sma_3 = _safe_sma(df, p["sma_short"])
    sma_6 = _safe_sma(df, p["sma_mid"])
    sma_12 = _safe_sma(df, p["sma_long"])
    ema_6 = _safe_ema(df, p["ema_fast"])
    ema_12 = _safe_ema(df, p["ema_slow"])

    # SMA crossover
    if sma_3 is not None and sma_6 is not None:
        if sma_3 > sma_6:
            score += 15
            details.append(f"SMA breve ({sma_3:.2f}) > SMA media ({sma_6:.2f}): trend rialzista")
        else:
            score -= 15
            details.append(f"SMA breve ({sma_3:.2f}) < SMA media ({sma_6:.2f}): trend ribassista")

    # Price vs medium SMA
    if sma_6 is not None:
        if current_price > sma_6:
            score += 10
            details.append(f"Prezzo ({current_price:.2f}) sopra SMA media: positivo")
        else:
            score -= 10
            details.append(f"Prezzo ({current_price:.2f}) sotto SMA media: negativo")

    # Long-term trend
    if sma_12 is not None:
        if current_price > sma_12:
            score += 5
            details.append(f"Sopra SMA lungo periodo ({sma_12:.2f}): trend di fondo positivo")
        else:
            score -= 5
            details.append(f"Sotto SMA lungo periodo ({sma_12:.2f}): trend di fondo negativo")

    # --- RSI ---
    rsi = None
    if len(df) >= p["rsi"]:
        rsi_series = ta.momentum.RSIIndicator(df["price"], window=p["rsi"]).rsi()
        rsi = rsi_series.iloc[-1]

        if rsi < 25:
            score += 25
            details.append(f"RSI ({rsi:.1f}) < 25: fortemente ipervenduto")
        elif rsi < 35:
            score += 15
            details.append(f"RSI ({rsi:.1f}) < 35: ipervenduto")
        elif rsi < 45:
            score += 5
            details.append(f"RSI ({rsi:.1f}) < 45: sottovalutato")
        elif rsi > 75:
            score -= 25
            details.append(f"RSI ({rsi:.1f}) > 75: fortemente ipercomprato")
        elif rsi > 65:
            score -= 15
            details.append(f"RSI ({rsi:.1f}) > 65: ipercomprato")
        elif rsi > 55:
            score -= 5
            details.append(f"RSI ({rsi:.1f}) > 55: sopravvalutato")
        else:
            details.append(f"RSI ({rsi:.1f}): zona neutra")

    # --- MACD ---
    macd_val = macd_signal_val = macd_histogram = None
    if len(df) >= p["macd_slow"]:
        macd_ind = ta.trend.MACD(
            df["price"],
            window_slow=p["macd_slow"], window_fast=p["macd_fast"], window_sign=p["macd_sign"],
        )
        macd_val = macd_ind.macd().iloc[-1]
        macd_signal_val = macd_ind.macd_signal().iloc[-1]
        macd_histogram = macd_ind.macd_diff().iloc[-1]

        macd_prev = macd_ind.macd().iloc[-2]
        signal_prev = macd_ind.macd_signal().iloc[-2]

        if macd_prev < signal_prev and macd_val > macd_signal_val:
            score += 20
            details.append("MACD crossover rialzista: segnale di acquisto")
        elif macd_prev > signal_prev and macd_val < macd_signal_val:
            score -= 20
            details.append("MACD crossover ribassista: segnale di vendita")
        elif macd_histogram > 0:
            score += 5
            details.append(f"MACD istogramma positivo ({macd_histogram:.4f})")
        else:
            score -= 5
            details.append(f"MACD istogramma negativo ({macd_histogram:.4f})")

    # --- Bollinger Bands ---
    bb_upper = bb_middle = bb_lower = None
    if len(df) >= p["bb"]:
        bb = ta.volatility.BollingerBands(df["price"], window=p["bb"], window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_middle = bb.bollinger_mavg().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]

        if current_price <= bb_lower:
            score += 20
            details.append(f"Prezzo sotto Bollinger inferiore ({bb_lower:.2f}): ipervenduto")
        elif current_price >= bb_upper:
            score -= 20
            details.append(f"Prezzo sopra Bollinger superiore ({bb_upper:.2f}): ipercomprato")
        else:
            bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) * 100
            details.append(f"Bollinger: prezzo al {bb_position:.0f}% della banda")

    # --- Price changes ---
    price_change_short = _pct_change(df, p["change_short"])
    price_change_mid = _pct_change(df, p["change_mid"])
    price_change_long = _pct_change(df, p["change_long"])

    if price_change_short is not None:
        if price_change_short > 20:
            score -= 10
            details.append(f"Variazione breve: +{price_change_short:.1f}% (salita troppo rapida)")
        elif price_change_short < -20:
            score += 10
            details.append(f"Variazione breve: {price_change_short:.1f}% (calo forte, possibile rimbalzo)")

    if price_change_mid is not None:
        details.append(f"Variazione media: {price_change_mid:+.1f}%")
    if price_change_long is not None:
        details.append(f"Variazione lunga: {price_change_long:+.1f}%")

    # --- Volume ---
    volume_trend = None
    if "volume" in df.columns and df["volume"].notna().sum() >= p["sma_mid"]:
        vol_recent = df["volume"].tail(p["sma_short"]).mean()
        vol_older = df["volume"].tail(p["sma_mid"]).head(p["sma_mid"] - p["sma_short"]).mean()
        if vol_older > 0:
            vol_change = (vol_recent - vol_older) / vol_older
            if vol_change > 0.2:
                volume_trend = "increasing"
                score += 5
                details.append("Volume in aumento: interesse crescente")
            elif vol_change < -0.2:
                volume_trend = "decreasing"
                score -= 5
                details.append("Volume in calo: interesse decrescente")
            else:
                volume_trend = "stable"

    # --- Spike detection ---
    is_spike, z_score = _detect_spike(df, p["sma_short"])
    if is_spike:
        # Dampen the signal - spike means unreliable movement
        dampening = 0.5
        old_score = score
        score *= dampening
        details.append(
            f"⚠ SPIKE rilevato (z={z_score:.1f}): movimento anomalo, "
            f"score ridotto da {old_score:+.0f} a {score:+.0f}"
        )

    # --- Seasonality ---
    last_date = df["date"].iloc[-1]
    seasonality_note = _check_seasonality(last_date if isinstance(last_date, datetime) else None)
    if seasonality_note:
        details.append(f"📅 {seasonality_note}")

    # --- Final signal ---
    score = max(-100, min(100, score))
    signal = _score_to_signal(score)

    return AnalysisResult(
        signal=signal,
        score=score,
        current_price=current_price,
        sma_3=sma_3,
        sma_6=sma_6,
        sma_12=sma_12,
        ema_6=ema_6,
        ema_12=ema_12,
        rsi=rsi,
        macd=macd_val,
        macd_signal=macd_signal_val,
        macd_histogram=macd_histogram,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        price_change_short=price_change_short,
        price_change_mid=price_change_mid,
        price_change_long=price_change_long,
        volume_trend=volume_trend,
        is_spike=is_spike,
        seasonality_note=seasonality_note,
        details=details,
    )


def _safe_sma(df: pd.DataFrame, window: int) -> float | None:
    if len(df) < window:
        return None
    return df["price"].rolling(window=window).mean().iloc[-1]


def _safe_ema(df: pd.DataFrame, window: int) -> float | None:
    if len(df) < window:
        return None
    return df["price"].ewm(span=window, adjust=False).mean().iloc[-1]


def _pct_change(df: pd.DataFrame, periods: int) -> float | None:
    if len(df) < periods:
        return None
    old_price = df["price"].iloc[-periods]
    new_price = df["price"].iloc[-1]
    if old_price == 0:
        return None
    return ((new_price - old_price) / old_price) * 100


def _score_to_signal(score: float) -> Signal:
    if score >= 40:
        return Signal.STRONG_BUY
    if score >= 15:
        return Signal.BUY
    if score <= -40:
        return Signal.STRONG_SELL
    if score <= -15:
        return Signal.SELL
    return Signal.HOLD


SIGNAL_EMOJI = {
    Signal.STRONG_BUY: "🟢🟢",
    Signal.BUY: "🟢",
    Signal.HOLD: "🟡",
    Signal.SELL: "🔴",
    Signal.STRONG_SELL: "🔴🔴",
}


def format_analysis(result: AnalysisResult) -> str:
    emoji = SIGNAL_EMOJI.get(result.signal, "")
    lines = [
        f"{emoji} Segnale: {result.signal.value} (score: {result.score:+.0f})",
        f"Prezzo attuale: ${result.current_price:.2f}",
    ]

    if result.is_spike:
        lines.append("⚠ ATTENZIONE: spike anomalo rilevato")

    lines.append("")
    lines.append("--- Indicatori ---")

    if result.rsi is not None:
        lines.append(f"RSI: {result.rsi:.1f}")
    if result.macd is not None:
        lines.append(f"MACD: {result.macd:.4f} | Signal: {result.macd_signal:.4f}")
    if result.sma_3 is not None:
        parts = [f"SMA breve: ${result.sma_3:.2f}"]
        if result.sma_6 is not None:
            parts.append(f"media: ${result.sma_6:.2f}")
        if result.sma_12 is not None:
            parts.append(f"lunga: ${result.sma_12:.2f}")
        lines.append(" | ".join(parts))
    if result.bb_upper is not None:
        lines.append(f"Bollinger: [{result.bb_lower:.2f} - {result.bb_middle:.2f} - {result.bb_upper:.2f}]")

    lines.append("")
    lines.append("--- Variazioni ---")
    if result.price_change_short is not None:
        lines.append(f"Breve: {result.price_change_short:+.1f}%")
    if result.price_change_mid is not None:
        lines.append(f"Media: {result.price_change_mid:+.1f}%")
    if result.price_change_long is not None:
        lines.append(f"Lunga: {result.price_change_long:+.1f}%")

    if result.seasonality_note:
        lines.append(f"\n📅 {result.seasonality_note}")

    lines.append("")
    lines.append("--- Dettagli ---")
    for detail in result.details:
        lines.append(f"  - {detail}")

    return "\n".join(lines)
