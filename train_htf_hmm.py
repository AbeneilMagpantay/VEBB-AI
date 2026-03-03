"""
VEBB-AI: Higher Timeframe HMM Training Script
Aggregates 15m data to 1H and 4H, then trains HMM models for each timeframe.
"""

import pandas as pd
import numpy as np
import pickle
from hmmlearn.hmm import GaussianHMM
from pathlib import Path

# Path to 15m data
DATA_PATH = Path(__file__).parent.parent / "Data" / "15m" / "BTCUSDT-15m-fresh.csv"
OUTPUT_DIR = Path(__file__).parent

def load_15m_data():
    """Load and clean 15m data."""
    print(f"[HTF Training] Loading 15m data from {DATA_PATH}...")
    
    df = pd.read_csv(DATA_PATH)
    
    # Parse timestamp
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts')
    
    # Phase 43: Filter for Modern Baseline (2024 onwards)
    df = df[df.index >= '2024-01-01']
    
    # Ensure we have OHLCV columns
    df = df[['open', 'high', 'low', 'close', 'volume']].copy()
    df.columns = ['open', 'high', 'low', 'close', 'volume']
    
    print(f"[HTF Training] Loaded {len(df):,} rows of modern 15m data (2024+)")
    print(f"[HTF Training] Date range: {df.index.min()} to {df.index.max()}")
    
    return df

def aggregate_to_higher_tf(df_15m, rule):
    """Aggregate 15m data to higher timeframe."""
    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }
    
    df_htf = df_15m.resample(rule).agg(agg_dict)
    df_htf = df_htf.dropna()
    
    return df_htf

def calculate_features(df):
    """Calculate HMM features: log return and Garman-Klass volatility."""
    # Log returns
    df['log_return'] = np.log(df['close'] / df['close'].shift(1))
    
    # Garman-Klass volatility (more efficient than close-to-close)
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    df['gk_volatility'] = np.sqrt(0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2)
    
    # Drop NaN
    df = df.dropna()
    
    return df

def train_hmm(features, n_states=3, random_state=42):
    """Train Gaussian HMM with 3 states (CALM, TRENDING, CRISIS)."""
    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=100,
        random_state=random_state
    )
    
    model.fit(features)
    
    return model

def label_regimes(model):
    """Label HMM states as CALM, TRENDING, CRISIS based on volatility."""
    # Get state means for volatility (second column)
    vol_means = model.means_[:, 1]
    
    # Sort states by volatility
    state_order = np.argsort(vol_means)
    
    labels = {}
    labels[state_order[0]] = "CALM"      # Lowest volatility
    labels[state_order[1]] = "TRENDING"  # Medium volatility
    labels[state_order[2]] = "CRISIS"    # Highest volatility
    
    return labels, state_order

def main():
    print("=" * 60)
    print("  VEBB-AI Higher Timeframe HMM Training")
    print("=" * 60)
    
    # Load 15m data
    df_15m = load_15m_data()
    
    # Aggregate to 1H and 4H
    print("\n[HTF Training] Aggregating to higher timeframes...")
    df_1h = aggregate_to_higher_tf(df_15m, '1h')
    df_4h = aggregate_to_higher_tf(df_15m, '4h')
    
    print(f"[HTF Training] 1H candles: {len(df_1h):,}")
    print(f"[HTF Training] 4H candles: {len(df_4h):,}")
    
    # Calculate features
    print("\n[HTF Training] Calculating features...")
    df_1h = calculate_features(df_1h)
    df_4h = calculate_features(df_4h)
    
    # Prepare feature matrices
    features_1h = df_1h[['log_return', 'gk_volatility']].values
    features_4h = df_4h[['log_return', 'gk_volatility']].values
    
    # Train HMM models
    print("\n[HTF Training] Training 1H HMM model...")
    model_1h = train_hmm(features_1h)
    labels_1h, order_1h = label_regimes(model_1h)
    print(f"[HTF Training] 1H Model converged: {model_1h.monitor_.converged}")
    print(f"[HTF Training] 1H State labels: {labels_1h}")
    
    print("\n[HTF Training] Training 4H HMM model...")
    model_4h = train_hmm(features_4h)
    labels_4h, order_4h = label_regimes(model_4h)
    print(f"[HTF Training] 4H Model converged: {model_4h.monitor_.converged}")
    print(f"[HTF Training] 4H State labels: {labels_4h}")
    
    # Save models
    print("\n[HTF Training] Saving models...")
    
    model_1h_path = OUTPUT_DIR / "hmm_model_1h.pkl"
    model_4h_path = OUTPUT_DIR / "hmm_model_4h.pkl"
    
    with open(model_1h_path, 'wb') as f:
        pickle.dump({
            'model': model_1h,
            'state_labels': labels_1h,
            'state_order': order_1h
        }, f)
    
    with open(model_4h_path, 'wb') as f:
        pickle.dump({
            'model': model_4h,
            'state_labels': labels_4h,
            'state_order': order_4h
        }, f)
    
    print(f"[HTF Training] Saved: {model_1h_path}")
    print(f"[HTF Training] Saved: {model_4h_path}")
    
    # Print summary stats
    print("\n" + "=" * 60)
    print("  Training Summary")
    print("=" * 60)
    
    for name, model, labels in [("1H", model_1h, labels_1h), ("4H", model_4h, labels_4h)]:
        print(f"\n{name} HMM Model:")
        print(f"  States: {model.n_components}")
        for i in range(model.n_components):
            label = labels[i]
            mean_ret = model.means_[i, 0] * 100
            mean_vol = model.means_[i, 1] * 100
            print(f"  State {i} ({label}): mean_return={mean_ret:+.3f}%, mean_vol={mean_vol:.3f}%")
    
    print("\n[HTF Training] Done!")

if __name__ == "__main__":
    main()
