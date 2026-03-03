"""
VEBB-AI: Automated Recalibration Engine (Phase 48)
Retrains the HMM model using live-logged market features.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from regime_detector import RegimeDetector
from dotenv import load_dotenv

load_dotenv()

def recalibrate():
    print("=" * 60)
    print("  VEBB-AI: Hybrid Recalibration Engine (Phase 51)")
    print("=" * 60)
    
    timeframe = os.getenv("TIMEFRAME", "15m").lower()
    
    # 1. Recalibrate Primary Anchor (e.g., 15m)
    primary_model = f"hmm_model_{timeframe}.pkl"
    live_data = Path(f"data/live_market_data_{timeframe}.csv")
    
    _train_model(live_data, primary_model, timeframe)
    
    # 2. Recalibrate Macro Filter (30m)
    if timeframe != "30m":
        macro_model = "hmm_model_30m.pkl"
        macro_data = Path("data/live_market_data_30m.csv")
        _train_model(macro_data, macro_model, "30m")

def _train_model(data_path: Path, model_path: str, tf: str):
    if not data_path.exists():
        print(f"\n[!] Skipping {tf}: Data file {data_path} not found.")
        return

    print(f"\nLoading {tf} features from {data_path}...")
    df = pd.read_csv(data_path)
    
    MIN_TRAIN_ROWS = 500 # 15m bars (500 bars = ~5 days)
    if len(df) < MIN_TRAIN_ROWS:
        print(f"    Warning: Limited data for {tf} ({len(df)} bars).")
    
    # Keep last 5k bars for adaptive memory
    df = df.tail(5000).reset_index(drop=True)
    
    print(f"    Retraining HMM for {tf}...")
    detector = RegimeDetector()
    try:
        detector.train(df, n_states=3)
        detector.save_model(model_path)
        print(f"    ✅ SUCCESS: {model_path} updated.")
    except Exception as e:
        print(f"    ❌ Error training {tf}: {e}")

if __name__ == "__main__":
    recalibrate()
