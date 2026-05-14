import pandas as pd
import pytest
from datetime import datetime, timedelta

from src.analysis.indicators import (
    analyze, Signal, _detect_data_frequency, _get_periods,
    _detect_spike, _check_seasonality, _score_to_signal,
)


def _make_prices(values: list[float], freq_days: int = 30) -> pd.DataFrame:
    """Helper to create a price DataFrame."""
    base = datetime(2024, 1, 1)
    return pd.DataFrame({
        "date": [base + timedelta(days=i * freq_days) for i in range(len(values))],
        "price": values,
    })


class TestDataFrequency:
    def test_daily(self):
        df = _make_prices([10] * 30, freq_days=1)
        assert _detect_data_frequency(df) == "daily"

    def test_weekly(self):
        df = _make_prices([10] * 20, freq_days=7)
        assert _detect_data_frequency(df) == "weekly"

    def test_monthly(self):
        df = _make_prices([10] * 20, freq_days=30)
        assert _detect_data_frequency(df) == "monthly"

    def test_few_points(self):
        df = _make_prices([10, 20])
        assert _detect_data_frequency(df) == "monthly"


class TestPeriods:
    def test_daily_periods(self):
        p = _get_periods("daily")
        assert p["rsi"] == 14
        assert p["sma_short"] == 7

    def test_monthly_periods(self):
        p = _get_periods("monthly")
        assert p["rsi"] == 6
        assert p["sma_short"] == 3


class TestSpikeDetection:
    def test_no_spike_stable(self):
        # Realistic gradual growth with some volatility
        import random
        random.seed(99)
        prices = [100 + i * 3 + random.uniform(-5, 5) for i in range(30)]
        df = _make_prices(prices)
        is_spike, z = _detect_spike(df, periods=3)
        assert not is_spike

    def test_spike_detected_concept(self):
        # Verify spike detection triggers on extreme values
        # With perfectly flat then extreme jump, std is 0 → division issue
        # Use slight noise + big jump
        import random
        random.seed(42)
        prices = [100 + random.uniform(-1, 1) for _ in range(27)] + [100, 300, 800]
        df = _make_prices(prices)
        is_spike, z = _detect_spike(df, periods=3)
        assert z > 2.0  # Should be very high


class TestSeasonality:
    def test_christmas(self):
        note = _check_seasonality(datetime(2024, 12, 15))
        assert "natalizio" in note.lower()

    def test_post_holiday(self):
        note = _check_seasonality(datetime(2024, 1, 20))
        assert "post-feste" in note.lower()

    def test_summer(self):
        note = _check_seasonality(datetime(2024, 7, 10))
        assert "estivo" in note.lower()

    def test_no_note(self):
        note = _check_seasonality(datetime(2024, 4, 15))
        assert note is None


class TestScoreToSignal:
    def test_strong_buy(self):
        assert _score_to_signal(50) == Signal.STRONG_BUY

    def test_buy(self):
        assert _score_to_signal(20) == Signal.BUY

    def test_hold(self):
        assert _score_to_signal(0) == Signal.HOLD

    def test_sell(self):
        assert _score_to_signal(-20) == Signal.SELL

    def test_strong_sell(self):
        assert _score_to_signal(-50) == Signal.STRONG_SELL


class TestAnalyze:
    def test_insufficient_data(self):
        df = _make_prices([10, 20, 30])
        result = analyze(df, min_points=6)
        assert result is None

    def test_basic_analysis(self):
        # Steady uptrend
        prices = [100 + i * 5 for i in range(20)]
        df = _make_prices(prices)
        result = analyze(df)
        assert result is not None
        assert result.current_price == prices[-1]
        assert result.score > 0  # Uptrend should be positive
        assert result.sma_3 is not None
        assert result.rsi is not None

    def test_downtrend(self):
        prices = [200 - i * 5 for i in range(20)]
        df = _make_prices(prices)
        result = analyze(df)
        assert result is not None
        assert result.score < 0

    def test_spike_dampens_score(self):
        import random
        random.seed(42)
        prices = [100 + random.uniform(-1, 1) for _ in range(27)] + [100, 300, 800]
        df = _make_prices(prices)
        result = analyze(df)
        assert result is not None
        # With noise + big jump, spike should be detected
        assert result.is_spike

    def test_constant_prices(self):
        # Perfectly flat prices produce edge-case RSI/Bollinger values
        # This is expected — the analysis still returns a result
        prices = [100] * 20
        df = _make_prices(prices)
        result = analyze(df)
        assert result is not None
        assert result.current_price == 100
