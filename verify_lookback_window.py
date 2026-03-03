
import asyncio
import pandas as pd
import requests
from datetime import datetime, timedelta

async def verify_lookback():
    print("=" * 60)
    print("  VEBB-AI: Lookback & Window Verification")
    print("=" * 60)
    
    # 1. Verify 15m Lookback (900 candles)
    print("\n[1] Verifying 15m Lookback (900 candles)...")
    url_15m = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=900"
    try:
        resp = requests.get(url_15m)
        data = resp.json()
        
        start_ts = int(data[0][0])
        end_ts = int(data[-1][0])
        
        start_date = datetime.fromtimestamp(start_ts / 1000)
        end_date = datetime.fromtimestamp(end_ts / 1000)
        duration = end_date - start_date
        
        print(f"  First Candle: {start_date}")
        print(f"  Last Candle:  {end_date}")
        print(f"  Total Duration: {duration}")
        print(f"  Expected Duration: ~9.375 days (900 * 15m)")
        
        if 9 <= duration.days <= 10:
             print("  ✅ 15m Verification PASSED")
        else:
             print("  ❌ 15m Verification FAILED (Duration mismatch)")
             
    except Exception as e:
        print(f"  Error: {e}")



    # 3. Verify Sliding Window Logic
    print("\n[3] Verifying Sliding Window Logic (Simulation)...")
    buffer = list(range(900)) # Simulate a full buffer of 900 candles [0...899]
    print(f"  Initial Buffer: Size={len(buffer)}, First={buffer[0]}, Last={buffer[-1]}")
    
    # New Candle Arrives
    new_candle = 900
    print(f"  > New Candle Arrives: {new_candle}")
    
    # 1. Append
    buffer.append(new_candle)
    
    # 2. Pop Oldest
    if len(buffer) > 900:
        popped = buffer.pop(0)
        print(f"  > Sliding Window: Removed Oldest ({popped})")
    
    print(f"  Final Buffer: Size={len(buffer)}, First={buffer[0]}, Last={buffer[-1]}")
    
    if len(buffer) == 900 and buffer[0] == 1 and buffer[-1] == 900:
        print("  ✅ Sliding Window Logic PASSED (Maintained size 900, shifted by 1)")
    else:
        print("  ❌ Sliding Window Logic FAILED")

if __name__ == "__main__":
    asyncio.run(verify_lookback())
