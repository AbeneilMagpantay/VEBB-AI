"""
VEBB-AI: Train Modern HMM
Exclusively uses 2025-2026 data to avoid historical volatility skew.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from regime_detector import RegimeDetector

# Points to the directory with 2025 data
DATA_DIR = Path("../Data/5m")

def load_and_standardize(filepath: Path) -> pd.DataFrame:
    df = pd.read_csv(filepath)
    col_map = {
        'Timestamp': 'ts', 'timestamp': 'ts', 'ts': 'ts',
        'Open': 'open', 'open': 'open',
        'High': 'high', 'high': 'high',
        'Low': 'low', 'low': 'low',
        'Close': 'close', 'close': 'close',
        'Volume': 'volume', 'volume': 'volume'
    }
    
    # Rename existing columns to standard names
    # If both 'timestamp' and 'ts' exist, we prioritize the original 'timestamp' and drop 'ts'
    if 'timestamp' in df.columns and 'ts' in df.columns:
        df = df.drop(columns=['ts'])
        
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    # Ensure 'ts' is a single Series
    ts_col = df['ts']
    if isinstance(ts_col, pd.DataFrame):
        ts_col = ts_col.iloc[:, 0]
        
    if pd.api.types.is_integer_dtype(ts_col) or pd.api.types.is_float_dtype(ts_col):
        if ts_col.iloc[0] > 1e15: unit = 'us'
        else: unit = 'ms'
        df['ts'] = pd.to_datetime(ts_col, unit=unit)
    else:
        df['ts'] = pd.to_datetime(ts_col)
    
    return df[['ts', 'open', 'high', 'low', 'close', 'volume']]

def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    # GK Volatility
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    df['gk_vol'] = np.sqrt(np.abs(0.5 * (log_hl ** 2) - (2 * np.log(2) - 1) * (log_co ** 2)))
    
    # Log Return
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
    
    # Hurst Proxy (100-period rolling)
    df['hurst'] = 0.5 + df['log_ret'].rolling(100).apply(
        lambda x: x.autocorr(lag=1) * 0.25 if len(x) >= 2 else 0,
        raw=False
    )
    return df.dropna()

def main():
    print("=" * 60)
    print("  VEBB-AI: Modern HMM Recalibration (2025-2026)")
    print("=" * 60)
    
    # Use the freshly downloaded data
    modern_file = DATA_DIR / "BTCUSDT-5m-fresh.csv"
    if not modern_file.exists():
        print(f"Error: {modern_file} not found. Please run fetch_data.py first.")
        return

    print(f"Loading {modern_file.name}...")
    df = load_and_standardize(modern_file)
    print(f"  Loaded {len(df):,} rows.")
    
    # Remove outliers or zero-vol
    df = df[(df['volume'] > 0) & (df['close'] > 0)]
    
    print("Calculating modern features...")
    df = calculate_features(df)
    
    print("Training Modern HMM (3 states)...")
    detector = RegimeDetector()
    detector.train(df, n_states=3)
    
    # Save with unique name to avoid overwriting prod immediately
    model_name = "hmm_model_5m_modern.pkl"
    detector.save_model(model_name)
    
    print(f"\n✅ Modern Model saved to {model_name}")
    print("   To activate, rename it to 'hmm_model_5m.pkl' on your VM.")
    print("   Note: This model is calibrated to 2025-2026 volatility scales.")

if __name__ == "__main__":
    main()
