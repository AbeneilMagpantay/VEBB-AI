"""
VEBB-AI: Sentinel Lead-Lag Detector
Implements the Hayashi-Yoshida covariation estimator to detect 
asynchronous lead-lag signatures between assets (SOL vs BTC).
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta

class SentinelLeadLagDetector:
    """
    Computes continuous-time lead-lag relationship between secondary (SOL) 
    and primary (BTC) price streams.
    """
    def __init__(self, max_lag_seconds: float = 30.0, significance_threshold: float = 0.003):
        self.max_lag = max_lag_seconds
        self.significance_threshold = significance_threshold
        # State
        self.last_signal = "NEUTRAL"
        self.current_lag = 0.0
        self.current_corr = 0.0

    def compute_lead_lag(self, btc_series: pd.Series, sol_series: pd.Series) -> dict:
        """
        Hayashi-Yoshida Covariation Estimator for asynchronous time series.
        Optimized for 15m scalping timeframe.
        """
        if len(btc_series) < 50 or len(sol_series) < 50:
            return {"status": "insufficient_data"}

        # Convert to numpy for performance
        btc_times = btc_series.index.values.astype(np.int64) // 10**9 # UNIX seconds
        btc_prices = btc_series.values
        sol_times = sol_series.index.values.astype(np.int64) // 10**9
        sol_prices = sol_series.values

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            with np.errstate(divide='ignore', invalid='ignore'):
                
                # Calculate returns
                btc_rets = np.diff(np.log(btc_prices))
                sol_rets = np.diff(np.log(sol_prices))
                
                # Intervals are [t_i, t_{i+1}]
                btc_intervals = list(zip(btc_times[:-1], btc_times[1:], btc_rets))
                sol_intervals = list(zip(sol_times[:-1], sol_times[1:], sol_rets))
    
                # We search for the lag theta in [-max_lag, max_lag] that maximizes 
                # the cross-variation:
                # CV(theta) = sum_{i,j} ret_i * ret_j * I(Interval_i intersects Interval_j + theta)
                
                lags = np.linspace(-self.max_lag, self.max_lag, 61) # Every 1s (approx)
                best_cv = -1.0
                best_theta = 0.0
                
                for theta in lags:
                    cv = 0.0
                    # Optimized interval intersection check
                    for b_start, b_end, b_ret in btc_intervals:
                        # Target interval for sol: [s_start + theta, s_end + theta]
                        for s_start, s_end, s_ret in sol_intervals:
                            # Check intersection of [b_start, b_end] and [s_start + theta, s_end + theta]
                            if max(b_start, s_start + theta) < min(b_end, s_end + theta):
                                cv += b_ret * s_ret
                    
                    abs_cv = abs(cv)
                    if abs_cv > best_cv:
                        best_cv = abs_cv
                        best_theta = theta
    
                # Map leadership: Positive theta in this loop logic means BTC leads SOL? 
                # Let's adjust for SOL leading BTC:
                # If theta is negative, it means SOL + theta aligns with BTC, so SOL is 'earlier'.
                # Example: SOL at T=0, BTC at T=2. theta = -2. SOL(-2) + 2 = BTC(0) -> No.
                # If theta = 2, then SOL(T) overlaps with BTC(T+2). So SOL leads by 2s.
                
                leader = "SOL" if best_theta > 0 else "BTC"
                self.current_lag = abs(best_theta)
                
                # Aligned correlation (Spearman for robustness)
                if leader == "SOL":
                    # Shift SOL forward or BTC backward
                    # For simplicity, we compare the current returns of SOL to 
                    # the future returns of BTC (or current BTC to past SOL)
                    pass 
    
                # Simplified correlation for live reporting
                # Check variance to prevent numpy RuntimeWarnings (divide by zero)
                btc_std = np.std(btc_prices)
                sol_std = np.std(sol_prices)
                
                # Must be strictly > 0 and not NaN
                if btc_std > 1e-8 and sol_std > 1e-8:
                    if leader == "SOL":
                        # If SOL leads, compare SOL returns with a shifted BTC series
                        shift_periods = max(1, int(self.current_lag))
                        # Shift BTC backwards (Current SOL vs Future BTC)
                        corr_series_btc = btc_series.shift(-shift_periods)
                        corr = btc_series.corr(sol_series) if shift_periods == 0 else corr_series_btc.corr(sol_series)
                    else:
                        corr = btc_series.corr(sol_series)
                        
                    self.current_corr = 0.0 if pd.isna(corr) else corr
                else:
                    self.current_corr = 0.0
    
                return {
                    "status": "success",
                    "leader": leader,
                    "lag_seconds": self.current_lag,
                    "cv_magnitude": best_cv,
                    "correlation": self.current_corr
                }

    def evaluate_signal(self, current_sol_ret: float, stats: dict) -> str:
        """
        Evaluates if the Sentinel impulse is strong enough to lead BTC.
        """
        if stats.get("status") != "success":
            return "NEUTRAL"

        is_sol_leading = stats["leader"] == "SOL"
        is_impulse_strong = abs(current_sol_ret) >= self.significance_threshold
        is_correlated = stats["correlation"] > 0.4 # Significant cross-asset link

        if is_sol_leading and is_impulse_strong and is_correlated:
            self.last_signal = "LONG" if current_sol_ret > 0 else "SHORT"
            return self.last_signal
        
        return "NEUTRAL"
