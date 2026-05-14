"""
Launcher: runs both the Telegram bot and the web dashboard.
Usage: python run.py
  - Bot Telegram: attivo
  - Web dashboard: http://localhost:8000
"""
import asyncio
import logging
import sys
import threading
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def run_web():
    """Run web dashboard in a thread."""
    uvicorn.run(
        "src.web.app:app",
        host="0.0.0.0",
        port=9000,
        log_level="warning",
    )


def run_bot():
    """Run Telegram bot (blocking)."""
    from src.bot.main import create_bot
    bot = create_bot()
    bot.run_polling()


if __name__ == "__main__":
    print("🎴 Nerd Nostalgia Analytics")
    print("   Bot Telegram: attivo")
    print("   Web dashboard: http://localhost:9000")
    print("   Premi Ctrl+C per fermare\n")

    # Start web in background thread
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    # Run bot in main thread (blocking)
    run_bot()
