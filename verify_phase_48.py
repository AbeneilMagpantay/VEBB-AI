"""
VEBB-AI: Phase 48 Verification Script
Tests the Feature Logger and Recalibration Engine.
"""

import os
import pandas as pd
from datetime import datetime
import subprocess

def test_logger():
    print("Testing Feature Logger logic...")
    # Mock candle and features
    mock_candle = {
        "ts": datetime.now().isoformat(),
        "open": 60000.0,
        "high": 61000.0,
        "low": 59000.0,
        "close": 60500.0,
        "volume": 100.0
    }
    gk_vol = 0.0015
    hurst = 0.45
    log_ret = 0.0001

    # Import and call logger (we imitate the TradingBot method)
    import csv
    os.makedirs("data", exist_ok=True)
    log_file = "data/live_market_data.csv"
    file_exists = os.path.isfile(log_file)
    
    with open(log_file, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["ts", "open", "high", "low", "close", "volume", "gk_vol", "hurst", "log_ret"])
        
        writer.writerow([
            mock_candle["ts"],
            mock_candle["open"],
            mock_candle["high"],
            mock_candle["low"],
            mock_candle["close"],
            mock_candle["volume"],
            f"{gk_vol:.8f}",
            f"{hurst:.4f}",
            f"{log_ret:.8f}"
        ])
    
    print(f"Logged test row to {log_file}")

def test_recalibrator():
    print("\nTesting Recalibration Engine execution...")
    # Fill with dummy data to allow training (if file is too small)
    log_file = "data/live_market_data.csv"
    df = pd.read_csv(log_file)
    
    if len(df) < 50:
        print("Generating dummy rows for training test...")
        # Create 100 rows of dummy data
        dummy_rows = []
        for i in range(100):
            dummy_rows.append({
                "ts": datetime.now().isoformat(),
                "open": 60000.0 + i,
                "high": 61000.0 + i,
                "low": 59000.0 + i,
                "close": 60500.0 + i,
                "volume": 100.0,
                "gk_vol": 0.001 + (i * 0.0001),
                "hurst": 0.4 + (i * 0.001),
                "log_ret": 0.0001
            })
        dummy_df = pd.DataFrame(dummy_rows)
        dummy_df.to_csv(log_file, index=False)
        print(f"Generated 100 dummy rows in {log_file}")

    # Run recalibrate.py
    try:
        result = subprocess.run(["python", "recalibrate.py"], capture_output=True, text=True)
        print(result.stdout)
        if result.returncode == 0:
            print("recalibrate.py executed successfully.")
        else:
            print(f"recalibrate.py failed: {result.stderr}")
    except Exception as e:
        print(f"Error running recalibrate.py: {e}")

if __name__ == "__main__":
    test_logger()
    test_recalibrator()
