import asyncio
import time
import pytest

from src.utils.rate_limiter import SmartRateLimiter


class TestRateLimiter:
    def test_domain_extraction(self):
        rl = SmartRateLimiter()
        assert "pricecharting.com" in rl._get_domain("https://www.pricecharting.com/game/test")
        assert "vinted.it" in rl._get_domain("https://www.vinted.it/items/123")

    def test_delay_per_domain(self):
        rl = SmartRateLimiter()
        assert rl._get_delay("www.pricecharting.com") == 2.0
        assert rl._get_delay("www.vinted.it") == 3.0
        assert rl._get_delay("unknown.com") == 2.0

    def test_success_resets_errors(self):
        rl = SmartRateLimiter()
        url = "https://www.pricecharting.com/test"
        rl.report_error(url, 429)
        domain = rl._get_domain(url)
        assert rl._domains[domain].consecutive_errors == 1
        rl.report_success(url)
        assert rl._domains[domain].consecutive_errors == 0

    def test_error_increases_backoff(self):
        rl = SmartRateLimiter()
        url = "https://www.vinted.it/test"
        rl.report_error(url, 429)
        domain = rl._get_domain(url)
        assert rl._domains[domain].backoff_until > time.time()
        first_backoff = rl._domains[domain].backoff_until

        rl.report_error(url, 429)
        assert rl._domains[domain].backoff_until > first_backoff  # Exponential

    def test_stats(self):
        rl = SmartRateLimiter()
        url = "https://www.pricecharting.com/test"
        rl.report_success(url)
        rl.report_success(url)
        stats = rl.get_stats()
        domain = rl._get_domain(url)
        assert domain in stats
        assert stats[domain]["errors"] == 0


class TestRateLimiterAsync:
    @pytest.mark.asyncio
    async def test_wait_respects_delay(self):
        rl = SmartRateLimiter()
        url = "https://www.pricecharting.com/test"

        start = time.time()
        await rl.wait(url)
        await rl.wait(url)
        elapsed = time.time() - start

        # Should have waited at least the delay between calls
        assert elapsed >= 1.5  # 2.0s delay minus some tolerance
