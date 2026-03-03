"""
VEBB-AI: Liquidation Magnet Detector
Uses Kernel Density Estimation (KDE) to identify liquidation clusters 
from historical aggTrade data.
"""

import numpy as np
import pandas as pd
from numba import njit, prange
from scipy.signal import argrelextrema
from typing import List, Optional, Tuple

@njit(nogil=True, fastmath=True, parallel=True)
def _numba_kde_score_samples(data: np.ndarray, eval_points: np.ndarray, bandwidth: float) -> np.ndarray:
    """
    High-performance, pure math implementation of Gaussian Kernel Density Estimation.
    Utilizes Numba's Low-Level Virtual Machine (LLVM) compiler to bypass the Python GIL.
    Executing this function releases the GIL, allowing asyncio to continue polling sockets.
    """
    n = len(data)
    m = len(eval_points)
    result = np.zeros(m)
    
    # 1.0 / (bandwidth * sqrt(2 * PI))
    prefactor = 1.0 / (bandwidth * 2.5066282746310002)
    
    # prange allows automatic multi-threading at the C level
    for i in prange(m):
        sum_kernel = 0.0
        x = eval_points[i]
        for j in range(n):
            # (x - x_i) / h
            u = (x - data[j]) / bandwidth
            # Gaussian Kernel: exp(-0.5 * u^2)
            sum_kernel += np.exp(-0.5 * u * u)
            
        # Average the kernel contributions
        result[i] = prefactor * (sum_kernel / n)
        
    return result

class LiquidationMagnetDetector:
    """
    Analyzes high-frequency aggTrade data to project probabilistic liquidation 
    clusters using Kernel Density Estimation.
    """
    def __init__(self, leverage_tiers: Optional[List[int]] = None, mmr: float = 0.005, threshold_usd: float = 75000.0):
        if leverage_tiers is None:
            self.leverage_tiers = [20, 25, 50, 75, 100]
        else:
            self.leverage_tiers = leverage_tiers
        self.mmr = mmr
        self.threshold_usd = threshold_usd
        
        # State
        self.fitted_data = None
        self.bandwidth = None
        self.magnets = [] # List of (Price, Density) sorted by strength

    def estimate_liquidations(self, trades: List[dict]) -> np.ndarray:
        """
        Projects theoretical liquidation prices from historical trade nodes.
        Expects list of dicts: {'price': float, 'qty': float, 'side': str}
        """
        if not trades:
            return np.array([]).reshape(-1, 1)

        liq_prices = []
        for trade in trades:
            price = trade['price']
            qty = trade['qty']
            side = trade['side']
            value_usd = price * qty

            if value_usd < self.threshold_usd:
                continue

            for L in self.leverage_tiers:
                if side == "SELL": # Market Sell -> Short Position Opened (or Long Closed)
                    # For a Short position, the liquidation is ABOVE entry
                    short_liq = price * (1 + (1 / L) - self.mmr)
                    liq_prices.append(short_liq)
                else: # Market Buy -> Long Position Opened (or Short Closed)
                    # For a Long position, the liquidation is BELOW entry
                    long_liq = price * (1 - (1 / L) + self.mmr)
                    liq_prices.append(long_liq)

        return np.array(liq_prices).reshape(-1, 1)

    def fit_kde_clusters(self, liq_prices: np.ndarray) -> bool:
        """
        Optimizes KDE bandwidth and fits the distribution.
        """
        if len(liq_prices) < 20: # Need some density to work
            return False

        # Use a heuristic for bandwidth to avoid heavy GridSearch on every tick
        # Silverman's Rule of Thumb as a baseline
        std = np.std(liq_prices)
        n = len(liq_prices)
        if std == 0 or n == 0:
            return False
            
        silverman_bw = 0.9 * std * (n ** -0.2)
        
        # Clamp bandwidth to realistic values for BTC (e.g., $10 to $100 range)
        self.bandwidth = np.clip(silverman_bw, 10.0, 100.0)
        
        # Store data for fast evaluation
        # Numba arrays must be continuous, 1D is faster
        self.fitted_data = np.ascontiguousarray(liq_prices.flatten())
        return True

    def extract_magnet_zones(self, current_price: float, range_pct: float = 0.02, steps: int = 1000) -> List[Tuple[float, float]]:
        """
        Scans the PDF around the current price to identify local maxima.
        Returns sorted list of (Price_Level, Gravitational_Density)
        """
        if self.fitted_data is None or self.bandwidth is None:
            return []

        # Define scan range around price
        price_range = np.linspace(current_price * (1 - range_pct), 
                                  current_price * (1 + range_pct), 
                                  steps)
        eval_points = np.ascontiguousarray(price_range)
        
        # 🚀 ZERO-COPY C-LEVEL MATH EXECUTION
        density = _numba_kde_score_samples(self.fitted_data, eval_points, self.bandwidth)
        
        # Identify local maxima
        maxima_indices = argrelextrema(density, np.greater)[0]
        
        if len(maxima_indices) == 0:
            return []

        magnets = []
        for idx in maxima_indices:
            magnets.append((float(price_range[idx]), float(density[idx])))
            
        # Sort by density (strength)
        self.magnets = sorted(magnets, key=lambda x: x[1], reverse=True)
        return self.magnets

    def get_nearest_magnet(self, current_price: float, direction: str) -> Optional[float]:
        """
        Finds the strongest magnet in the desired trade direction.
        """
        for price, density in self.magnets:
            if direction == "LONG" and price > current_price:
                return price
            if direction == "SHORT" and price < current_price:
                return price
        return None
