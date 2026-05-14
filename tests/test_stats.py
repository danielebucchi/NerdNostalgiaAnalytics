from src.bot.handlers.stats import COMMISSIONS


class TestCommissions:
    def test_all_platforms_exist(self):
        for platform in ["vinted", "ebay", "cardmarket", "subito", "wallapop"]:
            assert platform in COMMISSIONS

    def test_commission_format(self):
        for platform, comm in COMMISSIONS.items():
            assert "rate" in comm
            assert "fixed" in comm
            assert "name" in comm
            assert 0 <= comm["rate"] <= 1
            assert comm["fixed"] >= 0

    def test_target_price_calculation(self):
        """Verify: if I buy at X and sell at target_price, I get the desired margin."""
        buy_price = 50.0
        target_margin = 0.30  # 30%

        for platform, comm in COMMISSIONS.items():
            rate = comm["rate"]
            fixed = comm["fixed"]

            # Calculate sell price for target margin
            target_net = buy_price * (1 + target_margin)
            sell_price = (target_net + fixed) / (1 - rate)

            # Verify
            actual_net = sell_price * (1 - rate) - fixed
            actual_margin = (actual_net - buy_price) / buy_price

            assert abs(actual_margin - target_margin) < 0.01, (
                f"{platform}: expected {target_margin}, got {actual_margin}"
            )

    def test_subito_wallapop_free(self):
        assert COMMISSIONS["subito"]["rate"] == 0
        assert COMMISSIONS["subito"]["fixed"] == 0
        assert COMMISSIONS["wallapop"]["rate"] == 0
