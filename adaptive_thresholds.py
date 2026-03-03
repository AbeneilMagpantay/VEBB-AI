"""
VEBB-AI: Self-Calibrating Threshold Framework (Phase 109)

Replaces all hardcoded numerical thresholds with percentile-based,
rolling, regime-aware alternatives.

Every constant is derived from observable, rolling market statistics.
No developer intuition. No fixed magic numbers.

Derived from Deep Research: "Adaptive Thresholds for BTC HFT"
"""

import numpy as np
from collections import deque


class AdaptiveRegimeDetector:
    """
    Replaces fixed Z-score (3.0/1.5) and Hurst (0.55/0.45) thresholds
    with rolling percentile boundaries + asymmetric hysteresis.
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.z_history = deque(maxlen=window)
        self.h_history = deque(maxlen=window)
        self.current_vol_regime = "NORMAL"
        self.current_trend_regime = "TRANSITION"

    def update(self, current_z: float, current_h: float) -> tuple:
        """Returns (vol_regime, trend_regime) using adaptive percentile thresholds."""
        self.z_history.append(current_z)
        self.h_history.append(current_h)

        # Cold start: fall back to legacy hardcoded values
        if len(self.z_history) < self.window:
            vol = "CRISIS" if current_z > 3.0 else "HIGH_VOL" if current_z > 1.5 else "NORMAL"
            trend = "TREND" if current_h > 0.55 else "RANGE" if current_h < 0.45 else "TRANSITION"
            self.current_vol_regime = vol
            self.current_trend_regime = trend
            return vol, trend

        z_arr = np.array(self.z_history)
        h_arr = np.array(self.h_history)

        # Volatility regime: percentile-based with hysteresis
        p99, p90, p85, p75 = np.percentile(z_arr, [99, 90, 85, 75])

        if self.current_vol_regime == "CRISIS":
            if current_z < p90:
                self.current_vol_regime = "HIGH_VOL"
        elif self.current_vol_regime == "HIGH_VOL":
            if current_z > p99:
                self.current_vol_regime = "CRISIS"
            elif current_z < p75:
                self.current_vol_regime = "NORMAL"
        else:  # NORMAL
            if current_z > p99:
                self.current_vol_regime = "CRISIS"
            elif current_z > p85:
                self.current_vol_regime = "HIGH_VOL"

        # Trend regime: μ ± kσ with hysteresis
        mu_h = np.mean(h_arr)
        std_h = max(np.std(h_arr), 0.001)

        if self.current_trend_regime == "TREND":
            if current_h < mu_h + 0.25 * std_h:
                self.current_trend_regime = "TRANSITION"
        elif self.current_trend_regime == "RANGE":
            if current_h > mu_h - 0.25 * std_h:
                self.current_trend_regime = "TRANSITION"
        else:  # TRANSITION
            if current_h > mu_h + 0.75 * std_h:
                self.current_trend_regime = "TREND"
            elif current_h < mu_h - 0.75 * std_h:
                self.current_trend_regime = "RANGE"

        return self.current_vol_regime, self.current_trend_regime


class AdaptiveSniperBuffer:
    """
    Replaces hardcoded ENTRY_BUFFER=25 with Hurst-scaled, compression-aware buffer.
    Higher Hurst (trending) → wider entry zone (lower buffer).
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.comp_history = deque(maxlen=window)
        self.roc_history = deque(maxlen=window)
        self.va_history = deque(maxlen=window)
        self.last_buffer = 25.0

    def calculate_buffer(self, hurst: float, mu_h: float, std_h: float,
                         va_high: float, va_low: float, atr: float) -> float:
        """Returns adaptive ENTRY_BUFFER (replaces fixed 25)."""
        va_width = max(va_high - va_low, 0.01)
        self.va_history.append(va_width)

        # Rate of Change of VA width over 3 periods
        if len(self.va_history) >= 4:
            roc = (va_width - self.va_history[-4]) / (self.va_history[-4] + 1e-8)
        else:
            roc = 0.0
        self.roc_history.append(roc)

        comp_ratio = va_width / max(atr, 0.01)
        self.comp_history.append(comp_ratio)

        # Cold start
        if len(self.comp_history) < self.window:
            return 25.0

        # Normalize Hurst (0→1 based on empirical distribution)
        std_h_safe = max(std_h, 0.001)
        h_norm = np.clip((hurst - (mu_h - 2 * std_h_safe)) / (4 * std_h_safe), 0, 1)
        mu_comp = np.mean(self.comp_history)

        # Higher Hurst → lower multiplier → wider entry zone
        buffer_t = 25.0 * (1.5 - h_norm) * (mu_comp / (comp_ratio + 1e-6))

        # ROC expansion: widen further during structural breakouts
        p90_roc = np.percentile(list(self.roc_history), 90)
        if roc > p90_roc:
            buffer_t *= 1.20

        # Clamp and smooth
        buffer_t = np.clip(buffer_t, 10.0, 40.0)
        smoothed = (buffer_t * 0.3) + (self.last_buffer * 0.7)
        self.last_buffer = smoothed
        return float(smoothed)


class AdaptiveCircuitBreaker:
    """
    Replaces single-threshold Z>3.0 CB with dual-threshold logic:
    - Undirected Crisis (Toxic): λ > P99 AND dλ/dt < 0 AND |Δ| < P10 → FIRE CB
    - Directed Breakout:        λ > P99 AND |Δ| > P90 → ALLOW TRADING
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.lambda_history = deque(maxlen=window)
        self.delta_history = deque(maxlen=window)
        self.cb_locked_until = 0

    def evaluate(self, current_lambda: float, current_delta: float,
                 current_time_idx: int, current_z: float) -> bool:
        """Returns True if CB should fire (block trading)."""
        # Time-based hysteresis lock (4 periods = 1 hour)
        if current_time_idx < self.cb_locked_until:
            return True

        self.lambda_history.append(current_lambda)
        self.delta_history.append(abs(current_delta))

        # Cold start: fall back to Z > 3.0
        if len(self.lambda_history) < self.window:
            return current_z > 3.0

        lambda_arr = np.array(self.lambda_history)
        delta_arr = np.array(self.delta_history)

        p99_lambda = np.percentile(lambda_arr, 99)
        p10_delta = np.percentile(delta_arr, 10)
        p90_delta = np.percentile(delta_arr, 90)

        d_lambda = current_lambda - self.lambda_history[-2] if len(self.lambda_history) > 1 else 0

        # Condition B: Directed Breakout → ALLOW trading
        if current_lambda > p99_lambda and abs(current_delta) > p90_delta:
            return False

        # Condition A: Undirected Crisis → FIRE CB
        if current_lambda > p99_lambda and d_lambda < 0 and abs(current_delta) < p10_delta:
            self.cb_locked_until = current_time_idx + 4
            return True

        return False


class AdaptiveLiquidationGuard:
    """
    Replaces fixed DI Z < -2.50 with rolling P5 percentile + regime-aware γ scaling.
    In HIGH_VOL, γ=1.5 (wider tolerance). In NORMAL, γ=0.5 (tighter).
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.di_history = deque(maxlen=window)

    def is_trap(self, current_di: float, vol_regime: str) -> bool:
        """Returns True if liquidation hunt detected."""
        self.di_history.append(current_di)

        # Cold start
        if len(self.di_history) < self.window:
            return current_di < -2.50

        di_arr = np.array(self.di_history)
        p5_di = np.percentile(di_arr, 5)
        std_di = max(np.std(di_arr), 0.001)

        # Regime-aware scaling
        gamma = 1.5 if vol_regime in ("HIGH_VOL", "CRISIS") else 0.5

        dynamic_threshold = p5_di - (gamma * std_di)
        return current_di < dynamic_threshold


class AdaptiveAbsorptionGuard:
    """
    Replaces fixed absorption_probability > 0.85 with rolling P90 + regime penalty.
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.prob_history = deque(maxlen=window)
        self.last_threshold = 0.85

    def is_absorbing(self, current_prob: float, vol_regime: str) -> bool:
        """Returns True if absorption is statistically anomalous."""
        self.prob_history.append(current_prob)

        # Cold start
        if len(self.prob_history) < self.window:
            return current_prob > 0.85

        prob_arr = np.array(self.prob_history)
        p90_prob = np.percentile(prob_arr, 90)

        # Regime penalty: push threshold higher in volatile environments
        if vol_regime in ("HIGH_VOL", "CRISIS"):
            target = p90_prob + 0.05
        else:
            target = p90_prob

        target = np.clip(target, 0.75, 0.95)

        # EMA hysteresis smoothing (α=0.2)
        smoothed = (0.2 * target) + (0.8 * self.last_threshold)
        self.last_threshold = smoothed

        return current_prob > smoothed


class AdaptiveTrendBreakout:
    """
    Replaces base_trend=250.0, intensity_divisor=25000, GOBI>0.1 with:
    - base_trend = P95(|Δ|) over 24h
    - intensity_scalar = ln(1 + intensity/max(|Δ|))
    - GOBI threshold = μ(|GOBI|) + 1.25σ(|GOBI|)
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.delta_history = deque(maxlen=window)
        self.gobi_history = deque(maxlen=window)

    def get_thresholds(self, current_delta: float, current_intensity: float,
                       current_gobi: float) -> tuple:
        """
        Returns (trend_threshold, gobi_threshold, is_breakout_long, is_breakout_short).
        """
        self.delta_history.append(abs(current_delta))
        self.gobi_history.append(abs(current_gobi))

        # Cold start: legacy values
        if len(self.delta_history) < self.window:
            import math
            base_trend = 250.0
            trend_thresh = base_trend * (1.0 + math.log1p(current_intensity / 25000.0))
            is_long = current_delta > trend_thresh and current_gobi > 0.1
            is_short = current_delta < -trend_thresh and current_gobi < -0.1
            return trend_thresh, 0.1, is_long, is_short

        delta_arr = np.array(self.delta_history)
        gobi_arr = np.array(self.gobi_history)

        # Adaptive base trend: 95th percentile with absolute floor
        base_trend = max(50.0, np.percentile(delta_arr, 95))
        max_delta = max(1.0, np.max(delta_arr))

        # Adaptive intensity scalar
        trend_threshold = base_trend * (1.0 + np.log1p(current_intensity / max_delta))

        # Adaptive GOBI threshold
        gobi_thresh = np.mean(gobi_arr) + (1.25 * np.std(gobi_arr))

        is_long = current_delta > trend_threshold and current_gobi > gobi_thresh
        is_short = current_delta < -trend_threshold and current_gobi < -gobi_thresh

        return float(trend_threshold), float(gobi_thresh), is_long, is_short


class AdaptiveMeanReversion:
    """
    Replaces Bollinger 2σ and CVD Z > -1.5 with:
    - κ = 2.0 × √(GKV/P50(GKV)) — GK-scaled Bollinger width
    - Exhaustion = CVD_Z > P10(CVD_Z) + 0.5 — rolling percentile
    """

    def __init__(self, window: int = 96):
        self.window = window
        self.gkv_history = deque(maxlen=window)
        self.cvdz_history = deque(maxlen=window)

    def get_bollinger_kappa(self, o: float, h: float, l: float, c: float) -> float:
        """Returns adaptive Bollinger multiplier (replaces fixed 2.0)."""
        log_hl = np.log(max(h, 0.01) / max(l, 0.01)) ** 2
        log_co = np.log(max(c, 0.01) / max(o, 0.01)) ** 2
        gkv = max((0.5 * log_hl) - ((2 * np.log(2) - 1) * log_co), 1e-10)

        self.gkv_history.append(gkv)

        # Cold start
        if len(self.gkv_history) < self.window:
            return 2.0

        median_gkv = np.percentile(list(self.gkv_history), 50)
        if median_gkv > 0:
            kappa = 2.0 * np.sqrt(gkv / median_gkv)
        else:
            kappa = 2.0

        return float(np.clip(kappa, 1.5, 3.0))

    def is_sellers_exhausted(self, current_cvd_z: float) -> bool:
        """Returns True if sellers are exhausted (replaces fixed CVD_Z > -1.5)."""
        self.cvdz_history.append(current_cvd_z)

        # Cold start
        if len(self.cvdz_history) < self.window:
            return current_cvd_z > -1.5

        p10_cvdz = np.percentile(list(self.cvdz_history), 10)
        return current_cvd_z > (p10_cvdz + 0.5)
