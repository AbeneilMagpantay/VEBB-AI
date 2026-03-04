"""
VEBB-AI: Self-Calibrating Threshold Framework (Phase 109 + Phase 111)

Replaces all hardcoded numerical thresholds with percentile-based,
rolling, regime-aware alternatives.

Every constant is derived from observable, rolling market statistics.
No developer intuition. No fixed magic numbers.

Derived from Deep Research: "Adaptive Thresholds for BTC HFT"
"""

import numpy as np
import math
import bisect
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


# =============================================================================
# Phase 111: Self-Calibrating Predator Filter Thresholds
# Eliminates all hardcoded constants from Phase 110's Context-Aware
# Metric Substitution. Derived from Deep Research: "Self-Calibrating
# Predator Filter Thresholds"
# =============================================================================


class AdaptiveHawkesEWMA:
    """
    Phase 111: Replaces fixed gamma=0.05 EWMA with Kaufman Efficiency
    Ratio-driven adaptive smoothing. Responds instantly to clean breakouts
    (ER→1, gamma→0.44) and suppresses noise during chop (ER→0, gamma→0.004).
    """

    def __init__(self, n: int = 10, fast_len: int = 2, slow_len: int = 30):
        self.n = n
        self.sc_fast = 2.0 / (fast_len + 1.0)
        self.sc_slow = 2.0 / (slow_len + 1.0)
        self.lambda_history = deque(maxlen=n + 1)
        self._hawkes_ewma_mu = 0.0
        self._hawkes_ewma_var = 0.0
        self._initialized = False

    def update(self, h_lambda: float) -> float:
        """Update EWMA with adaptive gamma, returns current Hawkes Z-score."""
        self.lambda_history.append(h_lambda)

        # Calculate adaptive gamma via Kaufman Efficiency Ratio
        if len(self.lambda_history) < self.n + 1:
            gamma = 0.05  # Cold-start fallback
        else:
            directional_change = abs(self.lambda_history[-1] - self.lambda_history[0])
            volatility = sum(abs(self.lambda_history[i] - self.lambda_history[i - 1])
                             for i in range(1, len(self.lambda_history)))
            er = directional_change / volatility if volatility != 0.0 else 0.0
            gamma = (er * (self.sc_fast - self.sc_slow) + self.sc_slow) ** 2

        # Initialize or update EWMA
        if not self._initialized:
            self._hawkes_ewma_mu = h_lambda
            self._hawkes_ewma_var = max((h_lambda * 0.1) ** 2, 1.0)
            self._initialized = True
        else:
            self._hawkes_ewma_mu = gamma * h_lambda + (1 - gamma) * self._hawkes_ewma_mu
            self._hawkes_ewma_var = gamma * ((h_lambda - self._hawkes_ewma_mu) ** 2) + \
                                    (1 - gamma) * self._hawkes_ewma_var

        sigma = max(self._hawkes_ewma_var ** 0.5, 0.01)
        hawkes_z = (h_lambda - self._hawkes_ewma_mu) / sigma
        return float(hawkes_z)

    def get_state(self) -> dict:
        return {
            "lambda_history": list(self.lambda_history),
            "mu": self._hawkes_ewma_mu,
            "var": self._hawkes_ewma_var,
            "initialized": self._initialized
        }

    def set_state(self, state: dict):
        self.lambda_history = deque(state.get("lambda_history", []), maxlen=self.n + 1)
        self._hawkes_ewma_mu = state.get("mu", 0.0)
        self._hawkes_ewma_var = state.get("var", 0.0)
        self._initialized = state.get("initialized", False)


class AdaptiveBreakoutDetector:
    """
    Phase 111: Replaces fixed hawkes_z > 2.0 with rolling P95 of
    Z-score history. Heavy-tailed distributions need percentile-based
    thresholds, not Gaussian assumptions.
    """

    def __init__(self, buffer_size: int = 96, target_percentile: int = 95):
        self.buffer_size = buffer_size
        self.target_percentile = target_percentile
        self.z_buffer = deque(maxlen=buffer_size)

    def evaluate_and_update(self, current_hawkes_z: float, abs_delta: float,
                            delta_p90: float, is_htf_aligned: bool) -> tuple:
        """
        Causal evaluation then state update.
        Returns (is_breakout_regime: bool, dynamic_threshold: float).
        """
        # 1. Causal: threshold from past data only
        if len(self.z_buffer) < self.buffer_size:
            dynamic_threshold = 2.0  # Cold-start Gaussian fallback
        else:
            dynamic_threshold = float(np.percentile(self.z_buffer, self.target_percentile))

        is_breakout = (current_hawkes_z > dynamic_threshold) and \
                      (abs_delta > delta_p90) and \
                      is_htf_aligned

        # 2. State update: append AFTER evaluation
        self.z_buffer.append(current_hawkes_z)

        return is_breakout, dynamic_threshold

    def get_state(self) -> dict:
        return {"z_buffer": list(self.z_buffer)}

    def set_state(self, state: dict):
        self.z_buffer = deque(state.get("z_buffer", []), maxlen=self.buffer_size)


class AdaptiveTFIThreshold:
    """
    Phase 111: Replaces fixed TFI > 0.40 with rolling P80 of |TFI|.
    Automatically tightens during thin Asian sessions (high TFI variance)
    and relaxes during heavy US sessions (compressed TFI variance).
    """

    def __init__(self, buffer_size: int = 96, target_percentile: int = 80):
        self.buffer_size = buffer_size
        self.target_percentile = target_percentile
        self.abs_tfi_buffer = deque(maxlen=buffer_size)

    def evaluate_and_update(self, current_tfi: float) -> tuple:
        """
        Causal evaluation then state update.
        Returns (is_tfi_valid: bool, dynamic_threshold: float).
        """
        abs_tfi = abs(current_tfi)

        # 1. Causal: threshold from past data only
        if len(self.abs_tfi_buffer) < self.buffer_size:
            dynamic_threshold = 0.40  # Cold-start fallback
        else:
            dynamic_threshold = float(np.percentile(self.abs_tfi_buffer, self.target_percentile))

        is_valid = abs_tfi >= dynamic_threshold

        # 2. State update
        self.abs_tfi_buffer.append(abs_tfi)

        return is_valid, dynamic_threshold

    def get_state(self) -> dict:
        return {"abs_tfi_buffer": list(self.abs_tfi_buffer)}

    def set_state(self, state: dict):
        self.abs_tfi_buffer = deque(state.get("abs_tfi_buffer", []), maxlen=self.buffer_size)


class AdaptiveSigmoidCalibration:
    """
    Phase 111: Replaces hardcoded OBI_MIN=0.20, OBI_MAX=0.80, K=1.5,
    L_MID=2.5 with empirically derived values from rolling distributions.
    The sigmoid curve reshapes itself to match current market topology.
    """

    def __init__(self, buffer_size: int = 96):
        self.buffer_size = buffer_size
        self.obi_buffer = deque(maxlen=buffer_size)
        self.z_buffer = deque(maxlen=buffer_size)

    def get_adaptive_threshold(self, current_hawkes_z: float, current_obi: float,
                               rv_mult: float = 1.0, hurst_mult: float = 1.0,
                               abs_delta: float = 0.0) -> float:
        """
        Causal sigmoid evaluation with self-calibrating parameters.
        Returns the OBI threshold for non-breakout regimes.
        Cold-start: falls back to the old delta-based formula (no hardcoded sigmoid params).
        """
        # 1. Causal: derive parameters from past data only
        if len(self.obi_buffer) < self.buffer_size or len(self.z_buffer) < self.buffer_size:
            # Cold-start fallback: use the proven delta-based formula from Phase 110
            # This naturally scales: high delta → low threshold, low delta → high threshold
            threshold = 0.85 / (1.0 + (abs_delta / 60.0))
            threshold *= rv_mult * hurst_mult
            threshold = max(0.05, min(threshold, 0.80))
            self.obi_buffer.append(abs(current_obi))
            self.z_buffer.append(current_hawkes_z)
            return float(threshold)

        # Warmed up: use self-calibrating sigmoid
        obi_arr = np.array(self.obi_buffer)
        z_arr = np.array(self.z_buffer)

        obi_max = float(np.percentile(obi_arr, 90))
        obi_min = float(np.percentile(obi_arr, 20))

        l_mid = float(np.percentile(z_arr, 75))
        z_85 = float(np.percentile(z_arr, 85))
        z_65 = float(np.percentile(z_arr, 65))

        delta_z = max(z_85 - z_65, 0.01)
        k = math.log(9) / delta_z

        # Ensure min < max
        obi_min = min(obi_min, obi_max - 0.05)

        # Sigmoid: maps Z-score to [0, 1]
        exp_val = min(k * (current_hawkes_z - l_mid), 500)  # Overflow protection
        sigmoid_val = 1.0 / (1.0 + math.exp(-exp_val))

        # High Z → low threshold (sigmoid→1 → threshold drops toward OBI_MIN)
        threshold = obi_max - (obi_max - obi_min) * sigmoid_val

        # Apply multipliers
        threshold *= rv_mult * hurst_mult
        threshold = max(0.05, min(threshold, 0.80))

        # 2. State update
        self.obi_buffer.append(abs(current_obi))
        self.z_buffer.append(current_hawkes_z)

        return float(threshold)

    def get_state(self) -> dict:
        return {
            "obi_buffer": list(self.obi_buffer),
            "z_buffer": list(self.z_buffer)
        }

    def set_state(self, state: dict):
        self.obi_buffer = deque(state.get("obi_buffer", []), maxlen=self.buffer_size)
        self.z_buffer = deque(state.get("z_buffer", []), maxlen=self.buffer_size)


class AdaptiveMultipliers:
    """
    Phase 111: Replaces rv*10.0 and hurst-0.5 with ECDF percentile rank.
    Maps unbounded metrics to bounded [0.5x, 1.5x] multipliers via
    empirical cumulative distribution function.
    """

    def __init__(self, buffer_size: int = 96):
        self.buffer_size = buffer_size
        self.rv_sorted = []
        self.hurst_sorted = []
        self.rv_queue = deque(maxlen=buffer_size)
        self.hurst_queue = deque(maxlen=buffer_size)

    def _get_rank(self, sorted_list: list, value: float) -> float:
        if not sorted_list:
            return 0.5
        idx = bisect.bisect_left(sorted_list, value)
        return idx / len(sorted_list)

    def _update_buffer(self, sorted_list: list, queue: deque, value: float):
        if len(queue) == self.buffer_size:
            old_val = queue[0]  # Will be popped by deque maxlen
            old_idx = bisect.bisect_left(sorted_list, old_val)
            if old_idx < len(sorted_list) and sorted_list[old_idx] == old_val:
                del sorted_list[old_idx]
        queue.append(value)
        bisect.insort(sorted_list, value)

    def evaluate_and_update(self, current_rv: float, current_hurst: float) -> tuple:
        """
        Causal evaluation then state update.
        Returns (rv_mult: float, hurst_mult: float).
        """
        # 1. Causal: rank against past data only
        if len(self.rv_queue) < self.buffer_size:
            rv_mult = 1.0
            hurst_mult = 1.0
        else:
            rv_rank = self._get_rank(self.rv_sorted, current_rv)
            hurst_rank = self._get_rank(self.hurst_sorted, current_hurst)

            # Scale rank [0,1] to symmetric multiplier [0.5x, 1.5x]
            rv_mult = 1.0 + (rv_rank - 0.5)
            # Invert Hurst: low rank (choppy) → high penalty
            hurst_mult = 1.0 - (hurst_rank - 0.5)

        # 2. State update
        self._update_buffer(self.rv_sorted, self.rv_queue, current_rv)
        self._update_buffer(self.hurst_sorted, self.hurst_queue, current_hurst)

        return float(rv_mult), float(hurst_mult)

    def get_state(self) -> dict:
        return {
            "rv_queue": list(self.rv_queue),
            "hurst_queue": list(self.hurst_queue)
        }

    def set_state(self, state: dict):
        rv_data = state.get("rv_queue", [])
        hurst_data = state.get("hurst_queue", [])
        self.rv_queue = deque(maxlen=self.buffer_size)
        self.rv_sorted = []
        self.hurst_queue = deque(maxlen=self.buffer_size)
        self.hurst_sorted = []
        for v in rv_data:
            self._update_buffer(self.rv_sorted, self.rv_queue, v)
        for v in hurst_data:
            self._update_buffer(self.hurst_sorted, self.hurst_queue, v)


class AdaptiveCPR:
    """
    Phase 112: Replaces binary candle color (red/green) with continuous
    Close Position within Range: CPR = (Close - Low) / (High - Low).
    
    Three-tier evaluation:
    1. Delta P90 Override: extreme flow bypasses all color checks
    2. CPR > rolling P50: candle closed in upper half → structurally strong
    3. Baseline: if both fail AND candle is red, BLOCK (ranging protection)
    """

    def __init__(self, buffer_size: int = 96):
        self.buffer_size = buffer_size
        self.cpr_buffer = deque(maxlen=buffer_size)
        self.delta_buffer = deque(maxlen=buffer_size)

    def evaluate_and_update(self, direction: str, o: float, h: float, l: float, c: float,
                            delta: float, hawkes_z: float, is_pre_emptive: bool = False) -> tuple:
        """
        Causal evaluation then state update.
        Returns (should_allow: bool, reason: str, cpr: float).
        """
        # PRE-EMPTIVE exhaustion fades always pass
        if is_pre_emptive:
            return True, "PRE-EMPTIVE bypass", 0.5

        # Calculate CPR
        candle_range = h - l
        cpr = (c - l) / candle_range if candle_range > 0 else 0.5

        is_red = c < o
        is_green = c > o

        # 1. Causal: thresholds from past data only
        if len(self.cpr_buffer) < self.buffer_size:
            cpr_threshold = 0.50  # Cold-start default: median
            delta_p90 = 500.0  # Cold-start default
        else:
            cpr_threshold = float(np.percentile(self.cpr_buffer, 50))
            delta_p90 = float(np.percentile(self.delta_buffer, 90))

        should_allow = True
        reason = "passed"

        if direction == "LONG":
            if is_red:
                # Override Alpha: extreme bullish flow → bypass color
                if delta > 0 and delta >= delta_p90 and hawkes_z > 2.0:
                    reason = f"DELTA OVERRIDE (Δ={delta:.0f}>P90={delta_p90:.0f}, Hz={hawkes_z:.1f})"
                # Override Beta: CPR shows strength despite red
                elif cpr >= cpr_threshold:
                    reason = f"CPR OVERRIDE ({cpr:.2f}>P50={cpr_threshold:.2f})"
                else:
                    should_allow = False
                    reason = f"RED candle, CPR {cpr:.2f}<{cpr_threshold:.2f}, Δ={delta:.0f}<P90={delta_p90:.0f}"

        elif direction == "SHORT":
            if is_green:
                # Override Alpha: extreme bearish flow → bypass color
                if delta < 0 and abs(delta) >= delta_p90 and hawkes_z > 2.0:
                    reason = f"DELTA OVERRIDE (Δ={delta:.0f}, |Δ|>P90={delta_p90:.0f}, Hz={hawkes_z:.1f})"
                # Override Beta: CPR shows weakness despite green
                elif cpr <= (1.0 - cpr_threshold):
                    reason = f"CPR OVERRIDE ({cpr:.2f}<{1.0-cpr_threshold:.2f})"
                else:
                    should_allow = False
                    reason = f"GREEN candle, CPR {cpr:.2f}>{1.0-cpr_threshold:.2f}, |Δ|={abs(delta):.0f}<P90={delta_p90:.0f}"

        # 2. State update: ALWAYS feed buffers (every candle in main loop)
        self.cpr_buffer.append(cpr)
        self.delta_buffer.append(abs(delta))

        return should_allow, reason, cpr

    def get_state(self) -> dict:
        return {
            "cpr_buffer": list(self.cpr_buffer),
            "delta_buffer": list(self.delta_buffer)
        }

    def set_state(self, state: dict):
        self.cpr_buffer = deque(state.get("cpr_buffer", []), maxlen=self.buffer_size)
        self.delta_buffer = deque(state.get("delta_buffer", []), maxlen=self.buffer_size)
