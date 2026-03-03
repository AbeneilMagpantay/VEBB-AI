"""
VEBB-AI: High-Frequency Institutional VWAP Engine (Phase 72)
Implements an O(1) Rolling Composite Micro-VWAP and CVD Z-Score
channel using Welford's Method inside a Numba compiled circular buffer.
"""

import numpy as np
from numba import njit, float64, int64
from typing import Tuple, Dict

# =================================================================
# NUMBA COMPILED O(1) KERNEL
# =================================================================
@njit(nogil=True, cache=True)
def update_rolling_vwap_welford(
    prices_buffer: np.ndarray,
    volumes_buffer: np.ndarray,
    deltas_buffer: np.ndarray,
    buffer_index: int,
    max_window_size: int,
    p_in: float,
    v_in: float,
    delta_in: float,
    current_cv: float,
    current_m: float,
    current_s: float,
    current_cd: float,
    current_cd_m: float,
    current_cd_s: float
) -> Tuple[float, float, float, float, float, float, float, float]:
    """
    O(1) Circular Buffer update using Welford's algorithm for both:
    1. Volume-Weighted Price Variance (VWAP)
    2. Standard Cumulative Volume Delta (CVD) Variance
    
    Returns: 
    (new_cv, new_vwap, new_s, variance_vwap, 
     new_cd, new_cd_m, new_cd_s, variance_cvd)
    """
    # 1. Update the contigious circular buffer
    ptr = buffer_index % max_window_size
    
    # Retrieve outgoing data
    p_out = prices_buffer[ptr]
    v_out = volumes_buffer[ptr]
    delta_out = deltas_buffer[ptr]
    
    # Write incoming data
    prices_buffer[ptr] = p_in
    volumes_buffer[ptr] = v_in
    deltas_buffer[ptr] = delta_in
    
    # -------------------------------------------------------------
    # VOLUME-WEIGHTED PRICE VARIANCE (VWAP)
    # -------------------------------------------------------------
    new_cv = current_cv + v_in - v_out
    
    if new_cv <= 0.0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        
    diff_in = p_in - current_m
    diff_out = p_out - current_m
    
    new_m = current_m + ((v_in * diff_in) - (v_out * diff_out)) / new_cv
    
    diff_in_new = p_in - new_m
    diff_out_new = p_out - new_m
    
    new_s = current_s + (v_in * diff_in * diff_in_new) - (v_out * diff_out * diff_out_new)
    variance_vwap = max(0.0, new_s / new_cv)
    
    # -------------------------------------------------------------
    # CVD Z-SCORE VARIANCE (Standard Welford for rolling delta)
    # -------------------------------------------------------------
    window_count = float(min(buffer_index + 1, max_window_size))
    if window_count <= 1:
        # Prevent division by zero on the first tick
        return new_cv, new_m, new_s, variance_vwap, delta_in, delta_in, 0.0, 0.0
        
    # We are calculating the variance of the sequential Delta inputs
    # The sum of these deltas equals the Cumulative Volume Delta (CVD)
    new_cd = current_cd + delta_in - delta_out
    
    diff_cvd_in = delta_in - current_cd_m
    diff_cvd_out = delta_out - current_cd_m
    
    new_cd_m = current_cd_m + (diff_cvd_in - diff_cvd_out) / window_count
    
    diff_cvd_in_new = delta_in - new_cd_m
    diff_cvd_out_new = delta_out - new_cd_m
    
    new_cd_s = current_cd_s + (diff_cvd_in * diff_cvd_in_new) - (diff_cvd_out * diff_cvd_out_new)
    variance_cvd = max(0.0, new_cd_s / max(1.0, window_count - 1.0)) # Sample variance
    
    return new_cv, new_m, new_s, variance_vwap, new_cd, new_cd_m, new_cd_s, variance_cvd


# =================================================================
# PYTHON WRAPPER CLASS
# =================================================================
class InstitutionalVWAPEngine:
    """
    Maintains the Numba arrays and provides safe Python extraction for 
    the sniper engine, shielding the asyncio loop from O(N) calculations.
    """
    
    def __init__(self, max_window_size: int = 15000):
        # 15,000 is an extremely safe upper bound for a 315-minute 
        # rolling window assuming approx 1 trade agg every 1.2 seconds.
        self.max_window_size = max_window_size
        
        # Pre-allocated contiguous memory
        self.prices = np.zeros(max_window_size, dtype=np.float64)
        self.volumes = np.zeros(max_window_size, dtype=np.float64)
        self.deltas = np.zeros(max_window_size, dtype=np.float64)
        
        # Pointers and state
        self.index = 0
        
        # VWAP State
        self.cv = 0.0 # Cumulative Volume
        self.m = 0.0  # VWAP Mean
        self.s = 0.0  # Sum of Squares (VWAP)
        self.variance_vwap = 0.0
        
        # CVD State
        self.cd = 0.0     # Cumulative Delta 
        self.cd_m = 0.0   # Mean of Deltas
        self.cd_s = 0.0   # Sum of Squares (CDF)
        self.variance_cvd = 0.0

    def update(self, price: float, volume: float, delta: float):
        """Called constantly by FootprintBuilder or DataStream."""
        
        # First tick initialization
        if self.index == 0:
            self.m = price
            self.cd_m = delta
            
        (
            self.cv, self.m, self.s, self.variance_vwap, 
            self.cd, self.cd_m, self.cd_s, self.variance_cvd
        ) = update_rolling_vwap_welford(
            self.prices, self.volumes, self.deltas,
            self.index, self.max_window_size,
            p_in=price, v_in=volume, delta_in=delta,
            current_cv=self.cv, current_m=self.m, current_s=self.s,
            current_cd=self.cd, current_cd_m=self.cd_m, current_cd_s=self.cd_s
        )
        
        self.index += 1

    def get_metrics(self, current_price: float = 0.0) -> Dict[str, float]:
        """Returns instantaneous dual-vector institutional metrics."""
        
        std_vwap = np.sqrt(self.variance_vwap)
        std_cvd = np.sqrt(self.variance_cvd)
        
        # Calculate Volume Z-Score securely
        if std_cvd > 0.0:
            cvd_z_score = (self.cd - self.cd_m) / std_cvd
        else:
            cvd_z_score = 0.0
        
        # Calculate VWAP Deviation (how far price is from VWAP in standard deviations)
        if std_vwap > 0.0 and current_price > 0.0:
            vwap_deviation = (current_price - self.m) / std_vwap
        elif self.m > 0.0 and current_price > 0.0:
            vwap_deviation = (current_price - self.m) / self.m * 100  # Fallback: pct diff
        else:
            vwap_deviation = 0.0
            
        return {
            "vwap": self.m,
            "std_vwap": std_vwap,
            "vwap_deviation": vwap_deviation,
            "cvd": self.cd,
            "cvd_z_score": cvd_z_score,
            "is_ready": self.index > min(50, self.max_window_size // 10) 
        }

    def format_for_gemini(self) -> str:
        metrics = self.get_metrics()
        return (
            f"Institutional Rolling Micro-VWAP:\n"
            f"- VWAP: ${metrics['vwap']:,.2f} (StdDev: ${metrics['std_vwap']:,.2f})\n"
            f"- CVD Extnsion: {metrics['cvd']:+.2f} BTC (Z-Score: {metrics['cvd_z_score']:+.2f})"
        )
