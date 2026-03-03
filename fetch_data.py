"""
VEBB-AI: Fetch Historical Data
Downloads fresh OHLCV data from Binance Futures.
"""

import asyncio
import pandas as pd
import time
from datetime import datetime, timedelta
from pathlib import Path
from exchange_client import ExchangeClient

DATA_DIR = Path("../Data/5m")
DATA_DIR_15M = Path("../Data/15m")

async def fetch_historical_data(symbol: str, interval: str, days: int = 30):
    client = ExchangeClient(testnet=False) # Use Mainnet for real data
    
    print(f"Fetching {days} days of {interval} data for {symbol}...")
    
    end_time = int(time.time() * 1000)
    start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    
    all_klines = []
    current_start = start_time
    
    while current_start < end_time:
        print(f"  Fetching from {datetime.fromtimestamp(current_start/1000)}...")
        klines = await client.get_klines(symbol=symbol, interval=interval, limit=1000, start_time=current_start)
        
        if not klines:
            break
            
        all_klines.extend(klines)
        
        # New start time is the close time of the last kline + 1ms
        current_start = klines[-1][6] + 1
        
        # Rate limit protection
        await asyncio.sleep(0.5)
        
        if len(klines) < 1000:
            break

    # Format into DataFrame
    columns = [
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 
        'close_time', 'qav', 'num_trades', 'taker_base', 'taker_quote', 'ignore'
    ]
    df = pd.DataFrame(all_klines, columns=columns)
    
    # Convert to numeric
    for col in df.columns:
        if col != 'timestamp' and col != 'close_time':
            df[col] = pd.to_numeric(df[col])
            
    # Add 'ts' column for consistency with existing scripts
    df['ts'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # Save to CSV
    filename = f"{symbol}-{interval}-fresh.csv"
    if interval == "5m":
        save_path = DATA_DIR / filename
    else:
        save_path = DATA_DIR_15M / filename
        
    save_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(save_path, index=False)
    
    print(f"✅ Saved {len(df):,} rows to {save_path}")
    return save_path

async def main():
    # Fetch all data from 2025-01-01 to Present
    start_str = "2025-01-01"
    start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    days_since_2025 = (datetime.now() - start_dt).days + 1
    
    print(f"--- Global Recalibration: Fetching data from {start_str} ---")
    
    # Fetch 5m data
    await fetch_historical_data("BTCUSDT", "5m", days=days_since_2025)
    # Fetch 15m data
    await fetch_historical_data("BTCUSDT", "15m", days=days_since_2025)

if __name__ == "__main__":
    asyncio.run(main())
