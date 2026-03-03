"""
VEBB-AI: Live Statistical Regime Detector (Phase 61)
Replaces the offline Machine Learning HMM with a zero-latency, 
continuous rolling Z-Score of Garman-Klass Volatility and a 
rolling Hurst Exponent. 
"""

import numpy as np
from collections import deque
from typing import Tuple

class RegimeDetector:
    """
    Live Statistical Z-Score and Hurst Exponent Regime Classifier.
    Eliminates offline ML HMMs in favor of a 24-hour localized rolling window.
    """
    def __init__(self, window_size: int = 96): 
        # 96 periods of 15-minute candles = 24 hours
        self.window_size = window_size
        self.gk_vols = deque(maxlen=window_size)
        self.close_prices = deque(maxlen=window_size)
        
    def update(self, gk_vol: float, close_price: float) -> Tuple[str, str, float, float]:
        """
        Updates the rolling buffers and computes the live regime classifications.
        Returns:
            (vol_regime, trend_regime, z_score, hurst)
        """
        self.gk_vols.append(gk_vol)
        self.close_prices.append(close_price)
        
        # Calculate Z-Score for Volatility
        if len(self.gk_vols) < 2:
            return "NORMAL", "TRANSITION", 0.0, 0.5
            
        vol_array = np.array(self.gk_vols)
        mean_vol = np.mean(vol_array)
        std_vol = np.std(vol_array)
        
        if std_vol == 0:
            z_score = 0.0
        else:
            z_score = (gk_vol - mean_vol) / std_vol
            
        # Classify Volatility Regime based on Z-Score
        if z_score > 3.0:
            vol_regime = "CRISIS"       # Microstructural shock
        elif z_score > 1.5:
            vol_regime = "HIGH_VOL"     # High variance, widen limits
        else:
            vol_regime = "NORMAL"       # Standard operations
            
        # Calculate Hurst Exponent for Trend Classification
        hurst = self.calculate_hurst()
        
        # Classify Trend Regime based on Hurst Exponent
        if hurst < 0.45:
            trend_regime = "RANGE"      # Anti-persistent (fade shocks)
        elif hurst > 0.55:
            trend_regime = "TREND"      # Persistent (follow momentum)
        else:
            trend_regime = "TRANSITION" # Random Walk
            
        return vol_regime, trend_regime, z_score, hurst

    def calculate_hurst(self) -> float:
        """Calculate Hurst exponent from price buffer using R/S Analysis."""
        if len(self.close_prices) < 20: 
            return 0.5  # Neutral (random walk) minimum required
        
        series = np.array(self.close_prices)
        lags = range(2, min(20, len(series) // 2))
        
        try:
            tau = [np.sqrt(np.std(np.subtract(series[lag:], series[:-lag]))) for lag in lags]
            poly = np.polyfit(np.log(lags), np.log(tau), 1)
            return poly[0] * 2.0
        except Exception:
            return 0.5
            
    def update_price_buffer(self, close_price: float):
        """Deprecated method kept solely for backward-compatibility if referenced elsewhere."""
        self.close_prices.append(close_price)
