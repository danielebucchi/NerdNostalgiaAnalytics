from src.utils.price_aggregator import aggregate_prices, SourcePrice


class TestAggregation:
    def test_single_source(self):
        agg = aggregate_prices(pricecharting_usd=100, usd_to_eur_rate=0.90)
        assert agg.fair_value_eur == 90.0
        assert len(agg.sources) == 1
        assert agg.confidence == "low"

    def test_multiple_sources(self):
        agg = aggregate_prices(
            pricecharting_usd=100,
            vinted_avg_eur=85,
            usd_to_eur_rate=0.90,
        )
        assert len(agg.sources) == 2
        assert agg.confidence == "medium"
        # Weighted: PC=90*3 + Vinted=85*2 = 270+170=440, /5=88
        assert 85 < agg.fair_value_eur < 91

    def test_cardmarket_highest_weight(self):
        agg = aggregate_prices(
            pricecharting_usd=100,
            cardmarket_avg_sell_eur=50,
            usd_to_eur_rate=0.90,
        )
        # Cardmarket weight 5 vs PriceCharting weight 3
        # (50*5 + 90*3) / 8 = 520/8 = 65
        assert agg.fair_value_eur < 70  # Cardmarket pulls it down

    def test_ebay_sold_weight_scales(self):
        # Few sales: weight = 3.0 + 5*0.1 = 3.5
        agg1 = aggregate_prices(ebay_sold_avg_eur=100, ebay_sold_count=5)
        # Many sales: weight = min(5.0, 3.0 + 20*0.1) = 5.0
        agg2 = aggregate_prices(ebay_sold_avg_eur=100, ebay_sold_count=20)
        assert agg2.sources[0].weight > agg1.sources[0].weight

    def test_high_confidence_with_eu_sources(self):
        agg = aggregate_prices(
            cardmarket_avg_sell_eur=50,
            cardmarket_trend_eur=55,
            pricecharting_usd=60,
            usd_to_eur_rate=0.90,
        )
        assert agg.confidence == "high"

    def test_no_sources(self):
        agg = aggregate_prices()
        assert agg.fair_value_eur == 0
        assert agg.confidence == "low"
        assert len(agg.sources) == 0

    def test_zero_prices_ignored(self):
        agg = aggregate_prices(pricecharting_usd=0, vinted_avg_eur=0)
        assert agg.fair_value_eur == 0
        assert len(agg.sources) == 0

    def test_retrogaming_shops(self):
        agg = aggregate_prices(
            retrogamingshop_avg_eur=45,
            twentysixbits_avg_eur=50,
            pricecharting_usd=55,
            usd_to_eur_rate=0.90,
        )
        assert len(agg.sources) == 3
        assert 45 < agg.fair_value_eur < 55

    def test_all_sources(self):
        agg = aggregate_prices(
            pricecharting_usd=100,
            cardmarket_trend_eur=85,
            cardmarket_avg_sell_eur=80,
            cardmarket_low_eur=60,
            tcgplayer_market_usd=95,
            vinted_avg_eur=75,
            ebay_sold_avg_eur=82,
            ebay_sold_count=10,
            retrogamingshop_avg_eur=90,
            twentysixbits_avg_eur=88,
            usd_to_eur_rate=0.90,
        )
        assert len(agg.sources) == 9
        assert agg.confidence == "high"
        assert 70 < agg.fair_value_eur < 95
