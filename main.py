"""
VEBB-AI: Main Orchestrator
Ties all modules together for live trading.
"""

import csv
import asyncio
import os
import signal
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from collections import deque

from data_stream import DataStream, calculate_garman_klass, calculate_log_return
from regime_detector import RegimeDetector
from gemini_analyst import GeminiAnalyst, TradeAction, ExitAction, TradeSignal
from position_manager import PositionManager, Side
from exchange_client import ExchangeClient, OrderSide
from order_flow import FootprintBuilder, VolumeProfile
from order_book import OrderBookBuilder
from market_context import MarketContextFetcher
from multi_timeframe import MultiTimeframeFetcher, TrendDirection
from microstructure import MicrostructureEngine
from dynamic_tp_sl import DynamicExitFramework
from adaptive_thresholds import (AdaptiveRegimeDetector as AdaptiveRegime, AdaptiveSniperBuffer,
    AdaptiveCircuitBreaker, AdaptiveLiquidationGuard, AdaptiveAbsorptionGuard,
    AdaptiveTrendBreakout, AdaptiveMeanReversion,
    AdaptiveHawkesEWMA, AdaptiveBreakoutDetector, AdaptiveTFIThreshold,
    AdaptiveSigmoidCalibration, AdaptiveMultipliers, AdaptiveCPR)
from sentinel_detector import SentinelLeadLagDetector
from liquidity_magnet import LiquidationMagnetDetector
from volatility_tp import DynamicVolatilityEngine
from chart_memory import SupplyDemandMapper  # Phase 60: Memory Module
from delta_threshold import DynamicDeltaThreshold  # Phase 66: Dynamic Delta Gate
from vwap_engine import InstitutionalVWAPEngine # Phase 72: Advanced O(1) Institutional VWAP Engine
from trade_logger import get_logger
from shm_bridge import SHMReader # Phase 77: The Nervous System Bridge
from control_bridge import ControlBridge # Phase 81c/82: Cognitive Control

load_dotenv(override=True) # Forces .env to overwrite existing terminal session variables

def get_window_size(timeframe: str, target_hours: int) -> int:
    """Calculates the number of candles required to cover target_hours for a given timeframe."""
    import re
    match = re.match(r'(\d+)([a-zA-Z]+)', timeframe.lower())
    if not match:
        return int(target_hours * 60 / 15) # Default to 15m intervals
        
    val = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'm':
        tf_minutes = val
    elif unit == 'h':
        tf_minutes = val * 60
    elif unit in ['d', 'w']:
        tf_minutes = val * 60 * 24
    else:
        tf_minutes = 15
        
    return max(5, int((target_hours * 60) / max(1, tf_minutes)))

# Phase 91: High-Frequency O(1) Computational Veto utilizing Numba byte-compilation
from numba import float64, boolean
from numba.experimental import jitclass
import numba as nb
import math

# Define the precise C-level memory structures to dodge Python object overhead
spec = [
    ('alpha', float64),
    ('mean', float64),
    ('variance', float64),
    ('gamma', float64),
    ('kappa', float64),
    ('initialized', boolean)
]

@jitclass(spec)
class DynamicVolumeVeto:
    def __init__(self, half_life_ticks: float, gamma: float = 0.5, kappa: float = 1.2):
        self.alpha = 1.0 - math.exp(-math.log(2.0) / half_life_ticks)
        self.mean = 0.0
        self.variance = 0.0
        self.gamma = gamma
        self.kappa = kappa
        self.initialized = False

    def check_execution_safety(self, 
                               current_volume_delta: float, 
                               hmm_base_limit: float, 
                               obi: float, 
                               norm_kyle_lambda: float, 
                               intended_side: int) -> bool:
        """
        Updates the EWMV in O(1) time using Welford's algorithm
        and evaluates the dynamic veto logic against microstructure parameters.
        intended_side: 1 (LONG), -1 (SHORT)
        Returns: True if VETO triggered (do not execute), False if SAFE.
        """
        if not self.initialized:
            self.mean = current_volume_delta
            self.variance = 0.0
            self.initialized = True
            return False
            
        diff = current_volume_delta - self.mean
        self.mean = self.mean + self.alpha * diff
        self.variance = (1.0 - self.alpha) * (self.variance + self.alpha * diff * (current_volume_delta - self.mean))
        
        std_dev = math.sqrt(self.variance) + 1e-8
        current_z_score = (current_volume_delta - self.mean) / std_dev
        
        lambda_penalty = math.exp(-self.kappa * norm_kyle_lambda)
        
        if intended_side == 1:
            veto_limit = hmm_base_limit * (1.0 - self.gamma * obi) * lambda_penalty
            return current_z_score > veto_limit
            
        elif intended_side == -1:
            veto_limit = -hmm_base_limit * (1.0 + self.gamma * obi) * lambda_penalty
            return current_z_score < veto_limit
            
        return False

# Phase 92: Dynamic Global Divergence Index (DI Guard)
from numba import int32
di_spec = [
    ('alpha', float64),
    ('mean', float64),
    ('variance', float64),
    ('initialized', boolean),
    ('tick_count', int32),
    ('burn_in_period', int32)
]

@jitclass(di_spec)
class DynamicDIGuard:
    def __init__(self, lookback_periods: int):
        self.alpha = 2.0 / (lookback_periods + 1.0)
        self.mean = 0.0
        self.variance = 0.0
        self.initialized = False
        self.tick_count = 0
        self.burn_in_period = int(1.0 / self.alpha)

    def evaluate_di_trap(self, current_di: float, prob_chop: float, prob_trend: float, prob_crash: float) -> tuple:
        """
        Updates baseline metrics and evaluates if current DI constitutes a structural trap.
        Output: (z_score, dynamic_threshold, is_trap, is_absorption)
        """
        if not self.initialized:
            self.mean = current_di
            self.variance = 0.0
            self.initialized = True
            self.tick_count += 1
            return 0.0, 2.5, False, False

        self.tick_count += 1
        delta = current_di - self.mean
        self.mean += self.alpha * delta
        
        # Finch 2009 stable EWMV derivation
        self.variance = (1.0 - self.alpha) * (self.variance + self.alpha * (delta ** 2))
        
        # Clamp to 1e-8 to prevent Division by Zero
        std_dev = max(math.sqrt(self.variance), 1e-8)
        z_score = (current_di - self.mean) / std_dev
        
        # Dynamic HMM threshold blending
        t_chop = prob_chop * 1.5
        t_trend = prob_trend * 2.5
        t_crash = prob_crash * 3.8
        dynamic_threshold = t_chop + t_trend + t_crash
        
        # Initialize gracefully
        if self.tick_count < self.burn_in_period:
            dynamic_threshold = 2.5
            
        is_trap = z_score < -dynamic_threshold
        is_absorption = z_score > dynamic_threshold
        
        return z_score, dynamic_threshold, is_trap, is_absorption

# Phase 96: Dynamic Liquidation Squeeze Filters
spec_liquidation_guard = [
    ('alpha_time', float64),
    ('alpha_event', float64),
    ('base_z_threshold', float64),
    ('gamma_oi', float64),
    ('beta_gobi', float64),
    ('theta_opp', float64),
    ('min_variance', float64),
    ('mean_liq', float64),
    ('var_liq', float64),
]

@jitclass(spec_liquidation_guard)
class DynamicLiquidationGuard(object):
    def __init__(self, alpha_time: float64, alpha_event: float64, base_z: float64, gamma_oi: float64, beta_gobi: float64, theta_opp: float64, min_variance: float64):
        self.alpha_time = alpha_time
        self.alpha_event = alpha_event
        self.base_z_threshold = base_z
        self.gamma_oi = gamma_oi
        self.beta_gobi = beta_gobi
        self.theta_opp = theta_opp
        self.min_variance = min_variance
        
        self.mean_liq = 0.0
        self.var_liq = min_variance

    def update(self, primary_liq: float64) -> None:
        if primary_liq <= 1e-6:
            self.mean_liq = (1.0 - self.alpha_time) * self.mean_liq
        else:
            delta = primary_liq - self.mean_liq
            self.mean_liq = (1.0 - self.alpha_event) * self.mean_liq + (self.alpha_event * primary_liq)
            self.var_liq = (1.0 - self.alpha_event) * self.var_liq + (self.alpha_event * delta * (primary_liq - self.mean_liq))
            
            if self.var_liq < self.min_variance:
                self.var_liq = self.min_variance

    def evaluate(self, primary_liq: float64, opposite_liq: float64, global_oi: float64, gobi: float64, is_long_squeeze: boolean) -> boolean:
        # If there's 0 liquidations, there is no squeeze
        if primary_liq <= 1e-6:
            return False
            
        std_dev = math.sqrt(self.var_liq)
        current_z = (primary_liq - self.mean_liq) / std_dev
        
        oi_safe = global_oi if global_oi > 1.0 else 1.0 
        oi_ratio = primary_liq / oi_safe
        oi_multiplier = math.exp(-self.gamma_oi * oi_ratio)
        
        direction = 1.0 if is_long_squeeze else -1.0
        gobi_modifier = 1.0 - (direction * self.beta_gobi * gobi)
        
        dynamic_z_threshold = self.base_z_threshold * oi_multiplier * gobi_modifier
        opp_threshold_met = opposite_liq > (self.mean_liq * self.theta_opp)

        return (current_z >= dynamic_z_threshold) and opp_threshold_met

# Phase 95: Dynamic Lead-Lag Theta Alignment Specification
spec_theta_alignment = [
    ('alpha', float64),
    ('mu', float64),
    ('S', float64),
    ('variance', float64),
    ('initialized', boolean),
    ('tau_1', float64),
    ('tau_2', float64),
    ('tau_3', float64),
    ('rho', float64),
    ('current_gamma_long', float64),
    ('current_gamma_short', float64)
]

@jitclass(spec_theta_alignment)
class DynamicThetaAlignment(object):
    def __init__(self, alpha: float64, tau_1: float64, tau_2: float64, tau_3: float64, rho: float64):
        self.alpha = alpha
        self.mu = 0.0
        self.S = 0.0
        self.variance = 0.0
        self.initialized = False
        self.tau_1 = tau_1
        self.tau_2 = tau_2
        self.tau_3 = tau_3
        self.rho = rho
        self.current_gamma_long = 0.0
        self.current_gamma_short = 0.0

    def _update_variance(self, theta: float64) -> float64:
        if not self.initialized:
            self.mu = theta
            self.S = 0.0
            self.variance = 0.0
            self.initialized = True
            return 1e-6
            
        delta = theta - self.mu
        self.mu = self.mu + (1.0 - self.alpha) * delta
        self.S = self.alpha * self.S + (1.0 - self.alpha) * delta * (theta - self.mu)
        self.variance = self.S
        
        if self.variance < 1e-6:
            return 1e-6
            
        return math.sqrt(self.variance)

    def update_and_evaluate(self, theta: float64, gobi: float64, prob_s1: float64, prob_s2: float64, prob_s3: float64) -> None:
        current_std = self._update_variance(theta)
        m_hmm = (prob_s1 * self.tau_1) + (prob_s2 * self.tau_2) + (prob_s3 * self.tau_3)
        base_boundary = m_hmm * current_std
        
        self.current_gamma_long = base_boundary * math.exp(-self.rho * gobi)
        self.current_gamma_short = base_boundary * math.exp(self.rho * gobi)

# Phase 97: Dynamic Regime Confidence Scaling
@nb.njit(fastmath=True, cache=True)
def _calculate_dynamic_threshold(
    c_base: float, 
    p_norm: float, p_cb: float, p_crisis: float,
    d_norm: float, d_cb: float, d_crisis: float,
    hawkes_intensity: float,
    h_base: float, h_scale: float, kappa: float
) -> float:
    # Calculate the expected penalty (continuous HMM state mapping)
    c_penalty = (p_norm * d_norm) + (p_cb * d_cb) + (p_crisis * d_crisis)
    
    # Calculate the microstructural kinetic multiplier with asymptotic exponential dampening
    if hawkes_intensity > h_base:
        # Utilizing fastmath np.exp for hardware-level accelerated transcendental math
        m_lambda = 1.0 + kappa * (1.0 - np.exp(-(hawkes_intensity - h_base) / h_scale))
    else:
        m_lambda = 1.0
        
    # Apply multiplier to the penalty and add to base confidence
    threshold = c_base + (c_penalty * m_lambda)
    
    # Hard boundary constraint to prevent execution paralysis (floating point float rounding safety)
    return threshold if threshold < 0.99 else 0.99

spec_confidence_scaler = [
    ('c_base', nb.float64),
    ('d_norm', nb.float64),
    ('d_cb', nb.float64),
    ('d_crisis', nb.float64),
    ('h_base', nb.float64),
    ('h_scale', nb.float64),
    ('kappa', nb.float64),
]

@jitclass(spec_confidence_scaler)
class DynamicConfidenceScaler:
    """
    Ultra-low latency struct managing the dynamic calibration of LLM execution thresholds.
    Integrates continuous HMM probability simplexes with Hawkes process intensities.
    """
    def __init__(self, c_base: float = 0.70):
        self.c_base = c_base
        
        # HMM Regime Continuous Penalties
        self.d_norm = 0.00
        self.d_cb = 0.10
        self.d_crisis = 0.15
        
        # Microstructural Kinetic Parameters
        self.h_base = 2000.0    # Baseline intensity (events/time) before penalty scaling begins
        self.h_scale = 20000.0  # Velocity scaling denominator governing the exponential decay
        self.kappa = 1.0        # Maximum inflation multiplier (1.0 = allows up to 100% inflation)

    def calculate_threshold(self, p_norm: float, p_cb: float, p_crisis: float, hawkes_intensity: float) -> float:
        """
        Constant-time evaluation mapping latent probabilities and market velocity to an explicit risk threshold.
        Target Execution Latency: ~150 nanoseconds.
        """
        return _calculate_dynamic_threshold(
            self.c_base, 
            p_norm, p_cb, p_crisis,
            self.d_norm, self.d_cb, self.d_crisis,
            hawkes_intensity, 
            self.h_base, self.h_scale, self.kappa
        )

    def validate_execution(self, action: int, theta: float64) -> boolean:
        if action == 1: # LONG
            if theta < self.current_gamma_long:
                return False 
        elif action == -1: # SHORT
            if theta > -self.current_gamma_short:
                return False
        return True

# Phase 93: Dynamic Flashpoint Intensity Floors
spec_hawkes_floor = [
    ('alpha', float64),
    ('mean', float64),
    ('var', float64),
    ('current_z', float64),
    ('is_initialized', boolean)
]

@jitclass(spec_hawkes_floor)
class DynamicHawkesFloor(object):
    def __init__(self, alpha: float64):
        self.alpha = alpha
        self.mean = 0.0
        self.var = 0.0
        self.current_z = 0.0
        self.is_initialized = False

    def update(self, intensity: float64) -> float64:
        if not self.is_initialized:
            self.mean = intensity
            self.var = 0.0
            self.current_z = 0.0
            self.is_initialized = True
            return 0.0

        prev_mean = self.mean
        self.mean = (1.0 - self.alpha) * prev_mean + self.alpha * intensity
        
        self.var = (1.0 - self.alpha) * self.var + self.alpha * (intensity - prev_mean) * (intensity - self.mean)
        
        if self.var < 1e-10:
            std_dev = 1e-5
        else:
            std_dev = math.sqrt(self.var)
            
        self.current_z = (intensity - self.mean) / std_dev
        return self.current_z

    def evaluate_execution_state(self, intensity: float64, is_crisis: boolean, did_ratio: float64) -> int:
        z = self.update(intensity)
        
        if z >= 4.5 and did_ratio >= 0.002:
            return 3  # Unrestricted Displacement
            
        if is_crisis:
            if z >= 3.5:
                return 2  # Crisis PoNR 
            else:
                return 0
                
        if z >= 2.5:
            return 1  # Standard PoNR
            
        return 0

# Phase 94: Dynamic Volume Exhaustion Guards
spec_exhaustion_guard = [
    ('alpha', float64),
    ('mu_Q', float64),
    ('var_Q', float64),
    ('mu_dP', float64),
    ('cov_dP_Q', float64),
    ('mu_lambda', float64),
    ('var_lambda', float64),
    ('initialized', float64),
]

@jitclass(spec_exhaustion_guard)
class DynamicExhaustionGuard(object):
    def __init__(self, span: int):
        self.alpha = 2.0 / (span + 1.0)
        
        self.mu_Q = 0.0
        self.var_Q = 1e-8
        
        self.mu_dP = 0.0
        self.cov_dP_Q = 0.0
        
        self.mu_lambda = 0.0
        self.var_lambda = 1e-8
        
        self.initialized = 0.0

    def update(self, delta: float64, price_change: float64) -> tuple:
        if self.initialized == 0.0:
            self.mu_Q = delta
            self.mu_dP = price_change
            self.mu_lambda = 0.0
            self.initialized = 1.0
            return (0.0, 0.0, 0.0)

        prev_mu_Q = self.mu_Q
        prev_mu_dP = self.mu_dP

        self.mu_Q = self.alpha * delta + (1.0 - self.alpha) * prev_mu_Q
        self.mu_dP = self.alpha * price_change + (1.0 - self.alpha) * prev_mu_dP

        self.var_Q = (1.0 - self.alpha) * (self.var_Q + self.alpha * (delta - prev_mu_Q)**2)
        self.cov_dP_Q = (1.0 - self.alpha) * (self.cov_dP_Q + self.alpha * (price_change - prev_mu_dP) * (delta - prev_mu_Q))

        current_lambda = self.cov_dP_Q / max(self.var_Q, 1e-12)

        prev_mu_lambda = self.mu_lambda
        self.mu_lambda = self.alpha * current_lambda + (1.0 - self.alpha) * prev_mu_lambda
        self.var_lambda = (1.0 - self.alpha) * (self.var_lambda + self.alpha * (current_lambda - prev_mu_lambda)**2)

        std_Q = math.sqrt(self.var_Q)
        std_lambda = math.sqrt(self.var_lambda)
        
        Z_Q = (delta - self.mu_Q) / max(std_Q, 1e-12)
        Z_lambda = (current_lambda - self.mu_lambda) / max(std_lambda, 1e-12)

        return (Z_Q, Z_lambda, current_lambda)

    def is_exhaustion_climax(self, Z_Q: float64, Z_lambda: float64, z_q_thresh: float64, z_lam_thresh: float64) -> boolean:
        is_volume_anomaly = abs(Z_Q) > z_q_thresh
        is_absorption = Z_lambda < z_lam_thresh
        return is_volume_anomaly and is_absorption

class TradingBot:
    """
    Main trading bot orchestrator.
    
    Flow:
    1. Receive candle from WebSocket
    2. Calculate features → HMM regime
    3. If not CRISIS → Ask Gemini for trade decision
    4. If confidence > threshold → Execute via Exchange
    5. Monitor position for SL/TP
    """
    
    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        
        # Configuration
        self.timeframe = os.getenv("TIMEFRAME", "15m")
        self.confidence_threshold = float(os.getenv("CONFIDENCE_THRESHOLD", 0.7))
        
        # Lookback Window based on 2026 'Volatility Compression' Research (48h)
        # Phase 73: Enforce a rigid mathematical floor of 250 candles so the 200 EMA always converges
        self.lookback_candles = max(250, get_window_size(self.timeframe, 48))
        
        # Initialize modules with timeframe
        self.data_stream = DataStream(testnet=testnet, interval=self.timeframe)
        self.regime_detector = RegimeDetector(window_size=get_window_size(self.timeframe, 24))
        self.gemini = GeminiAnalyst()
        self.position_manager = PositionManager(
            initial_capital=float(os.getenv("INITIAL_CAPITAL", 100)),
            leverage=int(os.getenv("LEVERAGE", 1)),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", 0.2)),
            daily_loss_limit_pct=float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.10)),
            fixed_margin=float(os.getenv("RISK_PER_TRADE", 0.0)), # Phase 48: Fixed Margin Risk
            state_file=f"trading_state_{self.timeframe}.json" # Unique state per timeframe
        )
        self.exchange = ExchangeClient(testnet=testnet)
        self.multi_tf = MultiTimeframeFetcher(testnet=testnet)
        
        # Phase 78c Unity: Single-Stream Footprint Feeding
        self.footprint = FootprintBuilder(testnet=testnet)
        self.data_stream.on_agg_trade = self.footprint._process_trade
        
        # Phase 91 Dynamic Z-Score Volume Imbalance Engine
        self.dynamic_volume_veto = DynamicVolumeVeto(half_life_ticks=100.0)
        
        # Phase 92 Dynamic DI Guard Engine
        self.dynamic_di_guard = DynamicDIGuard(lookback_periods=100)
        
        # Phase 93 Dynamic Hawkes Intensity Floor
        self.dynamic_hawkes_floor = DynamicHawkesFloor(alpha=0.001)
        
        # Phase 77: The Nervous System (Shared Memory Bridge)
        self.shm_reader = SHMReader()
        self.control_bridge = ControlBridge()
        self.last_shm_seq = 0
        self.order_book = OrderBookBuilder()
        self.market_context = MarketContextFetcher(testnet=testnet)
        
        # Phase 64: Microstructure memory scales fractionally
        h24_window = get_window_size(self.timeframe, 24)
        ofi_window = max(5, get_window_size(self.timeframe, 2.5)) # 150 mins
        self.microstructure = MicrostructureEngine(timeframe=self.timeframe, h24_window=h24_window, ofi_window=ofi_window)
        
        self.sentinel = SentinelLeadLagDetector()
        self.magnet = LiquidationMagnetDetector()
        self.vol_tp = DynamicVolatilityEngine()
        self.chart_memory = SupplyDemandMapper(timeframe=self.timeframe, h24_window=h24_window)  # Phase 60: Chart Memory
        
        # Phase 66: Dynamic Delta Confirmation Threshold
        candle_seconds = 900 if self.timeframe == "15m" else (300 if self.timeframe == "5m" else 3600)
        self.delta_threshold = DynamicDeltaThreshold(candle_duration_s=candle_seconds, h24_window=h24_window)
        
        # Phase 94: Dynamic Volume Exhaustion / Climax Shield
        self.dynamic_exhaustion_guard = DynamicExhaustionGuard(span=5760) # ~24h at 15s updates
        
        # Phase 96: Dynamic Liquidation Squeeze Filters
        self.dynamic_liq_guard = DynamicLiquidationGuard(
            alpha_time=0.0001, alpha_event=0.1, base_z=3.5, 
            gamma_oi=100.0, beta_gobi=0.25, theta_opp=0.2, min_variance=10.0
        )
        
        # Phase 97: Dynamic Regime Confidence Scaling
        self.dynamic_confidence_scaler = DynamicConfidenceScaler(c_base=self.confidence_threshold)
        
        # Phase 64: Track Hawkes Rate for Z-Score climax detection
        self.hawkes_buffer = deque(maxlen=h24_window)
        
        # Phase 72: Advanced O(1) Institutional VWAP Engine (315-minute lookback)
        # Using 15,000 tick buffer, plenty for 315 minutes of live aggregates
        self.vwap_engine = InstitutionalVWAPEngine(max_window_size=15000)
        
        # Phase 43: HMM Policy
        self.relaxed_hmm = os.getenv("RELAXED_HMM", "False").lower() == "true"
        
        # State
        self.running = False
        self.candle_buffer = []
        self.prev_close = None
        self.current_signal = None
        self.entry_delta = 0.0  # Track delta at entry for logging
        self.entry_time = None  # Track entry time for duration
        self.prev_regime = None # Track previous regime for structural resets
        self.vol_buffer = []    # Track rolling volatility for dynamic TP (Phase 48)
        self.last_mid_candle_entry_ts = datetime.min
        self.last_gemini_poll_ts = datetime.min
        self.last_alpha_log_ts = datetime.min
        self.last_alpha_direction = None
        self.last_alpha_val = 0.0 # Tracking for hysteresis
        
        # Phase 79: Evolutionary Intelligence Parameters
        self.atr = 0.0 # 14-period ATR
        self.dynamic_exit = DynamicExitFramework()  # Phase 107: Regime-adaptive TP/SL
        self.hysteresis_multiplier = 1.0 # Default multi (scaled by Gemini)
        
        # Phase 109: Self-Calibrating Thresholds (replaces ALL hardcoded constants)
        self.adaptive_regime = AdaptiveRegime(window=96)
        self.adaptive_sniper_buffer = AdaptiveSniperBuffer(window=96)
        self.adaptive_cb = AdaptiveCircuitBreaker(window=96)
        self.adaptive_liq_guard = AdaptiveLiquidationGuard(window=96)
        self.adaptive_absorption = AdaptiveAbsorptionGuard(window=96)
        self.adaptive_trend = AdaptiveTrendBreakout(window=96)
        self.adaptive_mr = AdaptiveMeanReversion(window=96)
        # Phase 111: Self-Calibrating Predator Filter
        self.adaptive_hawkes = AdaptiveHawkesEWMA(n=10)
        self.adaptive_breakout = AdaptiveBreakoutDetector(buffer_size=96, target_percentile=95)
        self.adaptive_tfi = AdaptiveTFIThreshold(buffer_size=96, target_percentile=80)
        self.adaptive_sigmoid = AdaptiveSigmoidCalibration(buffer_size=96)
        self.adaptive_mults = AdaptiveMultipliers(buffer_size=96)
        self.adaptive_cpr = AdaptiveCPR(buffer_size=96)  # Phase 112: CPR + Delta Override
        self.candle_count = 0  # Running index for CB time-lock
        self.vol_floor = 2.0 # Minimum theta threshold (scaled by Gemini)
        self.last_macro_update_ts = datetime.min
        from gemini_analyst import MacroRegime
        self.current_macro_regime = MacroRegime(bias='NEUTRAL', hysteresis_multiplier=1.0, vol_floor=5.0, dynamic_sl_pct=0.015, reasoning='Booting')
        
        self.last_liquidation_v_1m = 0.0
        self.last_liquidation_v_1m = 0.0
        self.last_liquidation_side = "UNKNOWN"
        self.btc_ticks = deque() # Buffer for lead-lag ([(timestamp, price), ...])
        self.sol_ticks = deque()
        self.sentinel_stats = {}
        self.btc_trade_buffer = deque() # For Magnet analysis (List of dicts)
        
        # Phase 78c Unity: Track cumulative starts for global synthetic candles
        self.global_delta_start = 0.0
        self.global_vol_start = 0.0
        self.last_global_raw_delta = 0.0
        self.last_global_raw_volume = 0.0
        self.current_shm_state = None
        self.is_shm_init = False
        
        # Logger
        self.logger = get_logger()
        self.logger.start_console_logging() # Activate persistent console logs
        
        # Wire up callbacks
        self.data_stream.on_candle_close = self._on_candle_close
        self.data_stream.on_liquidation = self._on_liquidation
        self.data_stream.on_sentinel_update = self._on_sentinel_update
        self.data_stream.on_depth_update = self._on_depth_update
        self.footprint.on_trade = self._on_footprint_trade
        self.prev_delta = 0.0 # Track previous candle delta for deceleration
    
    async def _preload_candles(self):
        """Fetch recent historical candles via REST API to avoid warmup delay."""
        import requests
        
        # Dynamic lookback based on timeframe
        candle_limit = min(self.lookback_candles + 100, 1500)
        
        base_url = "https://testnet.binancefuture.com" if self.testnet else "https://fapi.binance.com"
        url = f"{base_url}/fapi/v1/klines?symbol=BTCUSDT&interval={self.timeframe}&limit={candle_limit}"
        
        try:
            response = await asyncio.to_thread(
                lambda: requests.get(url, timeout=10)
            )
            if response.status_code == 200:
                klines = response.json()
                
                for k in klines[:-1]:  # Skip last (current incomplete)
                    candle = {
                        "ts": k[0],
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5])
                    }
                    self.candle_buffer.append(candle)
                    self.prev_close = candle["close"]
                    # Phase 61: Feed historical math to prime the localized 24-hr Z-Score window
                    gk_vol_preload = calculate_garman_klass(candle)
                    self.regime_detector.update(gk_vol_preload, candle["close"])
                
                # Trim to EXACT size (For HMM/Lookbacks)
                while len(self.candle_buffer) > self.lookback_candles:
                    self.candle_buffer.pop(0)
                
                # =================================================================
                # SESSION-ANCHORED INITIALIZATION (Phase 43)
                # =================================================================
                # Find the most recent session anchor (UTC)
                now_utc = datetime.utcnow()
                anchors = [
                    now_utc.replace(hour=0, minute=0, second=0, microsecond=0),
                    now_utc.replace(hour=8, minute=0, second=0, microsecond=0),
                    now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
                ]
                # Filter out anchors in the future
                valid_anchors = [a for a in anchors if a <= now_utc]
                if not valid_anchors: # Handle case before 00:00 (yesterday's NY open)
                    last_anchor = anchors[2] - timedelta(days=1)
                else:
                    last_anchor = max(valid_anchors)

                # Populate Volume Profile ONLY with data from the current session
                self.volume_profile = VolumeProfile() # Reset
                session_candles = 0
                for c in self.candle_buffer:
                    # Convert kline timestamp (ms) to datetime for comparison
                    c_dt = datetime.utcfromtimestamp(c["ts"] / 1000)
                    if c_dt >= last_anchor:
                        self.volume_profile.add_candle(c)
                        session_candles += 1
                
                self.volume_profile.calculate()
                
                print(f"[Bot] Pre-loaded {len(self.candle_buffer)} candles for Lookback.")
                print(f"[Bot] Session VP: Loaded {session_candles} candles since {last_anchor.strftime('%H:%M')} UTC.")
                print(f"[Bot] Initial VP: VAL=${self.volume_profile.val:,.0f} | POC=${self.volume_profile.poc:,.0f} | VAH=${self.volume_profile.vah:,.0f}")
                
                # Show range of loaded data
                highs = [c["high"] for c in self.candle_buffer]
                lows = [c["low"] for c in self.candle_buffer]
                if highs and lows:
                    range_high = max(highs)
                    range_low = min(lows)
                    print(f"[Bot] Loaded Range: ${range_low:,.2f} - ${range_high:,.2f} ({(range_high/range_low - 1)*100:.2f}% width)")
                return True
        except Exception as e:
            print(f"[Bot] Warning: Could not preload candles: {e}")
        
        return False
    
    async def start(self):
        """Start the trading bot."""
        print("=" * 60)
        print("  VEBB-AI Trading Bot")
        print("=" * 60)
        print(f"  Mode: {'TESTNET' if self.testnet else 'LIVE'}")
        print(f"  Timeframe: {self.timeframe}")
        print(f"  Capital: ${self.position_manager.balance:.2f}")
        print(f"  Confidence Threshold: {self.confidence_threshold}")
        print("=" * 60)
        print()
        
        self.running = True
        
        # 1. Reconcile Phase: Sync internal state with Binance
        await self.sync_with_exchange()
        
        # 2. Warmup Phase: REST Preload for HMM/Indicators
        await self._preload_candles()
        
        # 3. Context Phase: Initial Multi-Timeframe Analysis
        await self.multi_tf.update()
        
        # 4. Neural Phase: Launch Advanced Math Workers
        asyncio.create_task(self._sentinel_worker())
        asyncio.create_task(self._macro_analyst_worker()) # Phase 79c: Off-Path Analyst
        asyncio.create_task(self._macro_regime_loop()) # Phase 101 Step 3
        
        # 5. Connection to Nervous System (Shared Memory Bridge)
        if self.shm_reader.connect():
            print("[Bot] Nervous System (SHM) Connected. Engaging high-frequency polling...")
            asyncio.create_task(self._nervous_system_heartbeat())
        else:
            print("[Bot] Warning: Could not connect to Rust Nervous System. Check if 'vebb-ingest' is running.")
            
        # 6. Data Phase: Combined WebSocket Stream (BLOCKING PERSISTENCE)
        print("[Bot] Starting main event loop...")
        await self.data_stream.start()

    async def _nervous_system_heartbeat(self):
        """
        High-frequency polling loop for the Rust Nervous System (Phase 77).
        This replaces the legacy 'on_aggTrade' firehose with a zero-copy RAM bridge.
        """
        while self.running:
            try:
                # Phase 96: Zero-Inflated Temporal Variance Update
                now = datetime.utcnow()
                if (now - getattr(self, "last_liq_update_ts", datetime.min)).total_seconds() >= 60:
                    self.dynamic_liq_guard.update(self.last_liquidation_v_1m)
                    self.last_liq_update_ts = now

                state = self.shm_reader.read()
                if state and state.sequence_id > self.last_shm_seq:
                    self.last_shm_seq = state.sequence_id
                    self.current_shm_state = state
                    
                    # Phase 78c Unity Initializer
                    if not self.is_shm_init:
                        self.global_delta_start = state.global_raw_delta
                        self.global_vol_start = state.global_raw_volume
                        self.last_global_raw_delta = state.global_raw_delta
                        self.last_global_raw_volume = state.global_raw_volume
                        self.is_shm_init = True
                    
                    # Calculate differentials for high-fidelity engine injection (Phase 78c)
                    delta_diff = state.global_raw_delta - self.last_global_raw_delta
                    vol_diff = state.global_raw_volume - self.last_global_raw_volume
                    self.last_global_raw_delta = state.global_raw_delta
                    self.last_global_raw_volume = state.global_raw_volume

                    # 1. Update VWAP Engine (Phase 78: Using Unified Global Raw Flow)
                    if state.binance_price > 0:
                        self.vwap_engine.update(state.binance_price, vol_diff, delta_diff)
                        
                        # Phase 78c: Update Microstructure with differential GLOBAL raw delta
                        if vol_diff > 0 or delta_diff != 0:
                             self.microstructure.update_with_trade(state.binance_price, abs(delta_diff), delta_diff > 0)
                        
                        # Update Chart Memory clock with Global Volume
                        self.chart_memory.update_global_clock(state.global_raw_volume)

                        # 2. Check for mid-candle exits (AI/Hard)
                        if self.position_manager.position is not None:
                            await self._on_live_candle_update({
                                "close": state.binance_price,
                                "ts": datetime.utcnow(),
                                "volume": state.binance_vol if hasattr(state, 'binance_vol') else 0.0,
                                "is_closed": False
                            })

                    if abs(state.lead_lag_theta) > self.vol_floor:
                        direction = "BULLISH" if state.lead_lag_theta > 0 else "BEARISH"
                        now = datetime.utcnow()
                        
                        # Phase 80b: GOBI-Scaled Hysteresis (Leading Microstructure)
                        gobi = state.global_obi 
                        # Bias: Lower hysteresis if GOBI aligns with the intended direction (buy-side pressure = gobi > 0)
                        is_aligned = (direction == "BULLISH" and gobi > 0.5) or (direction == "BEARISH" and gobi < -0.5)
                        gobi_bias = 0.5 if is_aligned else 1.8 # 0.5x threshold if aligned, 1.8x if resistant
                        
                        dyn_h_threshold = self.hysteresis_multiplier * gobi_bias 
                        
                        # Check for flip with GOBI-biased buffer
                        is_flip = (direction != self.last_alpha_direction) and (abs(state.lead_lag_theta - self.last_alpha_val) > dyn_h_threshold)
                        is_stale = (now - self.last_alpha_log_ts).total_seconds() > 60 
                        
                        if is_flip or is_stale:
                            log_msg = f"[Nervous] Lead-Lag Alpha: {direction} Theta={state.lead_lag_theta:.2f} (GOBI-Biased)"
                            self.logger.info(log_msg)
                            self.last_alpha_log_ts = now
                            self.last_alpha_direction = direction
                            self.last_alpha_val = state.lead_lag_theta
                            
                            # Phase 80: Autonomous SOTA Execution (OFF-PATH)
                            # If Theta is extreme and GOBI concurs, we strike to eliminate AI path latency
                            if abs(state.lead_lag_theta) > (self.vol_floor * gobi_bias * 1.5):
                                if self.position_manager.position is None:
                                    action = TradeAction.GO_LONG if direction == "BULLISH" else TradeAction.GO_SHORT
                                    
                                    # Phase 89: Structural Alignment Guard to prevent AI Panic Exits 
                                    # Don't front-run a move that is fighting a colossal local delta tidal wave.
                                    current_local_delta = getattr(self.footprint.current, "cumulative_delta", 0.0) if getattr(self, "footprint", None) and self.footprint.current else 0.0
                                    
                                    # Phase 89.2: Global Unity Guard (Upgraded to Phase 102 Dynamic EWMV Bounds)
                                    current_global_delta = state.global_raw_delta - self.global_delta_start
                                    
                                    # Retrieve dynamic stochastic O(1) bounds generated by Rust
                                    import ctypes
                                    tau_upper = ctypes.cast(ctypes.pointer(ctypes.c_uint64(state.dynamic_tau_upper)), ctypes.POINTER(ctypes.c_double)).contents.value if hasattr(state, 'dynamic_tau_upper') else 250.0
                                    tau_lower = ctypes.cast(ctypes.pointer(ctypes.c_uint64(state.dynamic_tau_lower)), ctypes.POINTER(ctypes.c_double)).contents.value if hasattr(state, 'dynamic_tau_lower') else -250.0
                                    
                                    is_conflicting = False
                                    if action == TradeAction.GO_SHORT:
                                        if current_local_delta > 50.0:
                                            is_conflicting = True
                                            print(f"  [{self.timeframe} 🛡️] SOTA VETO: Short blocked by massive Local Buy Delta (+{current_local_delta:.1f} BTC)")
                                        elif current_global_delta > tau_upper:
                                            is_conflicting = True
                                            print(f"  [{self.timeframe} 🛡️] SOTA VETO: Short blocked by dynamic Global Flow Threshold (+{current_global_delta:.1f} > +{tau_upper:.1f})")
                                    elif action == TradeAction.GO_LONG:
                                        if current_local_delta < -50.0:
                                            is_conflicting = True
                                            print(f"  [{self.timeframe} 🛡️] SOTA VETO: Long blocked by massive Local Sell Delta ({current_local_delta:.1f} BTC)")
                                        elif current_global_delta < tau_lower:
                                            is_conflicting = True
                                            print(f"  [{self.timeframe} 🛡️] SOTA VETO: Long blocked by dynamic Global Flow Threshold ({current_global_delta:.1f} < {tau_lower:.1f})")
                                    
                                    if not is_conflicting:
                                        signal = TradeSignal(
                                            action=action,
                                            confidence=0.92,
                                            reasoning=f"SOTA Alpha: Lead-Lag Theta {state.lead_lag_theta:.2f} confirmed by GOBI {gobi:.2f}.",
                                            stop_loss_pct=self.dynamic_exit.calculate(max(self.atr, 1.0), state.binance_price, self.prev_regime or 'NORMAL', getattr(self, '_last_hurst', 0.5), getattr(self, '_last_gk_vol', 0.005), 'Lead_Lag_Alpha')[1],
                                            take_profit_pct=self.dynamic_exit.calculate(max(self.atr, 1.0), state.binance_price, self.prev_regime or 'NORMAL', getattr(self, '_last_hurst', 0.5), getattr(self, '_last_gk_vol', 0.005), 'Lead_Lag_Alpha')[0]
                                        )
                                        print(f"  [{self.timeframe} ⚡] AUTONOMOUS SOTA ENTRY: {action.value} (Theta={state.lead_lag_theta:.2f}, GOBI={gobi:.2f})")
                                        self.entry_delta = current_local_delta
                                        self.entry_time = datetime.now()
                                        asyncio.create_task(self._execute_signal(signal, state.binance_price))
                                    
                    # Phase 83: Periodic Stochastic Recalibration
                    if state.sequence_id % 1000 == 0: # Every ~1000 SHM events (~10s-30s depending on vol)
                        self._update_stochastic_controls()

                await asyncio.sleep(0.0001) # 100 microsecond sleep
            except Exception as e:
                if "mmap closed" in str(e):
                    break
                # Only log unexpected errors to prevent spam
                print(f"[SHM] Heartbeat warning: {e}")
                await asyncio.sleep(1)
        
        # Phase 61: HMM Models are DELETED. Live Z-Score executes natively without loading .pkl files.

        # Phase 53: Dynamic Leverage from .env (Default to 20x)
        import re
        env_leverage = os.getenv("LEVERAGE", "20")
        match = re.search(r'\d+', str(env_leverage))
        LEVERAGE = int(match.group(0)) if match else 20
        
        # Explicit diagnostic log to show exactly what is being read
        print(f"[System Diagnostic] Raw LEVERAGE value loaded by Python: '{env_leverage}'")
        await self.exchange.set_leverage(LEVERAGE)
        self.position_manager.leverage = LEVERAGE
        print(f"[Bot] Leverage synced to {LEVERAGE}x (Buying Power: ${self.position_manager.balance * LEVERAGE:.2f})")
        
        # Pre-load historical candles for immediate trading
        await self._preload_candles()
        
        # Sync with actual Binance state BEFORE starting data stream
        await self.sync_with_exchange()
        
        # Start data stream and workers
        try:
            # Phase 78c Unity: Footprint now feeds passively from DataStream
            asyncio.create_task(self._sentinel_worker()) # Start lead-lag worker
            await self.data_stream.start()
        except KeyboardInterrupt:
            await self.stop()
            import sys
            sys.exit(130) # Explicitly exit with bash SIGINT code to break external wrapper loops
    
    async def stop(self):
        """Stop the trading bot gracefully."""
        print("\n[Bot] Shutting down...")
        self.running = False
        
        # Close any open position
        if self.position_manager.position:
            print("[Bot] Closing open position...")
            price = await self.exchange.get_current_price()
            await self._close_position(price, "SHUTDOWN")
        
        await self.data_stream.stop()
        await self.footprint.stop() # Stop footprint
        # OrderBook is now fed passively via DataStream callback (no standalone WS to stop)
        await self.exchange.close()
        
        # Export history
        self.position_manager.export_history()
        
        print("[Bot] Shutdown complete.")

    async def sync_with_exchange(self):
        """Fetch actual state from Binance and sync local PositionManager."""
        try:
            # 1. Fetch actual data from Binance
            balance = await self.exchange.get_balance()
            position = await self.exchange.get_position()
            
            # 2. Reconcile local state
            self.position_manager.sync_state(balance, position)
            
            # Update local attributes if needed
            if self.position_manager.position:
                self.current_signal = self.position_manager.position.side.value
            else:
                self.current_signal = None
                
        except Exception as e:
            print(f"[Bot] ❌ Exchange Sync Failed: {e}")

    async def _on_depth_update(self, depth_data: dict):
        """Feed depth snapshots from DataStream into OrderBookBuilder (passive mode)."""
        self.order_book.feed(depth_data)

    def _on_footprint_trade(self, price: float, volume: float, is_buyer_aggressor: bool):
        """Hook from FootprintBuilder to update micro-engines (Phase 72)."""
        self.microstructure.update_with_trade(price, volume, is_buyer_aggressor)
        
        # Calculate localized delta for the engine
        delta = volume if is_buyer_aggressor else -volume
        self.vwap_engine.update(price, volume, delta)

    def _calculate_macro_ema(self, period: int = 200) -> float:
        """Calculate the Macro EMA (Safe from VWAP removal) to identify structural walls (Phase 73)."""
        if len(self.candle_buffer) < period:
            return 0.0
        
        closes = [c["close"] for c in self.candle_buffer]
        alpha = 2 / (period + 1)
        
        # Start with Simple Moving Average
        ema = sum(closes[:period]) / period
        
        # Iteratively calculate EMA to convergence
        for p in closes[period:]:
            ema = (p - ema) * alpha + ema
            
        return ema
        
    def _calculate_atr(self, period=14):
        """Calculate Average True Range (Phase 79)."""
        if len(self.candle_buffer) < period + 1:
            return 0.0
        
        tr_list = []
        for i in range(len(self.candle_buffer) - period, len(self.candle_buffer)):
            curr = self.candle_buffer[i]
            prev = self.candle_buffer[i-1]
            tr = max(
                curr['high'] - curr['low'],
                abs(curr['high'] - prev['close']),
                abs(curr['low'] - prev['close'])
            )
            tr_list.append(tr)
        
        return float(np.mean(tr_list))

    def _update_stochastic_controls(self):
        """
        Phase 84: Supervisor shifted Stochastic math to Rust. 
        We now ONLY inject Macro Parameters (Toxicity, Sentiment, Thresholds) via ControlBridge.
        """
        try:
            # Inject into Hot-Path via ControlBridge
            self.control_bridge.update_params(
                lead_lag_threshold=self.vol_floor, # Lead-lag boundary
                obi_threshold=self.hysteresis_multiplier, # Base OBI
                min_trade_size=0.001,
                max_position_size=0.1,
                toxicity_scalar=self.vwap_engine.get_vpin() if hasattr(self.vwap_engine, 'get_vpin') else 0.0,
                sentiment_score=self.gemini.last_sentiment_score if hasattr(self.gemini, 'last_sentiment_score') else 0.0,
                wallet_margin_balance=self.position_manager.balance, # Phase 87 Defense
                z_base=getattr(self.current_macro_regime, 'z_base', 3.0),
                scaling_gamma=getattr(self.current_macro_regime, 'gamma', 0.5),
                gobi_kappa=getattr(self.current_macro_regime, 'kappa', 0.5),
                is_trading_enabled=self.running
            )
            # Print removed; Rust logs its own entropy and metrics now.

        except Exception as e:
            print(f"  [Macro Control] Sync Error: {e}")


    async def _macro_regime_loop(self):
        """Phase 101: Asynchronously updates macro heuristics every 15 minutes."""
        import asyncio
        print("[MacroStrategist] Loop started.")
        while self.running:
            try:
                # 1. Gather context non-blockingly
                await asyncio.gather(
                    self.market_context.get_context(),
                    self.multi_tf.update()
                )
                market_context_text = self.market_context.format_for_gemini()
                mtf_text = self.multi_tf.format_for_gemini()
                
                # 2. Fetch regime
                regime = await self.gemini.analyze_macro_regime(
                    recent_candles=self.candle_buffer[-20:] if len(self.candle_buffer) > 20 else self.candle_buffer,
                    footprint_text=self.footprint.format_for_gemini() if hasattr(self, 'footprint') else "No Footprint",
                    order_book_text=self.order_book.format_for_gemini() if hasattr(self, 'order_book') else "No OBI",
                    market_context_text=market_context_text,
                    mtf_text=mtf_text,
                    chart_memory_text=self.chart_memory.get_active_zone_summary() if hasattr(self, 'chart_memory') else ""
                )
                
                self.current_macro_regime = regime
                print(f"[MacroStrategist] Updated Tuning -> Bias: {regime.bias}, Hysteresis: {regime.hysteresis_multiplier:.2f}, Floor: {regime.vol_floor:.2f}")
                
            except Exception as e:
                print(f"[MacroStrategist] Error in background loop: {e}")
                
            # Wait 15 minutes
            await asyncio.sleep(900)
    async def _macro_analyst_worker(self):
        """
        Phase 79c: Macro Regime Analyst (OFF-PATH).
        Polls Gemini periodically to update execution parameters (Hysteresis, Vol Floor).
        """
        print("[Bot] Macro Analyst Worker started (Off-Path).")
        while True:
            try:
                if len(self.candle_buffer) >= 20:
                    # Refresh ATR
                    self.atr = self._calculate_atr()
                    
                    # Phase 79c: Global Context Analysis
                    ctx_text = f"Volatility: {self.atr:.2f} | Current Price: ${self.prev_close:.2f}"
                    
                    macro_regime = await self.gemini.analyze_macro_regime(
                        recent_candles=self.candle_buffer,
                        market_context_text=ctx_text
                    )
                    
                    # Update parameters safely
                    self.hysteresis_multiplier = min(macro_regime.hysteresis_multiplier, 4.0)  # Phase 108: Cap at 4.0
                    self.vol_floor = macro_regime.vol_floor
                    self.last_macro_update_ts = datetime.utcnow()
                    
                    print(f"  [{self.timeframe} 🧬] Macro Update: {macro_regime.bias} | Hyst: {self.hysteresis_multiplier:.1f}x | Floor: {self.vol_floor:.1f}")
                    print(f"  Reasoning: {macro_regime.reasoning[:100]}...")
                
                await asyncio.sleep(900) # Poll every 15 minutes
            except Exception as e:
                print(f"[MacroAnalyst] Error: {e}")
                await asyncio.sleep(60)

    def _check_sniper_entry(self, price: float, delta: float, intensity: float = 0.0) -> dict:
        """
        Sniper Entry Filter - PoNR (Point of No Return) Architecture (Phase 49).
        
        Zero-Underwater Entry: 
        1. Detects Spatial PoNR (VAH/VAL boundary cross OR LVN breach).
        2. Validates Kinetic PoNR (Hawkes Intensity > 50k).
        3. Executes Logistical OBI thresholding for zero-latency entry.
        4. Blocks on Passive Absorption (Absorption Guard 2.0).
        """
        result = {
            "should_trade": False,
            "direction": None,
            "reason": "",
            "val": self.volume_profile.val,
            "vah": self.volume_profile.vah,
            "poc": self.volume_profile.poc,
            "zone": ""
        }
        
        # 0. Climax/Exhaustion Guard (Phase 59: DID Ratio)
        # Prevent Sniper from buying the top/bottom of a massive volume spike,
        # UNLESS the Delta-Intensity Divergence (DID) proves it's Unrestricted Displacement.
        if intensity > 150000.0:
            did_ratio = abs(delta) / intensity if intensity > 0 else 0
            # If DID < 0.002 (e.g. 300 BTC delta on 150k ticks), it is Passive Absorption (Climax)
            if did_ratio < 0.002:
                # Phase 106: Macro Bias Override — allow entry if Gemini's async macro bias
                # aligns with the intended direction. High-intensity absorption + macro alignment
                # = institutional accumulation, NOT distribution.
                macro_bias = getattr(self.current_macro_regime, 'bias', 'NEUTRAL')
                intended_dir = "LONG" if delta > 0 else "SHORT"
                macro_aligned = (
                    (intended_dir == "LONG" and macro_bias == "BULLISH") or
                    (intended_dir == "SHORT" and macro_bias == "BEARISH")
                )
                if not macro_aligned:
                    result["reason"] = f"Exhaustion Guard: Passive Absorption Climax (Int={intensity:.0f}, DID={did_ratio:.4f})"
                    return result
                else:
                    import time as _time
                    _now = _time.time()
                    if _now - getattr(self, '_last_macro_bypass_log', 0) > 60:
                        print(f"  [{self.timeframe} 🟢] EXHAUSTION GUARD BYPASSED: Macro {macro_bias} aligns with {intended_dir} (Phase 106)")
                        self._last_macro_bypass_log = _now
            else:
                # Rate limit the print statement to avoid console deadlock during live ticks
                import time as _time
                _now = _time.time()
                if _now - getattr(self, '_last_disp_log_ts', 0) > 60:
                    print(f"  [{self.timeframe} 🌊] UNRESTRICTED DISPLACEMENT: Intensity 150k+ but DID={did_ratio:.4f} validates breakout!")
                    self._last_disp_log_ts = _now
            
        if len(self.candle_buffer) < 20:
            result["reason"] = "Heating up (Need 20 candles)"
            return result
        
        val = self.volume_profile.val
        vah = self.volume_profile.vah
        poc = self.volume_profile.poc
        current_obi = self.order_book.obi
        
        # Phase 78b: Microstructure Fusion (GOBI)
        # Pull the global liquidity-weighted OBI from the Nervous System
        state = self.shm_reader.read()
        gobi = state.global_obi if state else 0.0
        
        # High-trust OBI Fusion: Use GOBI as a secondary confirmation shield
        # If GOBI is extremely opposite to local OBI, the "wall" is likely a local fake.
        obi_fused = (current_obi + gobi) / 2.0 if state else current_obi
        
        vp_context = self.volume_profile.get_context(price)
        
        if vah > val:
            pct_va = (price - val) / (vah - val) * 100
        else:
            pct_va = 50.0

        result["zone"] = vp_context
        result["range_high"] = vah
        result["range_low"] = val
        result["position_in_range"] = f"{vp_context} ({pct_va:.0f}%)"

        # 1. INSTITUTIONAL PoNR (Point of No Return) - TREND EXPANSION
        # Boundary Cross + Critical Intensity (50k)
        # Phase 52: Also triggers on LVN (Liquidity Vacuum) breach
        lvns = []
        if hasattr(self.volume_profile, "get_lvns"):
            lvns = self.volume_profile.get_lvns()
        else:
            # Diagnostic for the user
            print(f"  [SYSTEM ERROR] ❌ VolumeProfile instance {type(self.volume_profile)} is missing 'get_lvns'.")
            print(f"  Module: {self.volume_profile.__class__.__module__}")
            import order_flow
            print(f"  Expected File: {order_flow.__file__}")

        in_lvn = any(abs(price - lvn) < 20 for lvn in lvns) if lvns else False
        
        is_ponr_long = (price >= vah or (in_lvn and price > poc)) and intensity >= 50000.0 and delta > 0
        is_ponr_short = (price <= val or (in_lvn and price < poc)) and intensity >= 50000.0 and delta < 0
        
        if is_ponr_long or is_ponr_short:
            # For PoNR, we use the INSTITUTIONAL LOGISTIC threshold
            obi_thresh = self.microstructure.get_adaptive_threshold(0.85, 0, 0, intensity=intensity)
            
            if (is_ponr_long and obi_fused >= obi_thresh) or (is_ponr_short and obi_fused <= -obi_thresh):
                result["should_trade"] = True
                result["direction"] = "LONG" if is_ponr_long else "SHORT"
                reason_type = "LVN Vacuum" if in_lvn else "Expansion"
                result["reason"] = f"⚛️ PoNR DETECTED: {result['direction']} {reason_type} (Int={intensity:.0f}, GOBI={obi_fused:.2f}/{obi_thresh:.2f})"
                return result

        # 1b. LEAD-LAG ALPHA TRIGGER (Phase 78: Institutional Front-running)
        if state and abs(state.lead_lag_theta) >= 2.5:
             if (state.lead_lag_theta > 0 and delta > 0) or (state.lead_lag_theta < 0 and delta < 0):
                result["should_trade"] = True
                result["direction"] = "LONG" if state.lead_lag_theta > 0 else "SHORT"
                result["reason"] = f"🕵️ LEAD-LAG THETA: {result['direction']} Smart Money Lead (Theta={state.lead_lag_theta:.2f})"
                return result

        # 1c. DYNAMIC INSTITUTIONAL ABSORPTION GUARD (Phase 92: Dynamic DI Guard)
        if state:
            # Synthesize pseudo-probabilities from the categorical RegimeDetector state
            prob_chop = 1.0 if self.prev_regime in ["NORMAL", "RANGE", "TRANSITION"] else 0.0
            prob_trend = 1.0 if self.prev_regime == "TREND" else 0.5 # Default weighting if unmatched
            prob_crash = 1.0 if self.prev_regime in ["CRISIS", "HIGH_VOL"] else 0.0
            
            z_score_di, dyn_thresh, is_trap_legacy, is_absorption = self.dynamic_di_guard.evaluate_di_trap(
                state.global_di, prob_chop, prob_trend, prob_crash
            )
            
            # Phase 109: Adaptive Liquidation Guard (replaces fixed Z < -2.50)
            is_trap = self.adaptive_liq_guard.is_trap(state.global_di, self.prev_regime or 'NORMAL')
            
            # LIQUIDATION HUNT GUARD: If Futures is pushing price (Negative DI), block entry
            if is_trap and delta > 0:
                 result["reason"] = f"🛡️ DYN-DI GUARD: Liquidation Hunt (Adaptive P5, Regime={self.prev_regime}). Blocking LONG."
                 return result
            # INSTITUTIONAL ABSORPTION SIGNAL: If Spot is buying the dip (Positive DI), pre-emptively LONG
            if is_absorption and vp_context == "DISCOUNT" and delta < 0:
                 result["should_trade"] = True
                 result["direction"] = "LONG"
                 result["reason"] = f"🐋 DYN INST. ABSORPTION: Spot buying the dip (Z={z_score_di:.2f})."
                 return result

        # 2. DYNAMIC TREND (Phase 109: Adaptive Breakout Thresholds)
        trend_threshold, gobi_thresh, is_breakout_long, is_breakout_short = self.adaptive_trend.get_thresholds(
            delta, intensity, obi_fused
        )
        
        if is_breakout_long:
            result["should_trade"] = True
            result["direction"] = "LONG"
            result["reason"] = f"🔥 TREND LONG: Adaptive breakout (Δ={delta:.0f} > Θ={trend_threshold:.0f}, GOBI {obi_fused:.2f} > {gobi_thresh:.2f})."
            return result
        elif is_breakout_short:
            result["should_trade"] = True
            result["direction"] = "SHORT"
            result["reason"] = f"🌊 TREND SHORT: Adaptive breakdown (Δ={delta:.0f} < -Θ={trend_threshold:.0f}, GOBI {obi_fused:.2f} < -{gobi_thresh:.2f})."
            return result

        # 3. MEAN REVERSION (Phase 109: Adaptive Sniper Buffer)
        mu_h = float(np.mean(self.adaptive_regime.h_history)) if len(self.adaptive_regime.h_history) > 10 else 0.5
        std_h = float(np.std(self.adaptive_regime.h_history)) if len(self.adaptive_regime.h_history) > 10 else 0.05
        ENTRY_BUFFER = self.adaptive_sniper_buffer.calculate_buffer(
            hurst=getattr(self, '_last_hurst', 0.5),
            mu_h=mu_h, std_h=std_h,
            va_high=self.volume_profile.vah, va_low=self.volume_profile.val,
            atr=max(self.atr, 0.01)
        )
        vw_metrics = self.vwap_engine.get_metrics()
        vwap = vw_metrics['vwap']
        std_vwap = vw_metrics['std_vwap']
        cvd_z_score = vw_metrics['cvd_z_score']
        poc = self.volume_profile.poc
        
        # OBI must align for reversion (No falling knives)
        base_obi_thresh = self.microstructure.get_adaptive_threshold(0.85, 0, 0, intensity=intensity)
        
        # Phase 59: MAGNET CONFLUENCE OVERRIDE
        magnet_long = self.magnet.get_nearest_magnet(price, "LONG")
        magnet_short = self.magnet.get_nearest_magnet(price, "SHORT")
        
        obi_thresh_long = base_obi_thresh * 0.75 if (magnet_long and magnet_long > price) else base_obi_thresh
        obi_thresh_short = base_obi_thresh * 0.75 if (magnet_short and magnet_short < price) else base_obi_thresh

        # =================================================================
        # HISTORICAL S&D MEMORY CONFLUENCE (Phase 60)
        # =================================================================
        hmm_state_multiplier = 1.0 if self.prev_regime != "CRISIS" else 1.5
        zone_mods = self.chart_memory.get_confluence_modifiers(price, hmm_multiplier=hmm_state_multiplier)
        
        # Radically discount OBI requirements if we are at a known institutional wall
        obi_thresh_long = max(0.15, obi_thresh_long - zone_mods['long_obi_discount'])
        obi_thresh_short = max(0.15, obi_thresh_short - zone_mods['short_obi_discount'])
        
        # Check Pre-Emptive Exhaustion Trade (Memory-Driven)
        # Phase 63: Hawkes Deceleration Catch (No more falling knives)
        if zone_mods.get('exhaustion_target') is not None:
            z_type = zone_mods.get('zone_type', '')
            
            # Fetch Hawkes Point-Process Kinematics
            h_lambda = getattr(self.microstructure, "hawkes_lambda", 0.0)
            h_deriv = getattr(self.microstructure, "hawkes_derivative", 0.0)
            
            # We require the event arrival rate (velocity) to be exceptionally high (> 50),
            # but the acceleration to be negative (decelerating into the wall).
            is_exhausting = (h_lambda > 50.0) and (h_deriv < 0.0)
            
            if is_exhausting:
                if z_type == 'SUPPLY':
                    result["should_trade"] = True
                    result["direction"] = "SHORT"
                    result["reason"] = f"🧱 PRE-EMPTIVE SHORT: Hawkes Decelerating into Supply Wall (${zone_mods['exhaustion_target']:,.0f}). λ={h_lambda:.1f}, dλ/dt={h_deriv:.2f}"
                    return result
                elif z_type == 'DEMAND':
                    result["should_trade"] = True
                    result["direction"] = "LONG"
                    result["reason"] = f"🧱 PRE-EMPTIVE LONG: Hawkes Decelerating into Demand Wall (${zone_mods['exhaustion_target']:,.0f}). λ={h_lambda:.1f}, dλ/dt={h_deriv:.2f}"
                    return result

        if (vp_context == "DISCOUNT" or (vp_context == "FAIR_VALUE" and pct_va < ENTRY_BUFFER)):
            # 1. Standard Reversion (Dual-Vector Institutional Alignment)
            # Phase 109: Adaptive Bollinger κ (GK-scaled, replaces fixed 2.0)
            _ohlc = getattr(self, '_last_candle_ohlc', (price, price, price, price))
            kappa_long = self.adaptive_mr.get_bollinger_kappa(_ohlc[0], _ohlc[1], _ohlc[2], _ohlc[3])
            lower_band = vwap - (kappa_long * std_vwap)
            price_extended_long = price <= lower_band
            # Phase 109: Adaptive CVD exhaustion (replaces fixed > -1.5)
            sellers_exhausted = self.adaptive_mr.is_sellers_exhausted(cvd_z_score)
            
            standard_long = (delta > 1.0) and (obi_fused >= obi_thresh_long) and price_extended_long and sellers_exhausted

            # 2. Phase 64: Exhaustion Reversion (adaptive OFI & Hawkes Kinetics)
            delta_decelerating = delta > self.prev_delta     # 2nd derivative positive
            
            # Fetch OFI Array for dynamic standard deviation bounds
            recent_ofis = list(self.microstructure.delta_volumes) if hasattr(self.microstructure, 'delta_volumes') and len(self.microstructure.delta_volumes) > 5 else [0.0]
            sigma_ofi = np.std(recent_ofis) if len(recent_ofis) > 1 else 10.0
            
            # Dynamic L_adverse bound instead of static -35.0 BTC
            # Base tolerance is dynamically scaled by OFI volatility (No static floor). 
            dynamic_tol_long = -(2.5 * sigma_ofi) 
            delta_within_tol = delta >= dynamic_tol_long
            
            obi_brick_wall = obi_fused >= max(obi_thresh_long * 1.5, 0.65) # Institutional skew
            
            # Phase 64: Hawkes Kinetics instead of static 50,000 count
            h_lambda = getattr(self.microstructure, "hawkes_lambda", 0.0)
            h_deriv = getattr(self.microstructure, "hawkes_derivative", 0.0)
            
            # Calculate rolling Z-Score of the Hawkes Arrival Rate
            self.hawkes_buffer.append(h_lambda)
            h_sigma = np.std(self.hawkes_buffer) if len(self.hawkes_buffer) > 10 else 1.0
            h_mu = np.mean(self.hawkes_buffer) if len(self.hawkes_buffer) > 10 else h_lambda
            h_zscore = (h_lambda - h_mu) / max(0.01, h_sigma)
            
            # Require 3.5 Sigma anomaly event AND negative acceleration
            hawkes_climax = (h_zscore > 3.5) and (h_deriv < 0.0)

            exhaustion_long = (delta < 0.0) and delta_decelerating and delta_within_tol and obi_brick_wall and hawkes_climax

            if standard_long or exhaustion_long:
                target = max(vwap, poc) if vwap > 0 and poc > 0 else (vwap if vwap > 0 else poc)
                if target > 0 and price < target:
                    result["should_trade"] = True
                    result["direction"] = "LONG"
                    reason_type = "EXHAUSTION" if exhaustion_long else "STANDARD"
                    result["reason"] = f"In {result['position_in_range']} + {reason_type} Reversion (GOBI {obi_fused:.2f} > {obi_thresh_long:.2f})."
                else:
                    result["reason"] = f"In {vp_context} but target (${target:,.0f}) reached."
            else:
                result["reason"] = f"In {vp_context} but GOBI {obi_fused:.2f} & Delta {delta:.1f} fail Reversion checks."
                
        elif (vp_context == "PREMIUM" or (vp_context == "FAIR_VALUE" and pct_va > (100 - ENTRY_BUFFER))):
            # 1. Standard Reversion (Dual-Vector Institutional Alignment)
            # Phase 109: Adaptive Bollinger κ for shorts
            _ohlc = getattr(self, '_last_candle_ohlc', (price, price, price, price))
            kappa_short = self.adaptive_mr.get_bollinger_kappa(_ohlc[0], _ohlc[1], _ohlc[2], _ohlc[3])
            upper_band = vwap + (kappa_short * std_vwap)
            price_extended_short = price >= upper_band
            buyers_exhausted = cvd_z_score < 1.5  # Symmetric (not adaptive for short side exhaustion)
            
            standard_short = (delta < -1.0) and (obi_fused <= (1 - obi_thresh_short)) and price_extended_short and buyers_exhausted

            # 2. Phase 64: Exhaustion Reversion (adaptive OFI & Hawkes Kinetics)
            delta_decelerating = delta < self.prev_delta
            
            recent_ofis = list(self.microstructure.delta_volumes) if hasattr(self.microstructure, 'delta_volumes') and len(self.microstructure.delta_volumes) > 5 else [0.0]
            sigma_ofi = np.std(recent_ofis) if len(recent_ofis) > 1 else 10.0
            
            # Dynamic L_adverse bound instead of static +35.0 BTC
            # Scale entirely via OFI Volatility (No static floor).
            dynamic_tol_short = (2.5 * sigma_ofi)
            delta_within_tol = delta <= dynamic_tol_short
            
            obi_brick_wall = obi_fused <= min(1 - (obi_thresh_short * 1.5), 0.35)
            
            h_lambda = getattr(self.microstructure, "hawkes_lambda", 0.0)
            h_deriv = getattr(self.microstructure, "hawkes_derivative", 0.0)
            
            # Calculate rolling Z-Score of the Hawkes Arrival Rate
            self.hawkes_buffer.append(h_lambda)
            h_sigma = np.std(self.hawkes_buffer) if len(self.hawkes_buffer) > 10 else 1.0
            h_mu = np.mean(self.hawkes_buffer) if len(self.hawkes_buffer) > 10 else h_lambda
            h_zscore = (h_lambda - h_mu) / max(0.01, h_sigma)
            
            # Require 3.5 Sigma anomaly event AND negative acceleration
            hawkes_climax = (h_zscore > 3.5) and (h_deriv < 0.0)

            exhaustion_short = (delta > 0.0) and delta_decelerating and delta_within_tol and obi_brick_wall and hawkes_climax

            if standard_short or exhaustion_short:
                target = min(vwap, poc) if vwap > 0 and poc > 0 else (vwap if vwap > 0 else poc)
                if target > 0 and price > target:
                    result["should_trade"] = True
                    result["direction"] = "SHORT"
                    reason_type = "EXHAUSTION" if exhaustion_short else "STANDARD"
                    result["reason"] = f"In {result['position_in_range']} + {reason_type} Reversion (GOBI {obi_fused:.2f})."
                else:
                    result["reason"] = f"In {vp_context} but target (${target:,.0f}) reached."
            else:
                result["reason"] = f"In {vp_context} but GOBI {obi_fused:.2f} & Delta {delta:.1f} fail Reversion checks."
                
        else:
            # Phase 108: Trend-Continuation Bypass
            # When macro + HTF + delta all confirm a directional move,
            # allow entries even in Fair Value during structural trends
            macro_bias = getattr(self.current_macro_regime, 'bias', 'NEUTRAL')
            _htf = getattr(self.multi_tf.current, 'overall_bias', None)
            htf_bullish = _htf in [TrendDirection.BULLISH, TrendDirection.STRONG_BULLISH]
            htf_bearish = _htf in [TrendDirection.BEARISH, TrendDirection.STRONG_BEARISH]
            
            dyn_thresh = getattr(self.delta_threshold, 'get_threshold', lambda *a, **kw: 50.0)(
                hurst=getattr(self, '_last_hurst', 0.5),
                z_gk=getattr(self, '_last_z_gk', 0.0),
                kyle_current=getattr(self.microstructure, 'kyles_lambda', 0.001) or 0.001,
                kyle_mean_24h=0.001
            )
            
            if macro_bias == "BULLISH" and htf_bullish and delta > dyn_thresh:
                result["should_trade"] = True
                result["direction"] = "LONG"
                result["reason"] = f"Phase 108 Trend-Continuation LONG (FV {pct_va:.0f}%, Δ={delta:.0f} > Θ={dyn_thresh:.0f}, Macro=BULLISH)"
            elif macro_bias == "BEARISH" and htf_bearish and delta < -dyn_thresh:
                result["should_trade"] = True
                result["direction"] = "SHORT"
                result["reason"] = f"Phase 108 Trend-Continuation SHORT (FV {pct_va:.0f}%, Δ={delta:.0f} < -Θ={dyn_thresh:.0f}, Macro=BEARISH)"
            else:
                result["reason"] = f"Price in Fair Value area ({pct_va:.0f}%)"
            
        # =================================================================
        # PHASE 73: MACRO EMA POINT-BLANK PROXIMITY GUARD
        # =================================================================
        if result.get("should_trade") is True:
            macro_ema = self._calculate_macro_ema(200)
            if macro_ema > 0:
                dist_pct = (price - macro_ema) / macro_ema
                
                # Fetch CVD Z-Score to check for absolute momentum (Phase 74 Breakout Override)
                cvd_z = self.vwap_engine.get_metrics(price)['cvd_z_score']
                
                # If SHORTING and we are hovering right above support (+0.00 to +0.0025)
                if result["direction"] == "SHORT" and 0.0 <= dist_pct <= 0.0025:
                    if cvd_z < -2.5: # 2.5 Sigma Selling Anomaly (Wall is breaking)
                        result["reason"] += f" (⚠️ OVERRIDE: CVD Z-Score {cvd_z:.2f} proves 200 EMA Support is breaking!)"
                    else:
                        result["should_trade"] = False
                        result["reason"] = f"🧱 MACRO GUARD: Blocked SHORT directly into point-blank 200 EMA Support (${macro_ema:,.0f}). CVD={cvd_z:.2f}"
                    
                # If LONGING and we are hovering right below resistance (-0.0025 to 0.0)
                elif result["direction"] == "LONG" and -0.0025 <= dist_pct <= 0.0:
                    if cvd_z > 2.5: # 2.5 Sigma Buying Anomaly (Wall is breaking)
                        result["reason"] += f" (⚠️ OVERRIDE: CVD Z-Score {cvd_z:.2f} proves 200 EMA Resistance is breaking!)"
                    else:
                        result["should_trade"] = False
                        result["reason"] = f"🧱 MACRO GUARD: Blocked LONG directly against point-blank 200 EMA Resistance (${macro_ema:,.0f}). CVD={cvd_z:.2f}"

        return result
    
    def _log_features(self, candle: dict, gk_vol: float, hurst: float, log_ret: float):
        """Log live features to CSV for future training (Phase 51 Versioned)."""
        try:
            os.makedirs("data", exist_ok=True)
            log_file = f"data/live_market_data_{self.timeframe}.csv"
            file_exists = os.path.isfile(log_file)
            
            with open(log_file, mode='a', newline='') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["ts", "open", "high", "low", "close", "volume", "gk_vol", "hurst", "log_ret"])
                
                writer.writerow([
                    candle["ts"],
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                    candle["volume"],
                    f"{gk_vol:.8f}",
                    f"{hurst:.4f}",
                    f"{log_ret:.8f}"
                ])
        except Exception as e:
            self.logger.error(f"Failed to log features: {e}")

    async def _on_candle_close(self, candle: dict):
        """Called when a candle closes - main decision point."""
        ts = candle["ts"]
        close = candle["close"]
        
        # Phase 78c Unity: Global Synthetic Candle Calculation
        state = self.shm_reader.read()
        global_delta_synthetic = 0.0
        global_vol_synthetic = 0.0
        if state and self.is_shm_init:
            global_delta_synthetic = state.global_raw_delta - self.global_delta_start
            global_vol_synthetic = state.global_raw_volume - self.global_vol_start
            # Reset for next candle
            self.global_delta_start = state.global_raw_delta
            self.global_vol_start = state.global_raw_volume
        else:
            # Fallback to local if SHM is not ready
            global_delta_synthetic = self.footprint.current.cumulative_delta
            global_vol_synthetic = candle["volume"]
        
        print(f"\n[{self.timeframe}] [{ts}] Candle closed @ ${close:,.2f}")
        
        # Sync with exchange to catch manual trades/closes before new decision
        await self.sync_with_exchange()
        
        # Update candle buffer (maintain dynamic window)
        self.candle_buffer.append(candle)
        if len(self.candle_buffer) > self.lookback_candles:
            self.candle_buffer.pop(0)
        
        # Calculate features
        gk_vol = calculate_garman_klass(candle)
        log_ret = calculate_log_return(close, self.prev_close or close)
        self.prev_close = close
        
        # Phase 61: Live Z-Score and Hurst Regime classification
        vol_regime, trend_regime, z_score, hurst = self.regime_detector.update(gk_vol, close)

        # Phase 66: Cache regime values for mid-candle flashpoint access
        self._last_hurst = hurst
        self._last_z_gk = z_score
        self._last_gk_vol = gk_vol  # Phase 107: Cache for dynamic TP/SL
        self._last_candle_ohlc = (float(candle['open']), float(candle['high']), float(candle['low']), close)  # Phase 109

        # Phase 66: Feed completed candle's CVD into rolling 24h buffer, then reset for new candle
        candle_abs_cvd = abs(self.footprint.current.cumulative_delta)
        self.delta_threshold.feed_candle_cvd(candle_abs_cvd)
        self.delta_threshold.reset_candle(self.microstructure.hawkes_lambda)

        # Phase 111: Update Kaufman-adaptive EWMA Hawkes Z on every candle close
        h_lambda = getattr(self.microstructure, 'hawkes_lambda', 0.0)
        self._hawkes_z = self.adaptive_hawkes.update(h_lambda)

        # Phase 111: Feed TFI buffer on every candle (for volume-conditioned calibration)
        fp = self.footprint.current
        total_v = fp.total_buy_volume + fp.total_sell_volume
        candle_tfi = (fp.total_buy_volume - fp.total_sell_volume) / total_v if total_v > 0 else 0.0
        self.adaptive_tfi.evaluate_and_update(candle_tfi)  # Feed buffer, ignore result

        # Phase 111: Feed ECDF multiplier buffers on every candle
        self.adaptive_mults.evaluate_and_update(gk_vol, hurst)

        # Phase 112: Feed CPR buffer on every candle (for self-calibrating close position)
        candle_o = float(candle['open'])
        candle_h = float(candle['high'])
        candle_l = float(candle['low'])
        self.adaptive_cpr.evaluate_and_update(
            "NEUTRAL", candle_o, candle_h, candle_l, close,
            delta, self._hawkes_z, is_pre_emptive=True  # Feed only, don't evaluate
        )

        # Phase 48: Dynamic TP Volatility Tracking
        self.vol_buffer.append(gk_vol)
        if len(self.vol_buffer) > 20:
            self.vol_buffer.pop(0)

        # Phase 48: Log features for Adaptive Lifecycle
        self._log_features(candle, gk_vol, hurst, log_ret)
        
        # =================================================================
        # INSTITUTIONAL SESSION ANCHORING (Phase 40)
        # =================================================================
        # Detect session starts (UTC)
        hour = ts.hour
        minute = ts.minute
        
        # Anchors: Asia (00:00), London (08:00), NY (13:30)
        is_session_start = False
        if (hour == 0 and minute == 0) or \
           (hour == 8 and minute == 0) or \
           (hour == 13 and minute == 30):
            is_session_start = True
            
        if is_session_start:
            print(f"  [{self.timeframe}] ⚓ SESSION ANCHOR HIT ({hour:02d}:{minute:02d} UTC). Applying Bayesian Decay (0.5).")
            self.volume_profile.decay(0.5)
            # Clear buffer to start fresh for the session? 
            # Research says align with the specific cohort. 
            # We'll keep a sliding version for HMM, but anchor the VP for Fair Value.
        
        # Update Volume Profile (Persistent Session-Anchored)
        self.volume_profile.add_candle(candle)
        self.volume_profile.calculate()
        
        # Display VP Summary
        print(f"  [{self.timeframe}] 📊 Session VP: VAL=${self.volume_profile.val:,.0f} | POC=${self.volume_profile.poc:,.0f} | VAH=${self.volume_profile.vah:,.0f} | VWAP=${self.volume_profile.vwap:,.2f}")
        
        print(f"  [{self.timeframe}] Features: GK_Vol={gk_vol:.6f}, Hurst={hurst:.3f}, LogRet={log_ret:.6f}")
        
        # Phase 109: Adaptive Regime Classification (percentile-based with hysteresis)
        vol_regime, trend_regime = self.adaptive_regime.update(z_score, hurst)
        regime_name = vol_regime
        
        # Snapshot Candle State for All Modules (Phase 48 Unified)
        open_price = float(candle["open"])
        is_green = close > open_price
        is_red = close < open_price
        local_delta = self.footprint.current.cumulative_delta
        delta = global_delta_synthetic # Single Source of Truth for logical engines
        vp_context = self.volume_profile.get_context(close)
        footprint_text = self.footprint.format_for_gemini()
        
        # Phase 109: Adaptive Circuit Breaker (Hawkes intensity + Delta directional context)
        # Must be AFTER delta assignment
        self.candle_count += 1
        h_lambda = getattr(self.microstructure, 'hawkes_lambda', 0.0)
        circuit_breaker = self.adaptive_cb.evaluate(h_lambda, delta, self.candle_count, z_score)
            
        print(f"  [{self.timeframe}] Regime: Vol={vol_regime} (Z={z_score:.2f}) | Trend={trend_regime} (H={hurst:.3f}) | CB={circuit_breaker}")
        print(f"  [{self.timeframe}] 📊 Order Flow (LOCAL): Delta={local_delta:+.3f} BTC")
        print(f"  [{self.timeframe}] 🌎 Global Unity: Synthetic Delta={delta:+.3f} BTC | Vol={global_vol_synthetic:.1f} BTC")

        # =================================================================
        # PHASE 71: Unrestricted Displacement Circuit Breaker Override
        # =================================================================
        micro_intensity = getattr(self.microstructure, "current_intensity", 0.0)
        # Fallback to calculating intensity if not tracked in real-time on candle close
        if micro_intensity == 0.0:
             micro_metrics = self.microstructure.calculate_metrics(close, delta)
             micro_intensity = micro_metrics.intensity

        unrestricted_displacement = False
        if circuit_breaker and micro_intensity > 150000.0:
            did_ratio = abs(delta) / micro_intensity if micro_intensity > 0 else 0
            # DID > 0.002 = Breakout, DID < 0.002 = Absorption Climax
            if did_ratio >= 0.002:
                print(f"  [{self.timeframe} 🌊] UNRESTRICTED DISPLACEMENT OVERRIDE: Intensity {micro_intensity:.0f}, DID {did_ratio:.4f}")
                print(f"  [{self.timeframe} 🔓] Circuit Breaker Disabled. Breakout is structurally validated.")
                circuit_breaker = False
                unrestricted_displacement = True

        # =================================================================
        # ADAPTIVE STRUCTURAL RESET (Phase 46)
        # =================================================================
        # If transitioning to CRISIS, apply aggressive decay to prioritize fresh discovery
        if regime_name == "CRISIS" and self.prev_regime != "CRISIS":
            print(f"  [{self.timeframe}] ⚡ REGIME TRANSITION: {self.prev_regime or 'None'} -> CRISIS. Applying Structural Soft-Reset (Decay 0.3).")
            self.volume_profile.decay(0.3)
        
        self.prev_regime = regime_name
            
        # FETCH CONTEXT EARLY (Phase 13: Bias-Aware)
        await asyncio.gather(
            self.market_context.get_context(),
            self.multi_tf.update()
        )
        htf_bias = self.multi_tf.current.overall_bias
        market_context_text = self.market_context.format_for_gemini()
        mtf_text = self.multi_tf.format_for_gemini()
        
        print(f"  [{self.timeframe}] 📈 HTF Bias: {htf_bias.value} ({self.multi_tf.current.alignment_score})")
        print(f"  [{self.timeframe}] 🌍 Context: Funding={self.market_context.current.funding_rate_pct}, F&G={self.market_context.current.fear_greed_value}")
        
        # =================================================================
        # HISTORICAL CHART MEMORY (Phase 60: Zone Decays & Creation)
        # =================================================================
        self.chart_memory.apply_probabilistic_decay()
        
        # Generate new zones on extreme CVD Shocks (Massive absorption)
        # Evaluated via dynamically scaled Z-Score & Fractional Power-Law
        z_high = candle["high"]
        z_low = candle["low"]
        # The POC (Point of Control) is the VWAP of this specific extreme candle
        z_poc = self.volume_profile.vwap if self.volume_profile.vwap > 0 else close
        
        # Instantiate the wall in memory if statistically valid
        self.chart_memory.evaluate_cvd_shock(delta, z_high, z_low, z_poc, timeframe=self.timeframe)
            
        print(self.chart_memory.get_active_zone_summary())

        # Check circuit breaker (only for 15m, 5m scalping uses volatility)
        # RELAXED: User requested to allow trading in CRISIS regime on 15m unless Circuit Breaker
        if circuit_breaker:
            print(f"  [{self.timeframe}] ⚠️ CIRCUIT BREAKER ACTIVE - Closing any positions")
            if self.position_manager.position:
                await self._close_position(close, "CIRCUIT_BREAKER")
            return
        elif regime_name == "CRISIS":
            # PHASE 18: Directional Crisis Shield
            crisis_long_allowed = False
            crisis_short_allowed = False
            
            TREND_THRESHOLD = 50.0
            is_bias_long = htf_bias in [TrendDirection.BULLISH, TrendDirection.STRONG_BULLISH]
            is_bias_short = htf_bias in [TrendDirection.BEARISH, TrendDirection.STRONG_BEARISH]
            
            # Phase 15 Safety
            is_safe_macro_long = is_bias_long and not (vp_context == "PREMIUM" and self.order_book.obi < 0.5)
            is_safe_macro_short = is_bias_short and not (vp_context == "DISCOUNT" and self.order_book.obi > -0.5)

            # LONG VALIDATION: Proof of Push (Green) + (Wall OR Momentum OR Macro)
            if is_green:
                if (self.order_book.obi > 0.2 and delta > 0) or \
                   (delta > TREND_THRESHOLD and self.order_book.obi > -0.2) or \
                   (is_safe_macro_long and self.order_book.obi > -0.1): 
                    crisis_long_allowed = True
                    print(f"  [{self.timeframe}] ⚡ CRISIS LONG ALLOWED: OBI={self.order_book.obi:.2f}, Delta={delta:.1f}, Zone={vp_context}")
                
            # SHORT VALIDATION: Proof of Push (Red) + (Wall OR Momentum OR Macro)
            if is_red:
                if (self.order_book.obi < -0.2 and delta < 0) or \
                   (delta < -TREND_THRESHOLD and self.order_book.obi < 0.2) or \
                   (is_safe_macro_short and self.order_book.obi < 0.1):
                    crisis_short_allowed = True
                    print(f"  [{self.timeframe}] ⚡ CRISIS SHORT ALLOWED: OBI={self.order_book.obi:.2f}, Delta={delta:.1f}, Zone={vp_context}")
            
            self.crisis_long_allowed = crisis_long_allowed
            self.crisis_short_allowed = crisis_short_allowed
            self.crisis_reason = f"Bias={htf_bias.value}, OBI={self.order_book.obi:.2f}, Push={'Green' if is_green else 'Red'}"
            
            if not (crisis_long_allowed or crisis_short_allowed):
                if self.relaxed_hmm:
                    print(f"  [{self.timeframe}] 🛡️ RELAXED HMM: CRISIS detected. Proceeding with EXTREME filters.")
                    # We continue, but we'll use much tighter OBI/Delta checks below
                else:
                    print(f"  [{self.timeframe}] ⛔ CRISIS REGIME: Blocked ({self.crisis_reason})")
                    return
        
        # Bypass trade evaluations for historical backfilled candles
        if (datetime.utcnow() - ts).total_seconds() > 3600:
            self.footprint.reset()
            return
            
        # Check existing position for SL/TP
        if self.position_manager.position:
            await self._check_exit_conditions(close, regime_name, gk_vol, hurst)
            self.footprint.reset()  # Reset footprint for new candle
            return  # Already in position, wait for exit
        
        # Can we trade?
        can_trade, reason = self.position_manager.can_trade()
        if not can_trade:
            print(f"  [{self.timeframe}] ❌ Cannot trade: {reason}")
            self.footprint.reset()
            return
        
        # 0. MICROSTRUCTURE ANALYSIS (Phase 40/48)
        # Move this up so Sniper can use intensity for dynamic trend thresholds
        micro_metrics = self.microstructure.calculate_metrics(close, delta)
        
        # =================================================================
        # SNIPER ENTRY FILTER (Ping Pong + Trend Integration)
        # =================================================================
        # Pass intensity to enable dynamic trend/breakout detection
        sniper_result = self._check_sniper_entry(close, delta, intensity=micro_metrics.intensity)
        
        # PHASE 18: Lockdown & Absorption Guard
        if sniper_result["should_trade"]:
            direction = sniper_result["direction"]
            
            # 1. HIERARCHICAL CONSENSUS (Phase 51 Hard Shield)
            # Ensure the 30m Chart agrees with the 15m Anchor
            macro_ok, macro_reason = await self._check_macro_consensus(direction)
            if not macro_ok:
                print(f"  [{self.timeframe}] 🛡️ MACRO SHIELD: {direction} blocked. Reason: {macro_reason}")
                sniper_result["should_trade"] = False
                sniper_result["reason"] = f"Macro Bias Conflict ({macro_reason})"
            
            # 2. DIRECTIONAL LOCKDOWN (For Crisis Regime)
            if sniper_result["should_trade"] and regime_name == "CRISIS":
                if unrestricted_displacement:
                    print(f"  [{self.timeframe} 🌊] Directional Lockdown bypassed via Unrestricted Displacement.")
                else:
                    shield_reason = getattr(self, "crisis_reason", "Safety Profile")
                    if direction == "LONG" and not self.crisis_long_allowed:
                        print(f"  [{self.timeframe}] ⏸️ Sniper LONG blocked by CRISIS SHIELD ({shield_reason})")
                        sniper_result["should_trade"] = False
                    elif direction == "SHORT" and not self.crisis_short_allowed:
                        print(f"  [{self.timeframe}] ⏸️ Sniper SHORT blocked by CRISIS SHIELD ({shield_reason})")
                        sniper_result["should_trade"] = False
            
            # 3. ABSORPTION GUARD 2.0 (Phase 109: Adaptive Absorption Threshold)
            if sniper_result["should_trade"]:
                # Phase 109: Rolling P90 + regime penalty (replaces fixed 0.85)
                is_absorbing = self.adaptive_absorption.is_absorbing(
                    micro_metrics.absorption_probability, self.prev_regime or 'NORMAL'
                )
                
                if is_absorbing:
                    print(f"  [{self.timeframe}] 🛡️ ABSORPTION GUARD 2.0: High Lambda Anomaly ({micro_metrics.absorption_probability*100:.0f}% chance). Blocking {direction}.")
                    sniper_result["should_trade"] = False
                    sniper_result["reason"] = f"Passive Absorption Detected ({micro_metrics.absorption_probability*100:.0f}%)"
                
                # Phase 112: Replace binary candle color with CPR + Delta Override
                is_pre_emptive = "PRE-EMPTIVE" in sniper_result.get("reason", "")
                hawkes_z = getattr(self, '_hawkes_z', 0.0)
                o_price = float(candle['open']) if 'candle' in dir() else close  # Get candle open
                h_price = float(candle['high']) if 'candle' in dir() else close
                l_price = float(candle['low']) if 'candle' in dir() else close
                
                color_allowed, color_reason, cpr_val = self.adaptive_cpr.evaluate_and_update(
                    direction, o_price, h_price, l_price, close,
                    delta, hawkes_z, is_pre_emptive=is_pre_emptive
                )
                
                if not color_allowed:
                    print(f"  [{self.timeframe}] ⚠️ ABSORPTION (P112): {color_reason}. CPR={cpr_val:.2f}. Blocking {direction}.")
                    sniper_result["should_trade"] = False
                elif is_red and direction == "LONG":
                    print(f"  [{self.timeframe}] 🟢 ABSORPTION BYPASSED (P112): {color_reason}. CPR={cpr_val:.2f} ✅")
                elif is_green and direction == "SHORT":
                    print(f"  [{self.timeframe}] 🟢 ABSORPTION BYPASSED (P112): {color_reason}. CPR={cpr_val:.2f} ✅")

        if sniper_result["should_trade"]:
            # Phase 66: DYNAMIC DELTA CONFIRMATION GATE (Candle Close Path)
            kyle_mean = float(np.mean(self.microstructure.lambda_buffer)) if len(self.microstructure.lambda_buffer) > 5 else 0.001
            kyle_current = micro_metrics.kyles_lambda or kyle_mean
            self.delta_threshold.integrate_hawkes(micro_metrics.hawkes_lambda)
            dyn_thresh = self.delta_threshold.get_threshold(
                hurst=hurst, z_gk=z_score,
                kyle_current=kyle_current, kyle_mean_24h=kyle_mean,
            )
            if abs(delta) < dyn_thresh:
                print(f"  [{self.timeframe} 🔒] DELTA GATE: |{abs(delta):.1f}| < Θ={dyn_thresh:.1f} BTC. Insufficient conviction.")
                sniper_result["should_trade"] = False
                sniper_result["reason"] = f"Delta Gate: |{abs(delta):.1f}| < Θ={dyn_thresh:.1f}"

        if sniper_result["should_trade"]:
            print(f"  [{self.timeframe}] 🎯 SNIPER ENTRY: {sniper_result['direction']} at ${close:,.2f}")
            print(f"     Value Area: ${sniper_result['range_low']:,.0f} - ${sniper_result['range_high']:,.0f} (POC: ${sniper_result['poc']:,.0f})")
            print(f"     Position: {sniper_result['position_in_range']}")
        else:
            if not sniper_result.get("reason"): sniper_result["reason"] = "Safety Guard Triggered"
            print(f"  [{self.timeframe}] ⏸️ Sniper filter: {sniper_result['reason']}")
            self.footprint.reset()
            return
        
        # Get order book context
        order_book_text = self.order_book.format_for_gemini()
        current_obi = self.order_book.obi_macro
        top_obi = self.order_book.obi_top
        print(f"  [{self.timeframe}] 📚 Order Book: Macro={current_obi:+.3f}, Top={top_obi:+.3f}")
        print(f"  [{self.timeframe}] 🔬 Micro: OFI={micro_metrics.ofi:+.2f}, Iceberg={micro_metrics.iceberg_score:.2f}, Intensity={micro_metrics.intensity:.1f}")
        
        # =================================================================
        # PREDATOR MODE: Context-Aware Metric Substitution (Phase 110)
        # =================================================================
        # Research: OBI structurally inverts during breakouts (market makers
        # replenish asks during bull runs). During momentum regimes, we
        # substitute OBI with Trade Flow Imbalance (TFI) which measures
        # aggressive executed flow, not passive resting liquidity.
        
        direction = sniper_result["direction"]
        
        # 1. SPOOFING GUARD: Hollow Wall Detection (ALWAYS runs first)
        is_hollow = False
        if direction == "LONG" and top_obi > 0.3:
            if current_obi < (top_obi * 0.9): is_hollow = True
        elif direction == "SHORT" and top_obi < -0.3:
            if current_obi > (top_obi * 0.9): is_hollow = True
            
        if is_hollow:
            print(f"  [{self.timeframe}] 🛡️ SPOOFING GUARD: Hollow Wall detected (Top {top_obi:.2f} vs Macro {current_obi:.2f}). Aborting.")
            self.footprint.reset()
            return

        # 2. Phase 111: Self-Calibrating Breakout Detection
        hawkes_z = getattr(self, '_hawkes_z', 0.0)
        delta_p90 = float(np.percentile(list(self.adaptive_trend.delta_history), 90)) if len(self.adaptive_trend.delta_history) > 20 else 500.0
        
        is_htf_aligned = (
            (direction == "LONG" and htf_bias in [TrendDirection.BULLISH, TrendDirection.STRONG_BULLISH]) or
            (direction == "SHORT" and htf_bias in [TrendDirection.BEARISH, TrendDirection.STRONG_BEARISH])
        )
        
        # Rolling P95 replaces fixed Z > 2.0 (adapts to heavy-tailed Hawkes distribution)
        is_breakout_regime, z_thresh = self.adaptive_breakout.evaluate_and_update(
            hawkes_z, abs(delta), delta_p90, is_htf_aligned
        )
        
        # 3. METRIC SUBSTITUTION: TFI during breakouts, OBI during ranging
        if is_breakout_regime:
            vol_buy = self.footprint.current.total_buy_volume
            vol_sell = self.footprint.current.total_sell_volume
            total_vol = vol_buy + vol_sell
            
            if total_vol > 0:
                if direction == "LONG":
                    tfi = (vol_buy - vol_sell) / total_vol
                else:
                    tfi = (vol_sell - vol_buy) / total_vol
                
                # Phase 111: Rolling P80 TFI threshold (adapts to session volume)
                is_tfi_valid, tfi_threshold = self.adaptive_tfi.evaluate_and_update(tfi)
                
                if is_tfi_valid:
                    print(f"  [{self.timeframe}] 🔥 PREDATOR BYPASS (Phase 111): Breakout (Hz={hawkes_z:.1f}>{z_thresh:.1f}, Δ={delta:.0f}>{delta_p90:.0f}). TFI={tfi:.2f} ≥ {tfi_threshold:.2f} ✅")
                else:
                    print(f"  [{self.timeframe}] ⏸️ Predator (TFI Mode): TFI {tfi:.2f} < {tfi_threshold:.2f} (P80). Blocking {direction}.")
                    self.footprint.reset()
                    return
            else:
                print(f"  [{self.timeframe}] ⏸️ Predator: Zero volume in breakout — fakeout. Blocking.")
                self.footprint.reset()
                return
        else:
            # Phase 111: ECDF-based multipliers (replaces rv*10 and hurst-0.5)
            rv_mult, hurst_mult = self.adaptive_mults.evaluate_and_update(gk_vol, hurst)
            
            # Phase 111: Self-calibrating sigmoid (replaces OBI_MIN/MAX/K/L_MID)
            PREDATOR_OBI_THRESHOLD = self.adaptive_sigmoid.get_adaptive_threshold(
                hawkes_z, current_obi, rv_mult=rv_mult, hurst_mult=hurst_mult
            )
            print(f"  [{self.timeframe}] 📉 ADAPTIVE OBI (P111): thresh={PREDATOR_OBI_THRESHOLD:.2f} (Hz={hawkes_z:.1f}, RVm={rv_mult:.2f}, Hm={hurst_mult:.2f})")
            
            if direction == "LONG" and current_obi < PREDATOR_OBI_THRESHOLD:
                dir_msg = "Wrong side" if current_obi < 0 else "Too weak"
                print(f"  [{self.timeframe}] ⏸️ Predator Filter: OBI {current_obi:.2f} ({dir_msg}) for LONG (needs {PREDATOR_OBI_THRESHOLD:.2f})")
                self.footprint.reset()
                return
            elif direction == "SHORT" and current_obi > -PREDATOR_OBI_THRESHOLD:
                dir_msg = "Wrong side" if current_obi > 0 else "Too weak"
                print(f"  [{self.timeframe}] ⏸️ Predator Filter: OBI {current_obi:.2f} ({dir_msg}) for SHORT (needs -{PREDATOR_OBI_THRESHOLD:.2f})")
                self.footprint.reset()
                return

        # Phase 105: Construct deterministic TradeSignal from Sniper + Macro Regime
        # (replaces the deleted synchronous Gemini analyze() call)
        macro_bias = getattr(self.current_macro_regime, 'bias', 'NEUTRAL')
        macro_mult = getattr(self.current_macro_regime, 'hysteresis_multiplier', 1.0)
        
        if direction == "LONG":
            action_enum = TradeAction.GO_LONG
            conf = min(0.99, 0.85 * macro_mult) if macro_bias in ["BULLISH", "NEUTRAL"] else 0.3
        elif direction == "SHORT":
            action_enum = TradeAction.GO_SHORT
            conf = min(0.99, 0.85 * macro_mult) if macro_bias in ["BEARISH", "NEUTRAL"] else 0.3
        else:
            action_enum = TradeAction.STAY_FLAT
            conf = 0.0
        
        # Phase 107: Classify entry type from sniper path
        sniper_reason = sniper_result.get('reason', '')
        if 'PoNR' in sniper_reason:
            _entry_type = 'PoNR_Expansion'
        elif sniper_result.get('position', '') in ('DISCOUNT', 'PREMIUM'):
            _entry_type = 'Mean_Reversion'
        else:
            _entry_type = 'Trend_Breakout'
        
        # Phase 107: Dynamic TP/SL from regime-adaptive framework
        _tp_pct, _sl_pct = self.dynamic_exit.calculate(
            atr=max(self.atr, 1.0),
            entry_price=close,
            regime=regime_name,
            hurst=hurst,
            gk_vol=gk_vol,
            entry_type=_entry_type
        )
        signal = TradeSignal(
            action=action_enum,
            confidence=conf,
            reasoning=f"Deterministic Quantitative Core (Sniper: {direction}, Macro: {macro_bias}, Mult: {macro_mult:.1f}, Entry: {_entry_type})",
            stop_loss_pct=_sl_pct,
            take_profit_pct=_tp_pct
        )
        print(f"  [{self.timeframe}] 📊 Phase 107 TP/SL: TP={_tp_pct*100:.2f}% SL={_sl_pct*100:.2f}% (Entry: {_entry_type}, Regime: {regime_name})")

        print(f"  Quantitative Core Action: {signal.action.value} (Confidence: {signal.confidence:.2f})")
        print(f"  Reasoning: {signal.reasoning[:100]}...")
        
        self.current_signal = signal
        
        # Reset footprint for next candle
        self.footprint.reset()
        
        # Execute if confident AND matches sniper direction
        executed = False
        skip_reason = ""
        
        # Phase 48: Regime-Adaptive Confidence
        # Tighten threshold in CIRCUIT BREAKER (panic), use baseline in others
        adaptive_threshold = self.confidence_threshold
        if circuit_breaker:
            adaptive_threshold += 0.1
            print(f"  [{self.timeframe}] 🛡️ CIRCUIT BREAKER ACTIVE: Tightening Gemini Threshold to {adaptive_threshold:.2f}")
        elif regime_name == "CRISIS":
            adaptive_threshold += 0.1
            print(f"  [{self.timeframe}] 🛡️ CRISIS SHIELD: Tightening Gemini Threshold to {adaptive_threshold:.2f}")
            
        if signal.should_execute(adaptive_threshold):
            # Final check: Gemini agrees with sniper direction
            gemini_long = signal.action == TradeAction.GO_LONG
            gemini_short = signal.action == TradeAction.GO_SHORT
            sniper_long = sniper_result["direction"] == "LONG"
            sniper_short = sniper_result["direction"] == "SHORT"
            
            if (gemini_long and sniper_long) or (gemini_short and sniper_short):
                # Phase 89.3/90 Structural Guard for HTF Entry (Dynamic Theta)
                is_conflicting = False
                theta = self.current_shm_state.lead_lag_theta if self.current_shm_state else 0.0
                
                # Fetch Dynamic Threshold & State Variables
                current_obi = self.order_book.obi_macro
                dyn_theta_thresh = self._calculate_dynamic_theta_threshold(regime_name, theta, micro_metrics.intensity, current_obi)
                
                # Phase 91: Dynamic Volume Delta Veto (replaces +/- 50.0 static check)
                hmm_base_limit = 5.0 if regime_name == "CRISIS" else (3.5 if regime_name == "TRANSITION" else 2.0)
                norm_kyle_lambda = 0.0 # Placeholder until full OBI integration
                intended_side = 1 if gemini_long else (-1 if gemini_short else 0)
                
                is_delta_vetoed = self.dynamic_volume_veto.check_execution_safety(
                    current_volume_delta=delta,
                    hmm_base_limit=hmm_base_limit,
                    obi=current_obi,
                    norm_kyle_lambda=norm_kyle_lambda,
                    intended_side=intended_side
                )
                
                if is_delta_vetoed:
                    is_conflicting = True
                    expected_dir = "Buy" if gemini_short else "Sell"
                    print(f"  [{self.timeframe} 🛡️] HTF VETO: Order blocked by Phase 91 Dynamic Volume Imbalance Shield! (Local Delta: {delta:.1f} BTC against intended position)")

                elif gemini_short and theta > dyn_theta_thresh:
                    is_conflicting = True
                    print(f"  [{self.timeframe} 🛡️] HTF VETO: Short blocked by Bullish Lead-Lag Alpha (Theta +{theta:.2f} > DynThresh {dyn_theta_thresh:.2f})")
                elif gemini_long and theta < -dyn_theta_thresh:
                    is_conflicting = True
                    print(f"  [{self.timeframe} 🛡️] HTF VETO: Long blocked by Bearish Lead-Lag Alpha (Theta {theta:.2f} < -DynThresh {-dyn_theta_thresh:.2f})")
                    
                if not is_conflicting:
                    self.entry_delta = delta  # Store for trade logging
                    self.entry_time = datetime.now()
                    await self._execute_signal(signal, close)
                    executed = True
                else:
                    skip_reason = "Phase 89.3 HTF Delta Veto"
            else:
                skip_reason = "Gemini vs Sniper conflict"
                print(f"  ⚠️ Gemini vs Sniper conflict ({signal.action.value} vs {sniper_result['direction']}) - skipping trade")
        else:
            if signal.action == TradeAction.STAY_FLAT:
                skip_reason = "Gemini STAY_FLAT"
                print(f"  ⏸️ Gemini chose to STAY_FLAT")
            else:
                skip_reason = f"Confidence below threshold ({adaptive_threshold})"
                print(f"  ⏸️ Confidence ({signal.confidence:.2f}) below threshold ({adaptive_threshold})")
        
        # Log the signal (executed or not)
        self.logger.log_signal(
            timeframe=self.timeframe,
            price=close,
            regime=regime_name,
            delta=delta,
            obi=current_obi,
            intensity=micro_metrics.intensity,
            sniper_result=sniper_result,
            gemini_action=signal.action.value,
            gemini_confidence=signal.confidence,
            gemini_reasoning=signal.reasoning,
            executed=executed,
            skip_reason=skip_reason
        )
        
        # Update state for next evaluation
        self.prev_delta = delta

        # =================================================================
        # PHASE 82: COGNITIVE INJECTION (Supervisor Path)
        # =================================================================
        await self._update_cognitive_injection(candle, delta)

    def _calculate_dynamic_theta_threshold(self, current_regime: str, raw_theta: float, current_intensity: float, current_obi: float) -> float:
        """
        Phase 90 Dynamic Theta Veto Threshold (O(1) calculation).
        Computes a non-linear, environmentally-aware magnitude required to trigger an algorithmic veto.
        """
        # 1. HMM Baseline Expectation
        if current_regime == "CRISIS":
            base_threshold = 1.8
        elif current_regime == "TRANSITION":
            base_threshold = 2.5
        else: # NORMAL / Default
            base_threshold = 3.5

        # 2. Hawkes Inflation Factor (H_inf)
        # Assuming moving baseline intensity is roughly 25,000 for standard volume
        lambda_base = 25000.0
        intensity_ratio = max(0.0, (current_intensity - lambda_base) / lambda_base)
        # If intensity spikes from 25k to 100k, ratio is 3.0. ln(1 + 3) = ln(4) = 1.38. H_inf = 1 + 0.5 * 1.38 = 1.69
        h_inf = 1.0 + (0.5 * math.log1p(intensity_ratio))

        # 3. OBI Confluence Multiplier (O_conf)
        theta_sign = 1.0 if raw_theta >= 0.0 else -1.0
        # If theta is +4 (Bullish) and OBI is +0.8 (Bullish), o_conf = 1.0 - (0.3 * +1 * +0.8) = 1.0 - 0.24 = 0.76 (Tighten)
        # If theta is +4 (Bullish) and OBI is -0.8 (Bearish), o_conf = 1.0 - (0.3 * +1 * -0.8) = 1.0 + 0.24 = 1.24 (Widen)
        o_conf = 1.0 - (0.3 * theta_sign * current_obi)

        # 4. Compute Final Dynamic Threshold (absolute magnitude)
        dynamic_threshold = base_threshold * h_inf * o_conf
        
        return dynamic_threshold

    async def _update_cognitive_injection(self, candle: dict, delta: float):
        """
        Phase 82: Cognitive Injection (VPIN + Gemini Sentiment).
        Calculates empirical toxicity and injects cognitive scalars into C++ core.
        """
        try:
            # 1. Calculate Empirical VPIN Proxy (Simplified Bulk Volume Classification)
            volume = candle["volume"]
            if volume > 0:
                # v_buy - v_sell = delta
                # v_buy + v_sell = volume
                v_buy = (volume + delta) / 2.0
                v_sell = (volume - delta) / 2.0
                vpin_proxy = abs(v_buy - v_sell) / volume
            else:
                vpin_proxy = 0.0

            # 2. Ask Gemini for Semantic Toxicity and Sentiment (Psi_Gemini & sigma_sen)
            market_context_text = self.market_context.format_for_gemini()
            cog_signal = await self.gemini.analyze_cognition(vpin_proxy, market_context_text)
            
            # 3. Hybrid Toxicity Scalar (Omega = 0.6)
            # T_s = omega * VPIN_proxy + (1 - omega) * Psi_Gemini
            omega = 0.6
            toxicity_scalar = (omega * vpin_proxy) + ((1.0 - omega) * cog_signal.semantic_toxicity)
            sentiment_score = cog_signal.sentiment_score
            
            print(f"  [{self.timeframe} 🧠] COGNITIVE INJECTION: TS={toxicity_scalar:.3f}, Sentiment={sentiment_score:+.2f}")
            print(f"  [{self.timeframe} 🧠] Reasoning: {cog_signal.reasoning[:80]}...")
            
            # 4. Inject into C++ Core via SPSC Bridge
            self.control_bridge.update_params(
                obi_threshold=float(os.getenv("OBI_THRESHOLD", 0.6)),
                toxicity_scalar=toxicity_scalar,
                sentiment_score=sentiment_score,
                z_base=getattr(self.current_macro_regime, 'z_base', 3.0),
                scaling_gamma=getattr(self.current_macro_regime, 'gamma', 0.5),
                gobi_kappa=getattr(self.current_macro_regime, 'kappa', 0.5),
                is_trading_enabled=True
            )
            
        except Exception as e:
            print(f"  [{self.timeframe} ⚠️] Cognitive Injection Error: {e}")
    
    async def _on_live_candle_update(self, candle: dict):
        """Called on live candle updates - for real-time risk management and Memory tracking."""
        price = candle["close"]
        volume = candle["volume"] # Needed for S&D Memory Absorption tracker
        
        # Phase 60: Update Global Memory Clock and Interaction (For Absorption Invalidations)
        # Compute marginal volume & delta so we don't accidentally add the entire cumulative body every tick
        current_cum_delta = self.footprint.current.cumulative_delta
        
        marginal_vol = volume - getattr(self, "prev_tick_vol", 0.0)
        marginal_delta = current_cum_delta - getattr(self, "prev_tick_delta", 0.0)
        
        # Reset trackers if a new candle started
        if candle["ts"] != getattr(self, "prev_tick_ts", None):
             marginal_vol = volume
             marginal_delta = current_cum_delta
             
        self.prev_tick_vol = volume
        self.prev_tick_delta = current_cum_delta
        self.prev_tick_ts = candle["ts"]
        
        # We'll use absolute marginal delta size to estimate aggressor volume.
        agg_vol = abs(marginal_delta)
        is_buy = marginal_delta > 0
        
        self.chart_memory.update_global_clock(marginal_vol)
        self.chart_memory.process_zone_interaction(price, agg_vol, is_buy)

        # Phase 66: Integrate Hawkes intensity (throttled to ~1/sec to avoid CPU overload during nukes)
        import time as _time
        _now = _time.time()
        if _now - getattr(self, '_last_hawkes_int', 0) >= 1.0:
            self.delta_threshold.integrate_hawkes(self.microstructure.hawkes_lambda)
            self._last_hawkes_int = _now

        # Phase 51: Entry Checks (if NOT in position)
        if not self.position_manager.position:
            # 1. Evaluate Mid-Candle Flashpoint Entry
            await self._check_flashpoint_entry(price, self.footprint.current.cumulative_delta, self.microstructure.intensity)
            return

        # If we ARE in a position, run risk management...
        pnl = self.position_manager.update_unrealized_pnl(price)
        # Use leveraged PnL (ROE%) to match Binance App
        roe_pct = self.position_manager.get_profit_pct(price, leveraged=True)
        profit_pct = self.position_manager.get_profit_pct(price, leveraged=False)
        
        # Log to console without newline
        print(f"  [{self.timeframe} Live] ${price:,.2f} | ROE: {roe_pct*100:+.2f}%", end="\r")
        
        # =================================================================
        # MID-CANDLE PROTECTOR (Phase 14: Emergency Escapes)
        # =================================================================
        
        # 0. MID-CANDLE TARGET HARVEST (Phase 68: Structural Exits)
        if profit_pct >= 0.005 and not getattr(self.position_manager.position, "mid_candle_tp_hit", False):
            target_hit = False
            is_long = self.position_manager.position.side.value == "LONG"
            is_short = self.position_manager.position.side.value == "SHORT"
            
            # 1. Check Volume Profile Targets (POC/VAH/VAL)
            poc = getattr(self.volume_profile, "poc", 0.0)
            vah = getattr(self.volume_profile, "vah", 0.0)
            val = getattr(self.volume_profile, "val", 0.0)
            
            if is_long and ((vah > 0 and price >= vah) or (poc > 0 and self.position_manager.position.entry_price < poc and price >= poc)):
                target_hit = True
            elif is_short and ((val > 0 and price <= val) or (poc > 0 and self.position_manager.position.entry_price > poc and price <= poc)):
                target_hit = True
                
            # 2. Check Liquidation Magnets (within 0.1% buffer)
            if not target_hit and hasattr(self.magnet, "magnets") and self.magnet.magnets:
                for mag_price, density in self.magnet.magnets[:2]:
                    if abs(price / mag_price - 1.0) < 0.001:
                        target_hit = True
                        break
                        
            if target_hit:
                self.position_manager.position.mid_candle_tp_hit = True
                self.position_manager.position.partial_tp_hit = True # Prevent double-trigger at candle close
                print(f"\n  [{self.timeframe} 🎯] MID-CANDLE TARGET HIT: Structural Resistance Reached at ${price:,.2f}!")
                # Harvest 50% immediately off the table
                await self._partial_close_position(price, 0.5, "MID_CANDLE_STRUCTURAL_TARGET")
        
        # 1. FIXED STOP LOSS (Emergency Exit)
        FIXED_SL_PCT = 0.01
        if profit_pct <= -FIXED_SL_PCT:
            print(f"\n  [{self.timeframe}] 🚨 EMERGENCY STOP LOSS hit at {profit_pct*100:.2f}%")
            await self._close_position(price, "LIVE_STOP_LOSS")
            return

        # 2. TRAILING STOP (Profit Protection)
        if self.position_manager.check_trailing_stop(price):
            print(f"\n  [{self.timeframe}] 🛑 TRAILING STOP hit at ${price:,.2f}")
            await self._close_position(price, "LIVE_TRAILING_STOP")
            return

        # 4. URGENT FLOW ESCAPE (Scalable Breakdown Detection - Phase 48)
        delta = self.footprint.current.cumulative_delta
        intensity = self.microstructure.intensity
        is_short = self.position_manager.position.side.value == "SHORT"
        is_long = self.position_manager.position.side.value == "LONG"
        
        # SCALABLE THRESHOLD: Higher timeframe or higher intensity = wider leash
        base_threshold = 150.0
        
        # Scale by intensity (e.g., at 170k intensity, threshold doubles)
        import math
        intensity_mult = 1.0 + math.log1p(intensity / 25000.0)
        urgent_threshold = base_threshold * intensity_mult
        
        if is_short and delta > urgent_threshold:
            print(f"\n  [{self.timeframe}] ⚠️ URGENT ESCAPE: Massive Buying detected ({delta:+.1f} BTC > {urgent_threshold:.1f} limit). Aborting.")
            await self._close_position(price, "URGENT_FLOW_ESCAPE")
        elif is_long and delta < -urgent_threshold:
            print(f"\n  [{self.timeframe}] ⚠️ URGENT ESCAPE: Massive Selling detected ({delta:+.1f} BTC < -{urgent_threshold:.1f} limit). Aborting.")
            await self._close_position(price, "URGENT_FLOW_ESCAPE")
            return
            
        # =================================================================
        # 5. CONTINUOUS GEMINI AI EXIT POLLING (Phase 69) - REMOVED (Phase 101)
        # =================================================================
        # This was blocking the high-frequency hot-path, causing VWAP/Hawkes drops.
        # AI Exits are now handled asynchronously if needed or via quantitative stops.
                    
    async def _on_sentinel_update(self, data: dict):
        """Handle tick data for BTC and SOL Lead-Lag analysis (Phase 56)."""
        symbol = data["symbol"]
        price = data["price"]
        ts = data["ts"]
        
        # Add to corresponding buffer for Lead-Lag (Tick level)
        if symbol == "BTCUSDT":
            self.btc_ticks.append((ts, price))
            # Phase 57: Store detailed trade data for Liquidation Magnet mapping
            self.btc_trade_buffer.append(data)
        else:
            self.sol_ticks.append((ts, price))
            
            # Check for sudden impulse in SOL (Early Warning)
            # If SOL moves >0.15% in <5s, it might be a lead impulse
            if len(self.sol_ticks) > 10:
                past_ts, past_price = self.sol_ticks[-10]
                if (ts - past_ts).total_seconds() < 5:
                    sol_ret = (price / past_price) - 1.0
                    if abs(sol_ret) >= 0.0015: # 0.15% impulse
                        signal = self.sentinel.evaluate_signal(sol_ret, self.sentinel_stats)
                        if signal != "NEUTRAL":
                            print(f"  [{self.timeframe} 📡] SENTINEL IMPULSE: SOL leads {signal} impulse! (Ret: {sol_ret*100:.2f}%)")
        
        # Keep Lead-Lag buffers lean (Last 2 minutes) using deque.popleft() which is O(1)
        short_cutoff = ts - timedelta(minutes=2)
        while self.btc_ticks and self.btc_ticks[0][0] < short_cutoff:
            self.btc_ticks.popleft()
        while self.sol_ticks and self.sol_ticks[0][0] < short_cutoff:
            self.sol_ticks.popleft()
            
        # Keep Magnet buffer lean (Last 10 minutes)
        # Cap strictly at 10,000 items to prevent ProcessPool pickling overhead from freezing the bot
        long_cutoff = ts - timedelta(minutes=10)
        while self.btc_trade_buffer and self.btc_trade_buffer[0]["ts"] < long_cutoff:
            self.btc_trade_buffer.popleft()
        while len(self.btc_trade_buffer) > 10000:
            self.btc_trade_buffer.popleft()

    @staticmethod
    def _compute_sentinel_and_magnets(btc_ticks, sol_ticks, btc_trades, current_price):
        """Isolated heavy-math execution for the ProcessPool."""
        # Instantiate fresh classes inside the worker memory space to prevent socket pickling
        from sentinel_detector import SentinelLeadLagDetector
        from liquidity_magnet import LiquidationMagnetDetector
        
        sentinel_engine = SentinelLeadLagDetector()
        magnet_engine = LiquidationMagnetDetector()
        
        sentinel_result = None
        magnet_results = []
        
        # 1. Lead-Lag Alpha (SOL Sentinel)
        if len(btc_ticks) >= 50 and len(sol_ticks) >= 50:
            btc_df = pd.DataFrame(btc_ticks, columns=["ts", "price"]).set_index("ts")
            sol_df = pd.DataFrame(sol_ticks, columns=["ts", "price"]).set_index("ts")
            sentinel_result = sentinel_engine.compute_lead_lag(btc_df["price"], sol_df["price"])

        # 2. Gravity Alpha (Liquidation Magnets)
        if len(btc_trades) >= 100:
            liq_prices = magnet_engine.estimate_liquidations(btc_trades)
            if magnet_engine.fit_kde_clusters(liq_prices) and current_price and current_price > 0:
                magnet_results = magnet_engine.extract_magnet_zones(current_price)
                
        # Return state array to update main thread values
        return sentinel_result, magnet_results, magnet_engine.magnets

    async def _sentinel_worker(self):
        """Background worker to calculate Lead-Lag and Magnet stats (Phases 56 & 57)."""
        from concurrent.futures import ThreadPoolExecutor
        
        # Phase 67: Heavy KDE is now njit(nogil=True). We can use a fast ThreadPool and completely bypass memory serialization latency.
        with ThreadPoolExecutor(max_workers=1) as pool:
            while self.running:
                try:
                    await asyncio.sleep(30) # Compute every 30s
                    
                    # Take snapshots to avoid altering lists during execution
                    btc_ticks_copy = list(self.btc_ticks)
                    sol_ticks_copy = list(self.sol_ticks)
                    btc_trades_copy = list(self.btc_trade_buffer)
                    current_price = self.prev_close
                    
                    loop = asyncio.get_event_loop()
                    # Run heavy math in a background process
                    sentinel_result, magnet_results, new_magnets_state = await loop.run_in_executor(
                        pool,
                        self._compute_sentinel_and_magnets,
                        btc_ticks_copy, sol_ticks_copy, btc_trades_copy, current_price
                    )
                    
                    # Apply results back to main thread state
                    self.magnet.magnets = new_magnets_state
                    
                    if sentinel_result:
                        self.sentinel_stats = sentinel_result
                        if sentinel_result.get("status") == "success":
                            lead_asset = sentinel_result["leader"]
                            lag = sentinel_result["lag_seconds"]
                            corr = sentinel_result["correlation"]
                            print(f"  [{self.timeframe} \U0001f4e1] SENTINEL STATUS: {lead_asset} leading by {lag:.2f}s (Corr: {corr:.2f})")
                            
                    if magnet_results:
                        primary_magnet, density = magnet_results[0]
                        dist_pct = abs(primary_magnet / current_price - 1.0) * 100
                        side = "ABOVE" if primary_magnet > current_price else "BELOW"
                        print(f"  [{self.timeframe} \U0001f9f2] MAGNET DETECTED: Dense Cluster ${primary_magnet:,.0f} ({side} +{dist_pct:.2f}%)")
                
                except Exception as e:
                    print(f"[Sentinel/Magnet] Worker Error: {e}")
                    await asyncio.sleep(5)

    async def _on_liquidation(self, data: dict):
        """Handle real-time liquidation events (Phase 50)."""
        self.last_liquidation_v_1m = data["v_1m"]
        self.last_liquidation_side = data["side"]
        if data["qty"] > 5.0:
            print(f"\n  [{self.timeframe} 🔥] LIQUIDATION: {data['side']} {data['qty']} BTC @ ${data['price']:,.2f} (1m Total: {self.last_liquidation_v_1m:.2f})")
            
        # If we have a massive liquidation spike, trigger an immediate state check
        if self.last_liquidation_v_1m > 25.0:
            # We don't call _on_candle_close directly (too heavy), 
            # but we let the next live update know to be hyper-aggressive.
            pass

    async def _check_flashpoint_entry(self, close: float, delta: float, intensity: float):
        """
        MID-CANDLE 'FLASHPOINT' ENTRY (Phase 50).
        Evaluates PoNR during extreme volatility without waiting for candle close.
        """
        # 1. Throttle: Only one mid-candle attempt per minute
        now = datetime.now()
        if (now - self.last_mid_candle_entry_ts).total_seconds() < 60:
            return

        # 2. Flashpoint Criteria: Dynamic Hawkes Intensity Floor or Capitulation (Liquidations)
        # Phase 93: Replacing static 75k/105k thresholds with dynamic allocation-free Z-Score evaluation
        regime_name = self.prev_regime or "UNKNOWN"
        is_crisis_regime = regime_name == "CRISIS"
        
        # We need DID ratio for unrestricted displacement evaluation. 
        # DID = Delta / Intensity
        micro_intensity = getattr(self.microstructure, "current_intensity", intensity)
        did_ratio = abs(delta) / micro_intensity if micro_intensity > 0 else 0.0
        
        # State: 0 (Noise), 1 (Standard PoNR), 2 (Crisis PoNR), 3 (Unrestricted Displacement)
        hawkes_state = self.dynamic_hawkes_floor.evaluate_execution_state(intensity, is_crisis_regime, did_ratio)
        
        # Pre-compute sniper result early so we can evaluate dynamic thresholds
        sniper_result = self._check_sniper_entry(close, delta, intensity=intensity)
        
        # Phase 96: Dynamic Liquidation Squeeze Guard
        global_oi = getattr(self.current_shm_state, 'global_open_interest', 100000.0) if getattr(self, "current_shm_state", None) else 100000.0
        global_gobi = getattr(self.current_shm_state, 'global_obi', 0.0) if getattr(self, "current_shm_state", None) else 0.0
        
        is_long_squeeze = False
        opposite_liq_vol = 0.0
        
        if sniper_result["should_trade"]: 
            is_long_squeeze = sniper_result["direction"] == "LONG"
            liq_side = self.last_liquidation_side
            is_long_liq = (liq_side == "SELL" or liq_side == "LONG")
            
            # If longing a short squeeze, we demand SHORT liquidations
            if is_long_squeeze and not is_long_liq:
                opposite_liq_vol = self.last_liquidation_v_1m
            # If shorting a long squeeze, we demand LONG liquidations
            if not is_long_squeeze and is_long_liq:
                opposite_liq_vol = self.last_liquidation_v_1m

        # Evaluate the stateless dynamic threshold
        is_capitulating = self.dynamic_liq_guard.evaluate(
             self.last_liquidation_v_1m,
             opposite_liq_vol,
             global_oi,
             global_gobi,
             is_long_squeeze
        )
        
        # If the Hawkes state is 0, it doesn't even qualify for a standard Flashpoint entry
        if hawkes_state == 0 and not is_capitulating:
            return
            
        # 3. Can we trade?
        can_trade, reason = self.position_manager.can_trade()
        if not can_trade:
            return

        # 4. Update Throttle BEFORE calling any deeper logic to absolutely guarantee
        # it cannot accidentally spin out of control if deeper checks fail
        self.last_mid_candle_entry_ts = now
        
        try:
            # 4. We already computed sniper_result above
            
            if sniper_result["should_trade"]:
                # Phase 66: DYNAMIC DELTA CONFIRMATION GATE
                # Feed latest Hawkes intensity into the integrated accumulator
                h_lambda = getattr(self.microstructure, 'hawkes_lambda', 0.1)
                self.delta_threshold.integrate_hawkes(h_lambda)
                
                # Get mean Kyle's Lambda from microstructure buffer
                kyle_mean = float(np.mean(self.microstructure.lambda_buffer)) if len(self.microstructure.lambda_buffer) > 5 else 0.001
                kyle_current = getattr(self.microstructure, 'kyles_lambda', kyle_mean) or kyle_mean
                
                dyn_thresh = self.delta_threshold.get_threshold(
                    hurst=getattr(self, '_last_hurst', 0.5),
                    z_gk=getattr(self, '_last_z_gk', 0.0),
                    kyle_current=kyle_current,
                    kyle_mean_24h=kyle_mean,
                )
                if abs(delta) < dyn_thresh:
                    print(f"  [{self.timeframe} 🔒] DELTA GATE: |{abs(delta):.1f}| < Θ={dyn_thresh:.1f} BTC. Insufficient conviction.")
                    return
                
                # Phase 94: Dynamic Exhaustion / Whipsaw Shield
                price_change = close - getattr(self, 'prev_close', close)
                Z_Q, Z_lambda, current_lambda = self.dynamic_exhaustion_guard.update(delta, price_change)
                
                if self.dynamic_exhaustion_guard.is_exhaustion_climax(Z_Q, Z_lambda, z_q_thresh=3.0, z_lam_thresh=-1.5):
                    print(f"  [{self.timeframe} 🛡️] WHIPSAW SHIELD: Entry blocked. Delta Z={Z_Q:.2f}, Lambda Z={Z_lambda:.2f} (Absorption Climax)")
                    return

                # Phase 96: The legacy Phase 54 'SQUEEZE FILTER' directional requirement has been 
                # mathematically absorbed into the DynamicLiquidationGuard's opposite_liq variance boundary.

                # Check if it's a PoNR expansion (Zero-Underwater candidate)
                if "PoNR" in sniper_result["reason"]:
                    print(f"  [{self.timeframe} ⚡] FLASHPOINT ALIGNED: PoNR detected mid-candle. Evaluating Macro Filter...")
                    
                    # Fetch context for Gemini
                    await asyncio.gather(
                        self.market_context.get_context(),
                        self.multi_tf.update()
                    )
                    
                    # Phase 93: Unrestricted Displacement is now strictly state 3 from the Dynamic Floor
                    unrestricted_displacement = (hawkes_state == 3)
                    
                    # Phase 51: Flashpoint requires 15m Map Alignment + Macro Consensus, unless Unrestricted Displacement
                    if regime_name == "CRISIS" and not unrestricted_displacement:
                        macro_ok, macro_reason = await self._check_macro_consensus(sniper_result["direction"])
                        if not macro_ok:
                            print(f"  [{self.timeframe} ⚡] FLASHPOINT BLOCKED: Macro conflict ({macro_reason})")
                            return
                    elif regime_name != "CRISIS": # For non-CRISIS regimes, always check macro consensus
                        macro_ok, macro_reason = await self._check_macro_consensus(sniper_result["direction"])
                        if not macro_ok:
                            print(f"  [{self.timeframe} ⚡] FLASHPOINT BLOCKED: Macro conflict ({macro_reason})")
                            return

                    print(f"  [{self.timeframe} ⚡] MACRO ALIGNED: Prompting Gemini for instant execution.")
                    # Calculate full Microstructure for Flashpoint (Phase 52)
                    micro_metrics = self.microstructure.calculate_metrics(close, delta)
                    is_absorbing = getattr(micro_metrics, "is_absorbing", False)
                    if is_absorbing:
                        print(f"  [{self.timeframe} ⚡] FLASHPOINT BLOCKED: Absorption detected (No displacement).")
                        return

                    # Fetch VWAP Metrics (Phase 75)
                    vw_metrics = self.vwap_engine.get_metrics(close)
                    
                    # Build Structured Context for Phase 98 Relative Taxonomy
                    current_theta = self.current_shm_state.lead_lag_theta if self.current_shm_state else 0.0
                    theta_hurdle = getattr(self.dynamic_theta, 'get_current_threshold', lambda h, t: 3.0)(getattr(self, '_last_hurst', 0.5), getattr(self, '_last_z_gk', 0.0))
                    
                    current_di = self.current_shm_state.global_di if self.current_shm_state else 0.0
                    di_expected = getattr(self.divergence_guard, 'expected_range', 2.0)
                    
                    dyn_delta_thresh = getattr(self.delta_threshold, 'get_threshold', lambda *args, **kwargs: 50.0)(
                        hurst=getattr(self, '_last_hurst', 0.5),
                        z_gk=getattr(self, '_last_z_gk', 0.0),
                        kyle_current=getattr(self.microstructure, 'kyles_lambda', 0.001) or 0.001,
                        kyle_mean_24h=0.001
                    )
                    
                    hawkes_multiplier = 1.0
                    if getattr(self, 'dynamic_intensity', None):
                        h_state = getattr(self.dynamic_intensity, 'get_hawkes_state', lambda z: 0)(getattr(self.dynamic_intensity, 'Z_I', 0.0))
                        if h_state == 3:
                            hawkes_multiplier = 1.5
                        elif h_state == 2:
                            hawkes_multiplier = 1.25

                    # =================================================================
                    # PHASE 101: Asynchronous Quantitative Core Override (Flashpoint)
                    # =================================================================
                    # Ask Gemini (Crisis context) removed to maintain UVLoop speed.
                    print(f"  [{self.timeframe} ⚡] MACRO ALIGNED: Consulting Quantitative Execution Core...")
                    macro_bias = getattr(self.current_macro_regime, 'bias', 'NEUTRAL')
                    macro_mult = getattr(self.current_macro_regime, 'hysteresis_multiplier', 1.0)
                    
                    is_aligned = False
                    if sniper_result["direction"] == "LONG" and macro_bias in ["BULLISH", "NEUTRAL"]:
                        is_aligned = True
                    elif sniper_result["direction"] == "SHORT" and macro_bias in ["BEARISH", "NEUTRAL"]:
                        is_aligned = True
                        
                    if is_aligned:
                        from gemini_analyst import TradeAction, TradeSignal
                        action_enum = getattr(TradeAction, "GO_" + sniper_result["direction"])
                        conf = min(0.99, 0.85 * macro_mult)
                    else:
                        from gemini_analyst import TradeAction, TradeSignal
                        action_enum = TradeAction.STAY_FLAT
                        conf = 0.0
                        
                    signal = TradeSignal(
                        action=action_enum,
                        confidence=conf,
                        reasoning=f"Autonomous Flashpoint Execution via Quantitative Core (Macro Bias: {macro_bias}).",
                        stop_loss_pct=self.dynamic_exit.calculate(max(self.atr, 1.0), sniper_result.get('price', 0) or self.prev_close, regime_name, getattr(self, '_last_hurst', 0.5), getattr(self, '_last_gk_vol', 0.005), 'Flashpoint')[1],
                        take_profit_pct=self.dynamic_exit.calculate(max(self.atr, 1.0), sniper_result.get('price', 0) or self.prev_close, regime_name, getattr(self, '_last_hurst', 0.5), getattr(self, '_last_gk_vol', 0.005), 'Flashpoint')[0]
                    )

                    # Synthesize continuous probabilities from string states for Phase 97 Deep Research Math
                    if regime_name == "CRISIS":
                        p_norm, p_cb, p_cris = 0.05, 0.15, 0.80
                    elif regime_name in ["TRANSITION", "HIGH_VOL", "RANGE"]:
                        p_norm, p_cb, p_cris = 0.20, 0.60, 0.20
                    else: # NORMAL
                        p_norm, p_cb, p_cris = 0.80, 0.15, 0.05
                    
                    # Phase 97: Dynamic Regime Confidence Scaling (Continuous Differentiable Hurdle)
                    hurdle = self.dynamic_confidence_scaler.calculate_threshold(
                        p_norm=p_norm, 
                        p_cb=p_cb, 
                        p_crisis=p_cris, 
                        hawkes_intensity=intensity
                    )
                    
                    if signal.should_execute(hurdle):
                        # Phase 89.2/90: Structural Alignment Guard for Flashpoint
                        # Don't let Gemini execute a Flashpoint entry against a colossal local delta tidal wave or Dynamic Theta.
                        is_conflicting = False
                        if getattr(signal, "action", None):
                            action_name = signal.action.value if hasattr(signal.action, "value") else str(signal.action)
                            theta = self.current_shm_state.lead_lag_theta if self.current_shm_state else 0.0
                            
                            # Fetch Dynamic Threshold & State Variables
                            current_obi = self.order_book.obi_macro
                            dyn_theta_thresh = self._calculate_dynamic_theta_threshold(regime_name, theta, intensity, current_obi)
                            
                            # Phase 91: Dynamic Volume Delta Veto (replaces +/- 50.0 static check)
                            hmm_base_limit = 5.0 if regime_name == "CRISIS" else (3.5 if regime_name == "TRANSITION" else 2.0)
                            norm_kyle_lambda = 0.0 # Placeholder until full OBI integration
                            intended_side = 1 if "LONG" in action_name else (-1 if "SHORT" in action_name else 0)
                            
                            is_delta_vetoed = self.dynamic_volume_veto.check_execution_safety(
                                current_volume_delta=delta,
                                hmm_base_limit=hmm_base_limit,
                                obi=current_obi,
                                norm_kyle_lambda=norm_kyle_lambda,
                                intended_side=intended_side
                            )
                            
                            if is_delta_vetoed:
                                is_conflicting = True
                                print(f"  [{self.timeframe} 🛡️] FLASHPOINT VETO: Order blocked by Phase 91 Dynamic Volume Imbalance Shield! (Local Delta: {delta:.1f} BTC)")

                            elif "SHORT" in action_name and theta > dyn_theta_thresh:
                                is_conflicting = True
                                print(f"  [{self.timeframe} 🛡️] FLASHPOINT VETO: Short blocked by Bullish Lead-Lag Alpha (Theta +{theta:.2f} > DynThresh {dyn_theta_thresh:.2f})")
                            elif "LONG" in action_name and theta < -dyn_theta_thresh:
                                is_conflicting = True
                                print(f"  [{self.timeframe} 🛡️] FLASHPOINT VETO: Long blocked by Bearish Lead-Lag Alpha (Theta {theta:.2f} < -DynThresh {-dyn_theta_thresh:.2f})")
                                
                            # Phase 95: Apply the continuous gate check 
                            action_int = 1 if "LONG" in action_name else -1
                            global_gobi = getattr(self.current_shm_state, 'global_obi', 0.0) if self.current_shm_state else 0.0
                            p_chop = getattr(self.regime_detector, "prob_chop", 0.33)
                            p_trend = getattr(self.regime_detector, "prob_trend", 0.33)
                            p_crash = getattr(self.regime_detector, "prob_crash", 0.33)
                            
                            self.dynamic_theta_gate.update_and_evaluate(
                                theta=theta, 
                                gobi=global_gobi,
                                prob_s1=p_chop, prob_s2=p_trend, prob_s3=p_crash
                            )
                            
                            if not self.dynamic_theta_gate.validate_execution(action_int, theta):
                                is_conflicting = True
                                print(f"  [{self.timeframe} 🔒] FLASHPOINT VETO: Position ({action_name}) structurally contradicts Sentinel direction or lacks dynamic conviction.")
                                
                        if not is_conflicting:
                            print(f"  [{self.timeframe} ⚡] FLASHPOINT EXECUTED at ${close:,.2f}!")
                            self.entry_delta = delta
                            self.entry_time = datetime.now()
                            await self._execute_signal(signal, close)
        except Exception as e:
            self.logger.error(f"Flashpoint logic crashed: {e}")
            # Ensure the throttle blocks a retry spin
            pass
    async def _check_macro_consensus(self, direction: str) -> tuple[bool, str]:
        """
        Verify Hierarchical Consensus (Phase 51).
        Checks if the 30m HMM state AND Bias permits a trade on the 15m anchor.
        """
        try:
            # 1. Fetch 30m regime
            macro_state = await self.multi_tf.get_regime("30m")
            
            # 2. Fetch 30m direction bias
            macro_trend = self.multi_tf.current.htf_30m.direction if self.multi_tf.current.htf_30m else TrendDirection.NEUTRAL
            
            # BLOCKER 1: 30m CRISIS is a hard pause for all mean reversion/small trends
            if macro_state == "CRISIS":
                return (False, f"30m CRISIS ({macro_trend.value})")
            
            # BLOCKER 2: Directional Alignment
            # Phase 59: CRISIS OVERRIDE. If we are in a true CRISIS regime, the 30m moving averages 
            # will lag severely behind the live order flow. We bypass the Bias block to catch the nuke/pump.
            if macro_state == "CRISIS":
                print(f"  [{self.timeframe} 🛡️] MACRO SHIELD BYPASSED: CRISIS regime active. Trusting lived order flow over 30m lagging bias.")
                return (True, f"CRISIS OVERRIDE | {macro_trend.value}")
                
            if direction == "LONG":
                if "Bearish" in macro_trend.value:
                    return (False, f"30m Macro Bias is {macro_trend.value}")
            elif direction == "SHORT":
                if "Bullish" in macro_trend.value:
                    return (False, f"30m Macro Bias is {macro_trend.value}")
            
            return (True, f"{macro_state} | {macro_trend.value}")
        except Exception as e:
            self.logger.error(f"Macro consensus check failed: {e}")
            return (True, "UNKNOWN") # Fallback to 15m autonomy
    
    async def _execute_signal(self, signal, price: float):
        """Execute a trade signal."""
        if signal.action == TradeAction.GO_LONG:
            side = Side.LONG
            order_side = OrderSide.BUY
        elif signal.action == TradeAction.GO_SHORT:
            side = Side.SHORT
            order_side = OrderSide.SELL
        else:
            return
        
        # Calculate position size
        qty = self.position_manager.get_max_position_size(price)
        
        # Check minimum order size
        if qty == 0:
            print(f"  ❌ Cannot trade: Position size below minimum (need more capital or higher MAX_POSITION_PCT)")
            return
            
        # 🚨 REAL-TIME PRICE SYNC: The 'price' argument is stale due to Gemini latency. 
        # Fetch the exact millisecond current price right before execution.
        live_price = await self.exchange.get_current_price()
        if live_price == 0.0: live_price = price # Fallback
        
        print(f"  📈 Opening {side.value} position: {qty:.6f} @ ${live_price:,.2f}")
        
        # Execute on exchange
        result = await self.exchange.place_market_order(order_side, qty, estimated_price=live_price)
        
        if result.success:
            self.position_manager.open_position(side, qty, result.price)
            print(f"  ✅ Order filled: {result.message}")
        else:
            print(f"  ❌ Order failed: {result.message}")
    
    async def _check_exit_conditions(self, price: float, regime_name: str, gk_vol: float, hurst: float):
        """Intelligent exit management: Fixed SL + Order Flow TP + Trailing Stop."""
        pos = self.position_manager.position
        if not pos:
            return
        
        profit_pct = self.position_manager.get_profit_pct(price)
        print(f"  📊 Position: {profit_pct*100:+.2f}% | Entry: ${pos.entry_price:,.2f}")

        # 1. Fetch microstructure for safety checks
        current_delta = self.footprint.current.cumulative_delta
        micro_metrics = self.microstructure.calculate_metrics(price, current_delta)

        # =================================================================
        # EXIT CHECK #0: LOGISTIC EXHAUSTION EXIT (Phase 59)
        # =================================================================
        # Calculate dynamic threshold based on unrealized profit (Logistic Sigmoid Curve)
        # L = 400000 (Maximum breathing room for new trades)
        # Floor = 150000 (Tightest threshold for harvesting deep profits)
        # Midpoint x_0 = 0.0075 (+0.75% profit)
        # Steepness k = 1000
        
        import math
        # Sigmoid function inverted to trail: higher PnL = lower allowed intensity
        try:
            sigmoid_val = 1.0 / (1.0 + math.exp(-1000.0 * (profit_pct - 0.0075)))
        except OverflowError:
            sigmoid_val = 1.0 if profit_pct > 0.0075 else 0.0
            
        # Phase 106: Scale ceiling by regime to prevent premature exits during breakouts
        regime_name = self.prev_regime or "NORMAL"
        if regime_name in ("CRISIS", "HIGH_VOL"):
            L_ceiling = 800000.0
            L_range = 500000.0
        else:
            L_ceiling = 400000.0
            L_range = 250000.0
        allowed_intensity = L_ceiling - (L_range * sigmoid_val)
        
        if micro_metrics.intensity > allowed_intensity:
            # Phase 107: Subordinate Hawkes exit to fee floor
            # Prevent net-negative harvests (must clear 0.25% breakeven before intensity exit fires)
            if self.dynamic_exit.should_hawkes_exit(profit_pct):
                print(f"  🌋 LOGISTIC EXHAUSTION EXIT: Intensity ({micro_metrics.intensity:.0f}) breached dynamic ceiling ({allowed_intensity:.0f}). Profit {profit_pct*100:.2f}% > fee floor. Harvesting now!")
                await self._close_position(price, "LOGISTIC_EXHAUSTION")
                return
            else:
                print(f"  [{self.timeframe}] ⏸️ EXHAUSTION SUPPRESSED: Intensity ({micro_metrics.intensity:.0f}) > ceiling ({allowed_intensity:.0f}) BUT profit {profit_pct*100:.2f}% < 0.25% fee floor. Holding.")

        # =================================================================
        # EXIT CHECK #1: BREAK-EVEN SHIELD & PARTIAL TP (Phase 53)
        # =================================================================
        # 1. Break-Even Shield: If profit > 0.4%, move SL to Entry + 0.12% (Fee Coverage)
        if profit_pct >= 0.004 and not getattr(pos, "break_even_hit", False):
            pos.break_even_hit = True
            # Calculate the fee-covered entry price for logging
            fee_covered_price = pos.entry_price * (1.0 + 0.0012) if pos.side.value == "LONG" else pos.entry_price * (1.0 - 0.0012)
            print(f"  🛡️ BREAK-EVEN SHIELD ACTIVE: Stop Loss moved to Entry + 0.12% (${fee_covered_price:,.2f})")
            
        # 2. Partial TP: If profit > 0.5%, harvest 50%
        if profit_pct >= 0.005 and not getattr(pos, "partial_tp_hit", False):
            pos.partial_tp_hit = True # Mark as hit immediately to prevent double trigger
            await self._partial_close_position(price, 0.5, "CAPITAL_SHIELD")
             # Continue analysis for the remaining 50%
        
        # =================================================================
        # TIMEFRAME-SPECIFIC SETTINGS & DYNAMIC SCALING (Phase 58: The Breathing Exit)
        # =================================================================
        # Prepare data for Dynamic TP Engine
        df = pd.DataFrame(self.candle_buffer)
        
        # Calculate Phase 58 Dynamic TP
        dynamic_tp_pct = self.vol_tp.calculate_tp(df["high"], df["low"], df["close"])
        atr = self.vol_tp.compute_atr(df["high"], df["low"], df["close"])
        
        # Calculate Phase 48 Volatility Ratio for legacy reporting (Optional)
        avg_vol = sum(self.vol_buffer) / len(self.vol_buffer) if self.vol_buffer else gk_vol
        vol_ratio = gk_vol / avg_vol if avg_vol > 0 else 1.0
        
        # =================================================================
        # EXIT CHECK #0: DYNAMIC TAKE PROFIT (WVF-Scaled)
        # =================================================================
        if profit_pct >= dynamic_tp_pct:
            print(f"  🎯 BREATHING TP hit at {profit_pct*100:+.2f}% (WVF Boundary: {dynamic_tp_pct*100:.2f}%)")
            await self._close_position(price, "DYNAMIC_TP_WVF")
            return
        
        # =================================================================
        # EXIT CHECK #2: FIXED STOP LOSS (Protection - Always Active)
        # =================================================================
        # If Break-Even Shield is active, SL is at Entry + 0.12% profit (to cover Binance Taker Fees)
        FIXED_SL_PCT = 0.0125
            
        dynamic_sl = 0.0012 if getattr(pos, "break_even_hit", False) else -FIXED_SL_PCT
        
        if profit_pct <= dynamic_sl:
            reason = "BREAK_EVEN_SHY" if getattr(pos, "break_even_hit", False) else "FIXED_SL"
            print(f"  🛑 {reason} hit at {profit_pct*100:.2f}%")
            await self._close_position(price, reason)
            return
            
        # =================================================================
        # EXIT CHECK #3: ATR-ACTIVATED TRAILING STOP (Phase 58)
        # =================================================================
        if atr > 0:
            self.position_manager.update_atr_trailing_stop(price, atr)
            
            if self.position_manager.check_trailing_stop(price):
                print(f"  🛑 ATR TRAILING hit at ${price:,.2f} | PnL: {profit_pct*100:+.2f}%")
                await self._close_position(price, "ATR_TRAILING_STOP")
                return
        
        # =================================================================
        # EXIT CHECK #2: SMART HARVEST (Hurst-Adaptive Targets)
        # =================================================================
        vw_metrics = self.vwap_engine.get_metrics()
        vwap = vw_metrics['vwap']
        poc = self.volume_profile.poc
        vah = self.volume_profile.vah
        val = self.volume_profile.val
        delta = self.footprint.current.cumulative_delta
        is_long = pos.side.value == "LONG"
        is_short = pos.side.value == "SHORT"

        # Adaptive Targets based on Hurst (Trend Intensity)
        target_reached = False
        if hurst > 0.55: # Trending: Target the Range Extremes (Let it run!)
            target_label = "VA Extreme"
            if is_long and price >= vah: target_reached = True
            elif is_short and price <= val: target_reached = True
        else: # Mean Reverting: Target the Mean (POC/VWAP)
            target_label = "Mean (POC/VWAP)"
            if is_long and (price >= vwap or price >= poc): target_reached = True
            elif is_short and (price <= vwap or price <= poc): target_reached = True

        # SMART HARVEST: If target reached + in profit + flow reversal
        # We use a tighter reversal threshold (0.3 BTC) once target is reached
        flow_reversed = (is_long and delta < -0.3) or (is_short and delta > 0.3)

        # Minimum profit to harvest target is 0.20% (strictly covers 0.10% total fee load)
        if target_reached and profit_pct >= 0.002 and flow_reversed:
            print(f"  🎯 SMART HARVEST: {target_label} reached (${price:,.2f}) + Flow reversal. PnL: {profit_pct*100:+.2f}%")
            await self._close_position(price, "SMART_HARVEST")
            return

        # =================================================================
        # EXIT CHECK #4: ORDER FLOW REVERSAL (Standard Flow TP)
        # =================================================================
        # Close on standard flow reversal IF in significant profit
        # Phase 93: Lower threshold to 0.3% if dynamic intensity is high (Z > 2.5) or crisis
        is_crisis_regime = regime_name == "CRISIS"
        did_ratio = abs(current_delta) / micro_metrics.intensity if micro_metrics.intensity > 0 else 0.0
        hawkes_state = self.dynamic_hawkes_floor.evaluate_execution_state(micro_metrics.intensity, is_crisis_regime, did_ratio)
        
        is_high_risk = hawkes_state > 0 or is_crisis_regime
        MIN_PROFIT_FOR_TP = 0.003 if is_high_risk else 0.005
        
        if flow_reversed and profit_pct >= MIN_PROFIT_FOR_TP:
            print(f"  🎯 TAKING PROFIT on flow reversal: {profit_pct*100:+.2f}%")
            await self._close_position(price, "FLOW_REVERSAL_TP")
            return
        
        # =================================================================
        # EXIT CHECK #4: VOLATILITY-SCALED TRAILING STOP
        # =================================================================
        # Dynamic trail: looser during broad expansions, tighter when calm
        trail_pct = max(0.003, min(0.015, gk_vol * 20.0)) 
        
        # Final tightening logic when near goal
        # Phase 58 Update: FIXED_TP_PCT deprecated for dynamic scaling.
        # Tighten trail aggressively when deep in profit (>0.8%)
        if profit_pct >= 0.008:
            trail_pct = min(trail_pct, 0.003) 
            print(f"  🔒 Tightening Trailing Stop to {trail_pct*100:.2f}% (Goal in sight!)")
            
        self.position_manager.update_trailing_stop(price, trail_pct=trail_pct)
        
        if self.position_manager.check_trailing_stop(price):
            print(f"  🛑 TRAILING STOP hit at ${price:,.2f} | PnL: {profit_pct*100:+.2f}%")
            await self._close_position(price, "TRAILING_STOP")
            return

    
    async def _partial_close_position(self, price: float, close_pct: float, reason: str):
        """Partially close the current position (Phase 53)."""
        pos = self.position_manager.position
        if not pos or pos.side == Side.FLAT:
            return
            
        qty_to_close = round(pos.qty * close_pct, 3)
        if qty_to_close < 0.001:
            print(f"  ⚠️ Partial close qty {qty_to_close} below 0.001. Executing FULL close instead.")
            await self._close_position(price, f"{reason}_FULL_CONVERSION")
            return
            
        side_to_close = OrderSide.SELL if pos.side == Side.LONG else OrderSide.BUY
        
        # Fetch exact live price before execution
        live_price = await self.exchange.get_current_price()
        if live_price == 0.0: live_price = price
        
        print(f"  💸 PARTIAL {reason}: Closing {close_pct*100:.0f}% ({qty_to_close:.3f}) @ ${live_price:,.2f}")
        
        result = await self.exchange.place_market_order(side_to_close, qty_to_close, estimated_price=live_price, reduce_only=True)
        
        if result.success:
            self.position_manager.partial_close(result.price, close_pct, reason)
            print(f"  ✅ Partial fill: {result.message}")
        else:
            print(f"  ❌ Partial close failed: {result.message}")

    async def _close_position(self, price: float, reason: str):
        """Close current position."""
        pos = self.position_manager.position
        if not pos:
            return
        
        # Store values for logging before closing
        entry_price = pos.entry_price
        side = pos.side.value
        qty = pos.qty
        exit_delta = self.footprint.current.cumulative_delta
        
        # Fetch exact live price before execution to fix Gemini lag discrepancy
        live_price = await self.exchange.get_current_price()
        if live_price == 0.0: live_price = price
        
        # Execute on exchange
        close_side = OrderSide.SELL if pos.side == Side.LONG else OrderSide.BUY
        result = await self.exchange.place_market_order(close_side, qty, estimated_price=live_price, reduce_only=True)
        
        if result.success:
            # Short sleep to let Binance process the trade history
            await asyncio.sleep(1.0)
            
            # Fetch Ground Truth PnL from Binance
            binance_res = await self.exchange.get_last_trade_pnl()
            real_pnl = binance_res.get("realized_pnl")
            real_fee = binance_res.get("commission")
            
            # Close locally using exact figures if found, otherwise fallback to estimation
            if real_pnl != 0:
                pnl = self.position_manager.close_position(
                    result.price, 
                    reason, 
                    external_pnl=real_pnl, 
                    external_fees=real_fee
                )
                print(f"  [5m] 🔗 Binance Sync: Realized=${real_pnl:.2f}, Fee=${real_fee:.2f}")
            else:
                pnl = self.position_manager.close_position(result.price, reason)
            
            profit_pct = (result.price - entry_price) / entry_price if side == "LONG" else (entry_price - result.price) / entry_price
            
            # Calculate duration
            duration = 0
            if self.entry_time:
                duration = int((datetime.now() - self.entry_time).total_seconds())
            
            # Log the trade
            self.logger.log_trade(
                timeframe=self.timeframe,
                side=side,
                entry_price=entry_price,
                exit_price=result.price,
                qty=qty,
                pnl=pnl,
                pnl_pct=profit_pct,
                exit_reason=reason,
                duration_seconds=duration,
                entry_delta=self.entry_delta,
                exit_delta=exit_delta
            )
            
            print(f"  Closed @ ${result.price:,.2f} | Net PnL: ${pnl:+.2f}")
            
            # Post-Trade Analyst (Gemini Trade Journal)
            trade_data = {
                "timeframe": self.timeframe,
                "regime": self.prev_regime or "UNKNOWN",
                "side": side,
                "entry_price": entry_price,
                "exit_price": result.price,
                "pnl_usd": f"${pnl:.2f}",
                "pnl_pct": f"{profit_pct*100:.2f}%",
                "binance_fees": f"${real_fee:.2f}" if 'real_fee' in locals() else "Unknown",
                "exit_reason": reason,
                "duration_seconds": duration,
                "entry_delta": self.entry_delta,
                "exit_delta": exit_delta,
                "obi_at_exit": self.order_book.obi_macro,
                "magnet_context": "Active Magnet Detected" if len(self.magnet.magnets) > 0 else "None",
                "vp_context": f"Price near VWAP (${self.volume_profile.vwap:,.0f})"
            }
            
            async def generate_and_log_journal(data: dict):
                """Background task to fetch and log the AI summary."""
                print(f"  [{self.timeframe} 🧠] Requesting Post-Trade Analysis...")
                summary = await self.gemini.analyze_closed_trade(data)
                self.logger.log_trade_journal(self.timeframe, "BTCUSDT", summary)
                print(f"  [{self.timeframe} 📓] Trade Journal Saved.")
                
            # Fire and forget
            asyncio.create_task(generate_and_log_journal(trade_data))
            
            # Print daily summary periodically
            self.logger.print_summary()
        else:
            print(f"  ❌ Close failed: {result.message}")

    def _verify_brain_freshness(self):
        """Log the last modified dates of the active models (Phase 51)."""
        models = [f"hmm_model_{self.timeframe}.pkl", "hmm_model_30m.pkl"]
        print(f"\n  [{self.timeframe}] 🧠 BRAIN FRESHNESS CHECK:")
        for m in models:
            if os.path.exists(m):
                mtime = os.path.getmtime(m)
                dt = datetime.fromtimestamp(mtime)
                print(f"   - {m}: Last Updated {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"   - {m}: ⚠️ MODEL NOT FOUND")
        print("-" * 30)


async def main():
    """Entry point."""
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    bot = TradingBot(testnet=testnet)
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    print("Starting VEBB-AI Trading Bot...")
    
    # Phase 67: High-Frequency Architecture
    # Apply uvloop for ultra-low latency socket polling if running on Linux (VPS)
    import sys
    if sys.platform != "win32":
        try:
            import uvloop
            uvloop.install()
            print("[System] uvloop activated for ultra-low latency event loop.")
        except ImportError:
            print("[System] uvloop not installed. Falling back to native asyncio.")
            
    asyncio.run(main())