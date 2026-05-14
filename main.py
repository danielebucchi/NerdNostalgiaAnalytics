import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.bot.main import create_bot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Reduce noise from httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

if __name__ == "__main__":
    bot = create_bot()
    print("🎴 Nerd Nostalgia Analytics Bot starting...")
    print("Press Ctrl+C to stop")
    bot.run_polling()
