"""
VEBB-AI: Institutional Microstructure Module
Implements OFI, Hawkes Process Intensity, and Iceberg detection.
"""

import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class MicrostructureMetrics:
    """Advanced market microstructure metrics."""
    ofi: float = 0.0  # Order Flow Imbalance (lead indicator)
    intensity: float = 0.0  # Hawkes Process intensity
    iceberg_score: float = 0.0  # Probability of passive absorption
    absorption_probability: float = 0.0  # Continuous Sigmoid Iceberg Score
    kyles_lambda: float = 0.0 # Price Impact per unit delta
    ofi_cvd_divergence: float = 0.0  # Divergence between OFI and CVD
    buy_pressure: float = 0.0
    sell_pressure: float = 0.0
    hawkes_lambda: float = 0.0 # Real-time event arrival rate
    hawkes_derivative: float = 0.0 # Deceleration metric (d/dt)

class MicrostructureEngine:
    """
    Analyzes trade-by-trade and book-by-book dynamics to detect institutional intent.
    """
    
    def __init__(self, timeframe: str = "15m", h24_window: int = 96, ofi_window: int = 10, intensity_decay: float = 0.1):
        # Phase 51: Optimized for 15m Window Persistence
        self.ofi_decay = 0.85      # Slower decay for 15m institutional flow
        self.intensity_decay = 0.05 # Lower beta for longer-lasting cascades
        self.ofi_window = ofi_window
        self.trades = deque(maxlen=200)
        self.last_price = None
        self.intensity = 0.0
        
        # Phase 63: Rigorous Hawkes Point-Process Modeling
        self.hawkes_lambda = 0.0
        self.prev_hawkes_lambda = 0.0
        # Binance BTCUSDT specific Maximum Likelihood parameters
        self.hawkes_mu = 4.127
        self.hawkes_alpha = 1.854
        self.hawkes_beta = 2.321
        import time
        self.last_hawkes_update = time.time()
        
        # Phase 64: Continuous Iceberg Detection (Kyle's Lambda Arrays)
        self.lambda_buffer = deque(maxlen=h24_window) # Dynamic 24h trailing distribution
        
        # OFI tracking (Aggressive flow)
        self.delta_volumes = deque(maxlen=ofi_window)
        
    def update_with_trade(self, price: float, qty: float, is_buyer_taker: bool):
        """Update OFI and intensity based on a single trade."""
        # 1. Legacy Intensity (Excitation)
        self.intensity += 1.0
        
        # 2. Phase 63 Hawkes Excitation (Jump Size)
        self.hawkes_lambda += self.hawkes_alpha
        
        # 3. Basic OFI component (Trade-based)
        delta = qty if is_buyer_taker else -qty
        self.trades.append((price, delta))

    def update_with_candle(self, candle: dict):
        """Helper to update state from a summarized candle (Phase 40)."""
        close = candle.get("close", 0)
        open_p = candle.get("open", 0)
        volume = candle.get("volume", 0)
        
        # Approximate delta if not explicitly provided
        delta = candle.get("delta", (close - open_p) * volume / 1000)
        
        self.trades.append((close, delta))
        self.intensity += 2.0 # Candles represent more activity

    def calculate_metrics(self, current_price: float, current_delta: float) -> MicrostructureMetrics:
        """
        Calculate complex metrics for the current state.
        - OFI: Normalized trade imbalance over the window.
        - Iceberg Score: CVD divergence vs. Price displacement (Absorption).
        - OFI-CVD Divergence: Lead vs. Aggression alignment.
        """
        # Legacy Decay intensity
        self.intensity *= (1 - self.intensity_decay)
        
        # Phase 63: Continuous Hawkes Decay & First-Derivative Calculation
        import time
        now = time.time()
        # Use a dynamic time delta (in seconds) since last metric calc
        time_delta = max(now - self.last_hawkes_update, 0.001) 
        self.last_hawkes_update = now
        
        # Exponential decay: λ(t) = μ + (λ(0) - μ) * e^(-βt)
        self.hawkes_lambda = self.hawkes_mu + (self.hawkes_lambda - self.hawkes_mu) * np.exp(-self.hawkes_beta * time_delta)
        
        # Calculate dλ/dt (Kinematic deceleration)
        hawkes_deriv = self.hawkes_lambda - self.prev_hawkes_lambda
        self.prev_hawkes_lambda = self.hawkes_lambda
        
        # 1. OFI Calculation (Institutional Lead Indicator)
        if len(self.trades) < 5:
            return MicrostructureMetrics(
                intensity=self.intensity, 
                hawkes_lambda=self.hawkes_lambda, 
                hawkes_derivative=hawkes_deriv
            )
            
        recent_trades = list(self.trades)[-self.ofi_window:]
        recent_deltas = [d for p, d in recent_trades]
        total_abs_vol = sum(abs(d) for d in recent_deltas)
        ofi = sum(recent_deltas) / total_abs_vol if total_abs_vol > 0 else 0.0
        
        # 2. Continuous Iceberg Detection (Kyle's Lambda Z-Score & Sigmoid)
        # Compare CVD (Market Aggression) vs. Price Displacement (Result)
        displacement = 0.0
        max_recent_price = current_price
        min_recent_price = current_price
        
        if len(recent_trades) > 0:
            prices = [p for p, d in recent_trades]
            start_price = prices[0]
            displacement = abs(current_price - start_price)
            max_recent_price = max(prices)
            min_recent_price = min(prices)
            
        # Localized True Range Proxy (using recent trade window)
        local_atr = max(max_recent_price - min_recent_price, 0.01) # Avoid DivZero
            
        # Calculate Kyle's Lambda (Price impact / sqrt(Volume))
        # Deep Research Phase 64: Fractional Power-Law scaling requires sqrt of Volume
        vol_sqrt = np.sqrt(total_abs_vol) if total_abs_vol > 0 else 1.0
        kyles_lambda = displacement / vol_sqrt
        
        self.lambda_buffer.append(kyles_lambda)
        
        # Statistical Modeling of Absorption
        absorption_prob = 0.0
        z_lambda = 0.0
        
        if len(self.lambda_buffer) > 10:
            mu_lambda = np.mean(self.lambda_buffer)
            std_lambda = np.std(self.lambda_buffer)
            
            if std_lambda > 0:
                z_lambda = (kyles_lambda - mu_lambda) / std_lambda
                
                # We want anomalously LOW price impact (Highly Negative Z-Score)
                # Sigmoid params from Phase 64 Deep Research
                k_sigmoid = 2.0
                gamma = 2.5 
                
                # Logistic Sigmoid Transformation
                val = -k_sigmoid * (-z_lambda - gamma)
                # Prevent overflow in exp
                if val > 100:
                    absorption_prob = 0.0
                elif val < -100:
                    absorption_prob = 1.0
                else:
                    absorption_prob = 1.0 / (1.0 + np.exp(val))
                    
                # Volatility Displacement Penalty
                # If the price moved more than 50% of the local ATR, it's not a true absorption wall
                penalized_displacement = displacement / local_atr
                if penalized_displacement > 0.5:
                    # Square the penalty to aggressively degrade confidence if price is slipping
                    penalty_factor = max(0.0, 1.0 - ((penalized_displacement - 0.5) * 2.0)**2)
                    absorption_prob *= penalty_factor
        
        # 3. OFI-CVD Divergence
        # OFI (calculated from trade sequence) vs Current Delta (cumulative)
        cvd_sign = 1 if current_delta > 0 else -1
        ofi_sign = 1 if ofi > 0 else -1
        # If they disagree, it's a divergence
        ofi_cvd_divergence = abs(ofi - (current_delta / 500.0)) if ofi_sign != cvd_sign else 0.0
        
        return MicrostructureMetrics(
            ofi=ofi,
            intensity=self.intensity,
            iceberg_score=iceberg_score if 'iceberg_score' in locals() else 0.0,
            absorption_probability=absorption_prob,
            kyles_lambda=kyles_lambda,
            ofi_cvd_divergence=(ofi - current_delta),
            hawkes_lambda=self.hawkes_lambda,
            hawkes_derivative=hawkes_deriv,
            buy_pressure=sum(d for d in recent_deltas if d > 0),
            sell_pressure=abs(sum(d for d in recent_deltas if d < 0))
        )

    def get_adaptive_threshold(self, base_threshold: float, rv: float, hurst: float, intensity: float = 1.0, iceberg: float = 0.0) -> float:
        """
        Log-Logistic OBI Thresholding (Phase 49/51/64).
        Timeframe-Agnostic Z-Score Scaling.
        """
        import math
        
        # 1. PARAMETERS FROM RESEARCH
        K = 1.5  # Curve steepness
        OBI_MAX = 0.80 # Margin of Safety
        OBI_MIN = 0.20 # Buffer against fakeouts
        
        # Phase 64: We use Hawkes Z-Score, not raw intensity counts.
        # So L_MID represents the critical Z-Score threshold (e.g., 2.5 sigma)
        L_MID = 2.5
        
        # We pass intensity into this function, but the bot might pass raw intensity.
        # If so, substitute it with `hawkes_lambda` Z-Score dynamically.
        # Here we approximate Z if actual Z isn't passed directly, 
        # or we assume 'intensity' param IS the z-score now.
        z_score = intensity 
        
        # 3. LOGISTICAL SIGMOID MODEL
        try:
            exp_term = math.exp(K * (z_score - L_MID))
            logistic_thresh = OBI_MIN + (OBI_MAX - OBI_MIN) / (1.0 + exp_term)
        except OverflowError:
            logistic_thresh = OBI_MIN # Intensity is so high it hit the lower asymptote
            
        # 4. VOLATILITY & TREND VOLUMIZER
        # We still scale the result slightly by RV/Hurst to ensure baseline safety
        rv_mult = 1.0 + (rv * 10.0)
        hurst_mult = 1.0 - (hurst - 0.5)
        
        final_thresh = logistic_thresh * rv_mult * hurst_mult
        
        return max(OBI_MIN, min(OBI_MAX, final_thresh))
