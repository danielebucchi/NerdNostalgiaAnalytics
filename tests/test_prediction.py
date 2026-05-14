import pandas as pd
import pytest
from datetime import datetime, timedelta

from src.analysis.prediction import predict_prices, _predict_with_linreg


def _make_prices(values: list[float], freq_days: int = 30) -> pd.DataFrame:
    base = datetime(2024, 1, 1)
    return pd.DataFrame({
        "date": [base + timedelta(days=i * freq_days) for i in range(len(values))],
        "price": values,
    })


class TestPrediction:
    def test_insufficient_data(self):
        df = _make_prices([10, 20, 30])
        assert predict_prices(df) is None

    def test_linreg_uptrend(self):
        prices = [100 + i * 10 for i in range(20)]
        df = _make_prices(prices)
        pred = _predict_with_linreg(df, prices[-1])
        assert pred is not None
        assert pred.pred_30d > pred.current_price
        assert pred.pred_90d > pred.pred_30d
        assert pred.trend in ("bullish", "sideways")  # May be sideways if change % is small

    def test_linreg_downtrend(self):
        prices = [300 - i * 10 for i in range(20)]
        df = _make_prices(prices)
        pred = _predict_with_linreg(df, prices[-1])
        assert pred is not None
        assert pred.pred_30d < pred.current_price
        assert pred.trend == "bearish"

    def test_linreg_flat(self):
        prices = [100] * 20
        df = _make_prices(prices)
        pred = _predict_with_linreg(df, 100)
        assert pred is not None
        assert abs(pred.pred_30d - 100) < 5
        assert pred.trend == "sideways"

    def test_ranges_make_sense(self):
        prices = [100 + i * 5 for i in range(20)]
        df = _make_prices(prices)
        pred = predict_prices(df)
        assert pred is not None
        assert pred.pred_30d_lower <= pred.pred_30d <= pred.pred_30d_upper
        assert pred.pred_60d_lower <= pred.pred_60d <= pred.pred_60d_upper
        assert pred.pred_90d_lower <= pred.pred_90d <= pred.pred_90d_upper

    def test_prices_never_negative(self):
        prices = [50 - i * 3 for i in range(20)]  # Goes to negative
        df = _make_prices(prices)
        pred = predict_prices(df)
        assert pred is not None
        assert pred.pred_30d >= 0
        assert pred.pred_90d >= 0
