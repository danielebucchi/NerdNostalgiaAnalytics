import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    current_price: float
    pred_30d: float
    pred_60d: float
    pred_90d: float
    pred_30d_lower: float
    pred_30d_upper: float
    pred_60d_lower: float
    pred_60d_upper: float
    pred_90d_lower: float
    pred_90d_upper: float
    trend: str  # "bullish", "bearish", "sideways"


def predict_prices(prices_df: pd.DataFrame) -> PredictionResult | None:
    """
    Predict future prices using Facebook Prophet.
    Falls back to linear regression if Prophet is not available.
    """
    if prices_df is None or len(prices_df) < 10:
        return None

    df = prices_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    current_price = float(df["price"].iloc[-1])

    try:
        return _predict_with_prophet(df, current_price)
    except ImportError:
        logger.info("Prophet not available, using linear regression fallback")
        return _predict_with_linreg(df, current_price)
    except Exception as e:
        logger.warning(f"Prophet prediction failed: {e}, using linear regression")
        return _predict_with_linreg(df, current_price)


def _predict_with_prophet(df: pd.DataFrame, current_price: float) -> PredictionResult:
    from prophet import Prophet
    import warnings
    warnings.filterwarnings("ignore", module="prophet")
    warnings.filterwarnings("ignore", module="cmdstanpy")

    prophet_df = pd.DataFrame({"ds": df["date"], "y": df["price"].astype(float)})

    m = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.1,
    )
    m.fit(prophet_df)

    future = m.make_future_dataframe(periods=90, freq="D")
    forecast = m.predict(future)

    last_date = df["date"].iloc[-1]
    pred_30 = forecast[forecast["ds"] == last_date + pd.Timedelta(days=30)]
    pred_60 = forecast[forecast["ds"] == last_date + pd.Timedelta(days=60)]
    pred_90 = forecast[forecast["ds"] == last_date + pd.Timedelta(days=90)]

    # If exact dates not found, use closest
    def _get_pred(days):
        target = last_date + pd.Timedelta(days=days)
        idx = (forecast["ds"] - target).abs().idxmin()
        row = forecast.iloc[idx]
        return max(0, row["yhat"]), max(0, row["yhat_lower"]), max(0, row["yhat_upper"])

    p30, p30_lo, p30_hi = _get_pred(30)
    p60, p60_lo, p60_hi = _get_pred(60)
    p90, p90_lo, p90_hi = _get_pred(90)

    trend = _classify_trend(current_price, p30, p90)

    return PredictionResult(
        current_price=current_price,
        pred_30d=p30, pred_60d=p60, pred_90d=p90,
        pred_30d_lower=p30_lo, pred_30d_upper=p30_hi,
        pred_60d_lower=p60_lo, pred_60d_upper=p60_hi,
        pred_90d_lower=p90_lo, pred_90d_upper=p90_hi,
        trend=trend,
    )


def _predict_with_linreg(df: pd.DataFrame, current_price: float) -> PredictionResult:
    """Simple linear regression fallback."""
    import numpy as np

    df = df.copy()
    df["days"] = (df["date"] - df["date"].iloc[0]).dt.days
    x = df["days"].values
    y = df["price"].values.astype(float)

    # Linear regression
    n = len(x)
    mean_x, mean_y = x.mean(), y.mean()
    slope = ((x - mean_x) * (y - mean_y)).sum() / ((x - mean_x) ** 2).sum()
    intercept = mean_y - slope * mean_x

    last_day = x[-1]
    residuals = y - (slope * x + intercept)
    std_err = float(np.std(residuals))

    def _pred(days_ahead):
        val = max(0, slope * (last_day + days_ahead) + intercept)
        return val, max(0, val - 1.96 * std_err), val + 1.96 * std_err

    p30, p30_lo, p30_hi = _pred(30)
    p60, p60_lo, p60_hi = _pred(60)
    p90, p90_lo, p90_hi = _pred(90)

    trend = _classify_trend(current_price, p30, p90)

    return PredictionResult(
        current_price=current_price,
        pred_30d=p30, pred_60d=p60, pred_90d=p90,
        pred_30d_lower=p30_lo, pred_30d_upper=p30_hi,
        pred_60d_lower=p60_lo, pred_60d_upper=p60_hi,
        pred_90d_lower=p90_lo, pred_90d_upper=p90_hi,
        trend=trend,
    )


def _classify_trend(current: float, pred_30: float, pred_90: float) -> str:
    if current == 0:
        return "sideways"
    change_30 = (pred_30 - current) / current * 100
    change_90 = (pred_90 - current) / current * 100
    if change_30 > 5 and change_90 > 10:
        return "bullish"
    if change_30 < -5 and change_90 < -10:
        return "bearish"
    return "sideways"


def format_prediction(pred: PredictionResult) -> str:
    trend_emoji = {"bullish": "📈", "bearish": "📉", "sideways": "➡️"}
    emoji = trend_emoji.get(pred.trend, "")

    def _change(future, current):
        if current == 0:
            return ""
        pct = (future - current) / current * 100
        return f" ({pct:+.1f}%)"

    return (
        f"{emoji} Trend: {pred.trend.upper()}\n\n"
        f"Prezzo attuale: ${pred.current_price:.2f}\n\n"
        f"Previsione 30gg: ${pred.pred_30d:.2f}{_change(pred.pred_30d, pred.current_price)}\n"
        f"  Range: ${pred.pred_30d_lower:.2f} - ${pred.pred_30d_upper:.2f}\n"
        f"Previsione 60gg: ${pred.pred_60d:.2f}{_change(pred.pred_60d, pred.current_price)}\n"
        f"  Range: ${pred.pred_60d_lower:.2f} - ${pred.pred_60d_upper:.2f}\n"
        f"Previsione 90gg: ${pred.pred_90d:.2f}{_change(pred.pred_90d, pred.current_price)}\n"
        f"  Range: ${pred.pred_90d_lower:.2f} - ${pred.pred_90d_upper:.2f}"
    )
