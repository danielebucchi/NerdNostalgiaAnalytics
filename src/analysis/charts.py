import io
import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import ta

logger = logging.getLogger(__name__)

COLORS = {
    "bg": "#1a1a2e",
    "panel": "#16213e",
    "text": "#e0e0e0",
    "grid": "#2a2a4a",
    "price": "#00d4ff",
    "sma_short": "#ff6b6b",
    "sma_mid": "#ffd93d",
    "sma_long": "#6bcb77",
    "bb_fill": "#1e3a5f",
    "bb_line": "#4a90d9",
    "macd": "#00d4ff",
    "macd_signal": "#ff6b6b",
    "macd_hist_pos": "#00c853",
    "macd_hist_neg": "#ff1744",
    "rsi": "#ff9800",
    "rsi_over": "#ff1744",
    "rsi_under": "#00c853",
    "buy": "#00c853",
    "sell": "#ff1744",
}


def _detect_frequency_and_periods(df: pd.DataFrame) -> dict:
    if len(df) < 3:
        return {"sma_short": 3, "sma_mid": 6, "sma_long": 12, "rsi": 6, "bb": 6,
                "macd_fast": 3, "macd_slow": 6, "macd_sign": 3}
    gaps = df["date"].diff().dropna().dt.days
    median_gap = gaps.median()
    if median_gap <= 2:
        return {"sma_short": 7, "sma_mid": 30, "sma_long": 90, "rsi": 14, "bb": 20,
                "macd_fast": 12, "macd_slow": 26, "macd_sign": 9}
    if median_gap <= 10:
        return {"sma_short": 4, "sma_mid": 12, "sma_long": 26, "rsi": 10, "bb": 10,
                "macd_fast": 6, "macd_slow": 12, "macd_sign": 4}
    return {"sma_short": 3, "sma_mid": 6, "sma_long": 12, "rsi": 6, "bb": 6,
            "macd_fast": 3, "macd_slow": 6, "macd_sign": 3}


def generate_chart(prices_df: pd.DataFrame, product_name: str) -> bytes:
    df = prices_df.copy()
    df = df.sort_values("date").reset_index(drop=True)
    df["price"] = df["price"].astype(float)
    df["date"] = pd.to_datetime(df["date"])

    p = _detect_frequency_and_periods(df)

    n_panels = 1
    show_rsi = len(df) >= p["rsi"]
    show_macd = len(df) >= p["macd_slow"]
    if show_rsi:
        n_panels += 1
    if show_macd:
        n_panels += 1

    height_ratios = [3] + [1] * (n_panels - 1)
    fig, axes = plt.subplots(n_panels, 1, figsize=(12, 4 + 2 * n_panels),
                             height_ratios=height_ratios, sharex=True)
    if n_panels == 1:
        axes = [axes]

    fig.patch.set_facecolor(COLORS["bg"])
    for ax in axes:
        ax.set_facecolor(COLORS["panel"])
        ax.tick_params(colors=COLORS["text"])
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color(COLORS["grid"])
        ax.spines["left"].set_color(COLORS["grid"])
        ax.grid(True, alpha=0.2, color=COLORS["grid"])

    panel_idx = 0

    # --- PRICE ---
    ax_price = axes[panel_idx]
    ax_price.plot(df["date"], df["price"], color=COLORS["price"], linewidth=1.5, label="Prezzo")
    ax_price.fill_between(df["date"], df["price"], alpha=0.1, color=COLORS["price"])

    if len(df) >= p["sma_short"]:
        sma = df["price"].rolling(p["sma_short"]).mean()
        ax_price.plot(df["date"], sma, color=COLORS["sma_short"], linewidth=1, alpha=0.8,
                      label=f"SMA{p['sma_short']}")
    if len(df) >= p["sma_mid"]:
        sma = df["price"].rolling(p["sma_mid"]).mean()
        ax_price.plot(df["date"], sma, color=COLORS["sma_mid"], linewidth=1, alpha=0.8,
                      label=f"SMA{p['sma_mid']}")
    if len(df) >= p["sma_long"]:
        sma = df["price"].rolling(p["sma_long"]).mean()
        ax_price.plot(df["date"], sma, color=COLORS["sma_long"], linewidth=1, alpha=0.8,
                      label=f"SMA{p['sma_long']}")

    if len(df) >= p["bb"]:
        bb = ta.volatility.BollingerBands(df["price"], window=p["bb"], window_dev=2)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        ax_price.plot(df["date"], bb_upper, color=COLORS["bb_line"], linewidth=0.5, linestyle="--", alpha=0.5)
        ax_price.plot(df["date"], bb_lower, color=COLORS["bb_line"], linewidth=0.5, linestyle="--", alpha=0.5)
        ax_price.fill_between(df["date"], bb_upper, bb_lower, alpha=0.08, color=COLORS["bb_fill"])

    ax_price.set_title(product_name, fontsize=14, fontweight="bold", color=COLORS["text"], pad=10)
    ax_price.set_ylabel("Prezzo ($)", color=COLORS["text"])
    ax_price.legend(loc="upper left", fontsize=8, facecolor=COLORS["panel"], edgecolor=COLORS["grid"],
                    labelcolor=COLORS["text"])

    # --- RSI ---
    if show_rsi:
        panel_idx += 1
        ax_rsi = axes[panel_idx]
        rsi = ta.momentum.RSIIndicator(df["price"], window=p["rsi"]).rsi()
        ax_rsi.plot(df["date"], rsi, color=COLORS["rsi"], linewidth=1)
        ax_rsi.axhline(y=65, color=COLORS["rsi_over"], linestyle="--", alpha=0.5, linewidth=0.8)
        ax_rsi.axhline(y=35, color=COLORS["rsi_under"], linestyle="--", alpha=0.5, linewidth=0.8)
        ax_rsi.fill_between(df["date"], rsi, 65, where=(rsi >= 65), alpha=0.2, color=COLORS["rsi_over"])
        ax_rsi.fill_between(df["date"], rsi, 35, where=(rsi <= 35), alpha=0.2, color=COLORS["rsi_under"])
        ax_rsi.set_ylabel(f"RSI({p['rsi']})", color=COLORS["text"])
        ax_rsi.set_ylim(0, 100)

    # --- MACD ---
    if show_macd:
        panel_idx += 1
        ax_macd = axes[panel_idx]
        macd_ind = ta.trend.MACD(df["price"], window_slow=p["macd_slow"],
                                  window_fast=p["macd_fast"], window_sign=p["macd_sign"])
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()
        histogram = macd_ind.macd_diff()

        ax_macd.plot(df["date"], macd_line, color=COLORS["macd"], linewidth=1, label="MACD")
        ax_macd.plot(df["date"], signal_line, color=COLORS["macd_signal"], linewidth=1, label="Signal")
        colors = [COLORS["macd_hist_pos"] if v >= 0 else COLORS["macd_hist_neg"] for v in histogram]
        ax_macd.bar(df["date"], histogram, color=colors, alpha=0.5, width=1)
        ax_macd.axhline(y=0, color=COLORS["grid"], linewidth=0.5)
        ax_macd.set_ylabel("MACD", color=COLORS["text"])
        ax_macd.legend(loc="upper left", fontsize=8, facecolor=COLORS["panel"], edgecolor=COLORS["grid"],
                       labelcolor=COLORS["text"])

    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, len(df) // 200)))
    plt.xticks(rotation=45, ha="right")
    axes[-1].set_xlabel("Data", color=COLORS["text"])

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=COLORS["bg"])
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_portfolio_chart(portfolio_data: list[dict]) -> bytes:
    """
    Generate portfolio value over time chart.
    portfolio_data: [{"date": datetime, "value": float, "invested": float}, ...]
    """
    df = pd.DataFrame(portfolio_data)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(COLORS["bg"])
    ax.set_facecolor(COLORS["panel"])
    ax.tick_params(colors=COLORS["text"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(COLORS["grid"])
    ax.spines["left"].set_color(COLORS["grid"])
    ax.grid(True, alpha=0.2, color=COLORS["grid"])

    ax.plot(df["date"], df["value"], color=COLORS["buy"], linewidth=2, label="Valore attuale")
    ax.plot(df["date"], df["invested"], color=COLORS["sma_mid"], linewidth=1.5, linestyle="--",
            label="Investito")
    ax.fill_between(df["date"], df["invested"], df["value"],
                    where=(df["value"] >= df["invested"]), alpha=0.15, color=COLORS["buy"])
    ax.fill_between(df["date"], df["invested"], df["value"],
                    where=(df["value"] < df["invested"]), alpha=0.15, color=COLORS["sell"])

    ax.set_title("Portfolio P&L", fontsize=14, fontweight="bold", color=COLORS["text"])
    ax.set_ylabel("Valore ($)", color=COLORS["text"])
    ax.legend(facecolor=COLORS["panel"], edgecolor=COLORS["grid"], labelcolor=COLORS["text"])

    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=COLORS["bg"])
    plt.close(fig)
    buf.seek(0)
    return buf.read()
