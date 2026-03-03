"""
VEBB-AI: Dynamic Volatility Take-Profit Engine
Implements Williams VIXFix normalization and Chande Momentum Oscillator 
to dynamically scale TP boundaries.
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple

class DynamicVolatilityEngine:
    """
    Computes real-time volatility percentiles and momentum exhaustion limits.
    """
    def __init__(self, cmo_period=14, wvf_period=22, wvf_history=252, 
                 tp_min=0.003, tp_max=0.020):
        self.cmo_period = cmo_period
        self.wvf_period = wvf_period
        self.wvf_history = wvf_history
        self.tp_min = tp_min
        self.tp_max = tp_max
        
        # Historical buffers
        self.wvf_raw_buffer = []

    def compute_cmo(self, close_series: pd.Series) -> float:
        """
        Calculates the raw, unsmoothed Chande Momentum Oscillator (last value).
        """
        if len(close_series) < self.cmo_period + 1:
            return 0.0
            
        price_diff = close_series.diff().dropna()
        recent_diffs = price_diff.tail(self.cmo_period)
        
        sum_up = recent_diffs[recent_diffs > 0].sum()
        sum_down = abs(recent_diffs[recent_diffs < 0].sum())
        
        denominator = sum_up + sum_down
        if denominator == 0:
            return 0.0
            
        cmo = 100 * ((sum_up - sum_down) / denominator)
        return float(cmo)

    def compute_wvf_percentile(self, high_series: pd.Series, low_series: pd.Series, close_series: pd.Series) -> float:
        """
        Calculates the Williams VIXFix and normalizes it to a percentile rank.
        """
        if len(close_series) < self.wvf_period:
            return 0.0
            
        # 1. Raw VIXFix: (HighestRecentClose - CurrentLow) / HighestRecentClose
        highest_close = close_series.tail(self.wvf_period).max()
        current_low = low_series.iloc[-1]
        
        if highest_close == 0:
            return 0.0
            
        wvf_raw = ((highest_close - current_low) / highest_close) * 100
        self.wvf_raw_buffer.append(wvf_raw)
        
        # Keep buffer to history size
        if len(self.wvf_raw_buffer) > self.wvf_history:
            self.wvf_raw_buffer.pop(0)
            
        if len(self.wvf_raw_buffer) < 50: # Need some history to normalize
            return 0.0
            
        # 2. Normalize
        wvf_min = min(self.wvf_raw_buffer)
        wvf_max = max(self.wvf_raw_buffer)
        
        vw_range = wvf_max - wvf_min
        if vw_range == 0:
            return 0.0
            
        percentile = (wvf_raw - wvf_min) / vw_range
        return float(np.clip(percentile, 0.0, 1.0))

    def calculate_tp(self, high: pd.Series, low: pd.Series, close: pd.Series) -> float:
        """
        Scales TP based on VIXFix and applies CMO exhaustion override.
        """
        percentile = self.compute_wvf_percentile(high, low, close)
        cmo = self.compute_cmo(close)
        
        # Linear scaling
        tp_dynamic = self.tp_min + (percentile * (self.tp_max - self.tp_min))
        
        # CMO Exhaustion Override
        # Abs(CMO) > 50 indicates localized over-extension
        if abs(cmo) > 50:
            # Shift TP lower to lock in profits before reversion
            tp_dynamic *= 0.5
            
        return max(tp_dynamic, self.tp_min)

    def compute_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
        """Computes current ATR for trailing stop logic."""
        if len(close) < period + 1:
            return 0.0
            
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        
        return float(tr.tail(period).mean())
