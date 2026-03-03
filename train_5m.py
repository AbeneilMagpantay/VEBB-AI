"""
VEBB-AI: Train HMM on 5-minute data
Combines 2011-2021 + 2025 data for more robust training.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from regime_detector import RegimeDetector

DATA_DIR = Path("../Data/5m")


def load_and_standardize(filepath: Path) -> pd.DataFrame:
    """Load CSV and standardize column names."""
    df = pd.read_csv(filepath)
    
    # Check if 'ts' column exists (already datetime string)
    if 'ts' in df.columns and df['ts'].dtype == 'object':
        df['ts'] = pd.to_datetime(df['ts'])
        # Use existing columns
        col_map = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'}
        df = df.rename(columns=col_map)
        return df[['ts', 'open', 'high', 'low', 'close', 'volume']]
    
    # Standardize column names for other formats
    col_map = {
        'Timestamp': 'ts',
        'timestamp': 'ts',
        'Open': 'open',
        'High': 'high', 
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    # Handle timestamp - could be milliseconds 
    if df['ts'].dtype in ['int64', 'float64']:
        # Check if it's in microseconds (2025 data) or milliseconds (2011-2021 data)
        if df['ts'].iloc[0] > 1e15:
            df['ts'] = pd.to_datetime(df['ts'], unit='us')
        else:
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    else:
        df['ts'] = pd.to_datetime(df['ts'])
    
    return df[['ts', 'open', 'high', 'low', 'close', 'volume']]


def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add GK volatility, Hurst proxy, log return features."""
    
    # Garman-Klass Volatility
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    df['gk_vol'] = np.sqrt(np.abs(0.5 * (log_hl ** 2) - (2 * np.log(2) - 1) * (log_co ** 2)))
    df['gk_vol'] = df['gk_vol'].clip(lower=0).fillna(0)
    
    # Log Return
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1)).fillna(0)
    
    # Hurst Exponent Proxy (simplified rolling autocorrelation)
    # This is a faster proxy than the full Hurst calculation
    df['hurst'] = 0.5 + df['log_ret'].rolling(100).apply(
        lambda x: x.autocorr(lag=1) * 0.25 if len(x) >= 2 else 0,
        raw=False
    ).fillna(0.5)
    
    return df


def main():
    print("=" * 60)
    print("  VEBB-AI: 5-Minute HMM Training")
    print("=" * 60)
    
    # Load 2011-2021 data
    file_2011_2021 = DATA_DIR / "btc_usd_5m_bitstamp_18-08-2011_27-04-2021.csv"
    print(f"\nLoading {file_2011_2021.name}...")
    df_old = load_and_standardize(file_2011_2021)
    print(f"  Loaded {len(df_old):,} rows ({df_old['ts'].min()} to {df_old['ts'].max()})")
    
    # Load 2025 data (use the clean file with 'ts' column)
    file_2025 = DATA_DIR / "BTC_5m_Clean.csv"
    if file_2025.exists():
        print(f"\nLoading {file_2025.name}...")
        df_2025 = load_and_standardize(file_2025)
        print(f"  Loaded {len(df_2025):,} rows ({df_2025['ts'].min()} to {df_2025['ts'].max()})")
    else:
        df_2025 = pd.DataFrame()
        print("\n2025 data not found, continuing with 2011-2021 only.")
    
    # Combine datasets
    print("\nCombining datasets...")
    if not df_2025.empty:
        df = pd.concat([df_old, df_2025], ignore_index=True)
    else:
        df = df_old
    df = df.sort_values('ts').drop_duplicates(subset=['ts']).reset_index(drop=True)
    print(f"  Total: {len(df):,} rows ({df['ts'].min()} to {df['ts'].max()})")
    
    # Remove zero-volume / invalid rows
    df = df[(df['volume'] > 0) & (df['close'] > 0) & (df['high'] > df['low'])]
    print(f"  After cleanup: {len(df):,} rows")
    
    # Calculate features
    print("\nCalculating features...")
    df = calculate_features(df)
    df = df.dropna()
    print(f"  Features calculated: {len(df):,} rows")
    
    # Train HMM
    print("\nTraining HMM (3 states)...")
    detector = RegimeDetector()
    detector.train(df, n_states=3)
    
    # Save model
    model_path = "hmm_model_5m.pkl"
    detector.save_model(model_path)
    
    # Test
    print("\n--- Test Predictions ---")
    for i in range(5):
        row = df.iloc[-(i+1)*1000]
        regime_id, regime_name, cb = detector.predict(row['gk_vol'], row['hurst'], row['log_ret'])
        print(f"  Sample {i+1}: {regime_name} (GK={row['gk_vol']:.6f}, Hurst={row['hurst']:.3f})")
    
    print(f"\n✅ Model saved to {model_path}")
    print("   To use 5m mode, set TIMEFRAME=5m in .env and restart main.py")


if __name__ == "__main__":
    main()
