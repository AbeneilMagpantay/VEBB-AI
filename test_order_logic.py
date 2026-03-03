
import asyncio
import os
from dotenv import load_dotenv
from exchange_client import ExchangeClient, OrderSide
from position_manager import PositionManager

async def test_order_logic():
    print("=" * 60)
    print("  🧪 TESTING LEVERAGE & ORDER SIZE")
    print("=" * 60)
    
    # 1. Load Config
    load_dotenv(".env.5m")
    
    initial_capital = float(os.getenv("INITIAL_CAPITAL", 100))
    leverage = int(os.getenv("LEVERAGE", 20))
    max_pct = float(os.getenv("MAX_POSITION_PCT", 1.0))
    
    print(f"  💰 Capital: ${initial_capital}")
    print(f"  ⚡ Leverage: {leverage}x")
    print(f"  📊 Allocation: {max_pct*100}%")
    print("-" * 60)

    # 2. Initialize Logic
    pm = PositionManager(
        initial_capital=initial_capital,
        leverage=leverage,
        max_position_pct=max_pct
    )
    
    exchange = ExchangeClient(testnet=True)
    
    # 3. Fetch Price
    print("  📡 Fetching BTC price...")
    try:
        import requests
        response = requests.get("https://testnet.binancefuture.com/fapi/v1/ticker/price?symbol=BTCUSDT")
        price_data = response.json()
        price = float(price_data["price"])
        print(f"  🏷️  BTC Price: ${price:,.2f}")
    except Exception as e:
        print(f"  ❌ Failed to fetch price: {e}")
        return

    # 4. Calculate Position Size
    print("-" * 60)
    print("  🧮 CALCULATING SIZE:")
    qty = pm.get_max_position_size(price)
    
    notional_value = qty * price
    margin_cost = notional_value / leverage
    
    print(f"  ➡️  Quantity: {qty:.3f} BTC")
    print(f"  ➡️  Notional Value: ${notional_value:,.2f}")
    print(f"  ➡️  Margin Cost: ${margin_cost:,.2f} (from your ${initial_capital} balance)")
    
    # 5. Check Testnet Minimum rule
    if notional_value < 100:
        print("\n  ❌ FAIL: Value is < $100 (Binance Testnet Minimum)")
    else:
        print("\n  ✅ PASS: Value is > $100")
        
    print("=" * 60)
    
    # Optional: Place REAL test order?
    # await exchange.place_market_order(OrderSide.LONG, qty)

if __name__ == "__main__":
    asyncio.run(test_order_logic())
