import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DomainState:
    last_request: float = 0.0
    consecutive_errors: int = 0
    backoff_until: float = 0.0
    request_count: int = 0


class SmartRateLimiter:
    """
    Per-domain rate limiter with exponential backoff on errors.
    Tracks request counts and automatically backs off when receiving 429/403.
    """

    def __init__(self):
        self._domains: dict[str, DomainState] = defaultdict(DomainState)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Default delays per domain (seconds between requests)
        self._delays: dict[str, float] = {
            "pricecharting.com": 2.0,
            "vinted.it": 3.0,
            "vinted.fr": 3.0,
            "vinted.de": 3.0,
            "vinted.es": 3.0,
            "subito.it": 2.5,
            "es.wallapop.com": 2.5,
            "old.reddit.com": 2.0,
            "cardmarket.com": 3.0,
            "open.er-api.com": 0.5,
        }
        self._default_delay = 2.0

    def _get_domain(self, url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc or "unknown"

    def _get_delay(self, domain: str) -> float:
        for key, delay in self._delays.items():
            if key in domain:
                return delay
        return self._default_delay

    async def wait(self, url: str):
        """Wait before making a request to respect rate limits."""
        domain = self._get_domain(url)
        async with self._locks[domain]:
            state = self._domains[domain]

            now = time.time()

            # Check if we're in backoff
            if now < state.backoff_until:
                wait_time = state.backoff_until - now
                logger.info(f"Rate limiter: {domain} in backoff, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)

            # Normal delay between requests
            delay = self._get_delay(domain)
            elapsed = now - state.last_request
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)

            state.last_request = time.time()
            state.request_count += 1

    def report_success(self, url: str):
        """Report a successful request - resets error counter."""
        domain = self._get_domain(url)
        state = self._domains[domain]
        state.consecutive_errors = 0

    def report_error(self, url: str, status_code: int = 0):
        """Report a failed request - increases backoff."""
        domain = self._get_domain(url)
        state = self._domains[domain]
        state.consecutive_errors += 1

        if status_code in (429, 403):
            # Exponential backoff: 30s, 60s, 120s, 240s, max 600s
            backoff = min(600, 30 * (2 ** (state.consecutive_errors - 1)))
            state.backoff_until = time.time() + backoff
            logger.warning(
                f"Rate limiter: {domain} got {status_code}, "
                f"backoff {backoff}s (error #{state.consecutive_errors})"
            )
        elif state.consecutive_errors >= 3:
            # Generic errors: shorter backoff after 3 consecutive
            backoff = min(120, 15 * state.consecutive_errors)
            state.backoff_until = time.time() + backoff
            logger.warning(f"Rate limiter: {domain} {state.consecutive_errors} errors, backoff {backoff}s")

    def get_stats(self) -> dict[str, dict]:
        """Get stats for all domains."""
        stats = {}
        for domain, state in self._domains.items():
            stats[domain] = {
                "requests": state.request_count,
                "errors": state.consecutive_errors,
                "in_backoff": time.time() < state.backoff_until,
            }
        return stats


# Global singleton
rate_limiter = SmartRateLimiter()
