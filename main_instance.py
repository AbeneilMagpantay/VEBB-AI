"""
VEBB-AI: Single Instance Runner
Used by run_dual.py to run separate bot instances.
"""

import asyncio
import os
import sys
import signal
from dotenv import load_dotenv

# Single 15m Instance
instance = "15m"

# Load base env file
env_file = ".env"
load_dotenv(env_file, override=True)

print(f"[VEBB-AI 15m] Loading config from {env_file}")

from main import TradingBot


async def main():
    """Entry point for single instance."""
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    bot = TradingBot(testnet=testnet)
    
    # Handle Ctrl+C
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
        except NotImplementedError:
            pass
    
    await bot.start()


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  VEBB-AI Instance: {instance.upper()}")
    print(f"{'='*60}\n")
    asyncio.run(main())
