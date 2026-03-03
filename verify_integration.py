
import asyncio
from main import TradingBot
from order_book import OrderBookBuilder

def test_integration():
    print("[Integration Test] Importing TradingBot...")
    try:
        # Create bot instance (testnet=True to avoid real connection)
        bot = TradingBot(testnet=True)
        print("[Integration Test] TradingBot initialized successfully.")
        
        # Verify OrderBookBuilder integration
        if hasattr(bot, 'order_book'):
            print("[Integration Test] PASS: bot.order_book attribute exists.")
            
            if isinstance(bot.order_book, OrderBookBuilder):
                print("[Integration Test] PASS: bot.order_book is instance of OrderBookBuilder.")
            else:
                print(f"[Integration Test] FAIL: bot.order_book is {type(bot.order_book)}")
        else:
            print("[Integration Test] FAIL: bot.order_book attribute MISSING.")
            
    except Exception as e:
        print(f"[Integration Test] FAIL: Exception during init: {e}")

if __name__ == "__main__":
    test_integration()
