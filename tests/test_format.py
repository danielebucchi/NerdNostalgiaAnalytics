"""Test formatting functions across modules."""
from src.analysis.indicators import format_analysis, AnalysisResult, Signal
from src.analysis.prediction import format_prediction, PredictionResult
from src.utils.price_aggregator import format_aggregated_prices, AggregatedPrice, SourcePrice


class TestFormatAnalysis:
    def test_basic_format(self):
        result = AnalysisResult(
            signal=Signal.BUY, score=25, current_price=100.0,
            sma_3=95, sma_6=90, sma_12=85,
            ema_6=92, ema_12=88,
            rsi=35.5, macd=1.5, macd_signal=1.0, macd_histogram=0.5,
            bb_upper=110, bb_middle=100, bb_lower=90,
            price_change_short=5.0, price_change_mid=10.0, price_change_long=20.0,
            volume_trend=None, is_spike=False, seasonality_note=None,
            details=["Test detail"],
        )
        text = format_analysis(result)
        assert "BUY" in text
        assert "$100.00" in text
        assert "RSI" in text
        assert "Test detail" in text

    def test_spike_warning(self):
        result = AnalysisResult(
            signal=Signal.HOLD, score=0, current_price=100.0,
            sma_3=None, sma_6=None, sma_12=None,
            ema_6=None, ema_12=None,
            rsi=None, macd=None, macd_signal=None, macd_histogram=None,
            bb_upper=None, bb_middle=None, bb_lower=None,
            price_change_short=None, price_change_mid=None, price_change_long=None,
            volume_trend=None, is_spike=True, seasonality_note=None,
            details=[],
        )
        text = format_analysis(result)
        assert "spike" in text.lower()


class TestFormatPrediction:
    def test_basic_format(self):
        pred = PredictionResult(
            current_price=100, trend="bullish",
            pred_30d=110, pred_60d=120, pred_90d=130,
            pred_30d_lower=100, pred_30d_upper=120,
            pred_60d_lower=105, pred_60d_upper=135,
            pred_90d_lower=110, pred_90d_upper=150,
        )
        text = format_prediction(pred)
        assert "BULLISH" in text
        assert "$100.00" in text
        assert "$110.00" in text
        assert "30gg" in text
        assert "%" in text


class TestFormatAggregated:
    def test_basic_format(self):
        agg = AggregatedPrice(
            fair_value_eur=85.0,
            sources=[
                SourcePrice("PriceCharting", 90.0, 3.0, "USD converted"),
                SourcePrice("Vinted", 80.0, 2.0, "asking prices"),
            ],
            confidence="medium",
        )
        text = format_aggregated_prices(agg)
        assert "€85.00" in text
        assert "medium" in text
        assert "PriceCharting" in text
        assert "Vinted" in text

    def test_high_confidence(self):
        agg = AggregatedPrice(fair_value_eur=100, sources=[], confidence="high")
        text = format_aggregated_prices(agg)
        assert "high" in text
