from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    database_url: str = "sqlite+aiosqlite:///./nerd_nostalgia.db"
    pricecharting_api_token: str = ""

    # eBay API credentials (get from https://developer.ebay.com/my/keys)
    ebay_app_id: str = ""
    ebay_cert_id: str = ""

    # CardTrader API (seller JWT token)
    cardtrader_token: str = ""

    # Groq API key — optional; enables the LLM fallback parser for noisy queries.
    # Genuine free tier (no billing needed, EEA-friendly): 30 RPM / 6000 RPD on
    # llama-3.3-70b-versatile. Get one at https://console.groq.com/keys
    groq_api_key: str = ""

    # Comma-separated Telegram user IDs allowed to interact with the bot. Empty
    # means "open to anyone who messages me". Use this when you want to restrict
    # to friends or yourself.
    whitelist_telegram_ids: str = ""

    # Comma-separated Telegram user IDs with admin privileges (e.g. /backup,
    # broadcast, user management). Always implicitly includes the first user
    # who ever registered when this list is empty (bootstrap-the-owner pattern).
    admin_telegram_ids: str = ""

    # Scraping settings
    scrape_delay_seconds: float = 2.0
    max_concurrent_requests: int = 3

    # Scheduler settings
    price_update_interval_hours: int = 6
    alert_check_interval_minutes: int = 30

    # Analysis settings
    min_data_points_for_analysis: int = 14

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
