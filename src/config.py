from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    database_url: str = "sqlite+aiosqlite:///./nerd_nostalgia.db"
    pricecharting_api_token: str = ""

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
