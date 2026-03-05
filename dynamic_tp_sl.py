"""
VEBB-AI: Dynamic TP/SL Calibration Module (Phase 107)

Regime-adaptive, ATR-scaled, Kelly-adjusted exit framework.
Replaces all hardcoded take_profit_pct and stop_loss_pct values.

Key constraints (75x leverage on Binance):
  - SL ceiling: 0.80% (exchange liquidates at ~1.0%, bankruptcy at 1.33%)
  - TP floor: 0.25% (0.10% taker fee × 2 sides + 0.05% slippage)

Derived from Deep Research: "Regime-Adaptive Stochastic Exit Framework"
"""

import math
import numpy as np
from collections import deque


class DynamicExitFramework:
    """
    Self-calibrating TP/SL engine that adapts to regime, entry type,
    volatility, Hurst persistence, and historical edge (Kelly).

    Phase 107: Zero hardcoded TP/SL percentages.
    """

    # Structural constraints from 75x leverage mechanics
    MAX_SL_CEILING = 0.0080   # 0.80% — max before Binance liquidation engine fires
    MIN_TP_FLOOR = 0.0025     # 0.25% — breakeven after 0.20% round-trip fees + 0.05% slippage

    # Regime base multipliers (theoretically justified from Deep Research)
    # Format: (k_TP_base, k_SL_base)
    REGIME_MATRIX = {
        'NORMAL':     (2.5, 1.2),
        'HIGH_VOL':   (3.2, 1.5),   # 3.2× ATR for fat-tail outlier expansion
        'CRISIS':     (1.0, 0.5),   # Capital preservation — tight bands
        'TRANSITION': (1.5, 0.8),   # Uncertainty between Markov states
        'RANGE':      (1.5, 1.0),   # Mean-reverting compression
    }

    # Entry-type asymmetry multipliers (applied on top of regime base)
    # Format: (tp_mult, sl_mult)
    ENTRY_TYPE_MATRIX = {
        'Trend_Breakout':   (1.50, 0.80),  # Wide TP, tight SL — capture fat right tail
        'Mean_Reversion':   (0.70, 0.90),  # Compressed R:R — scalp back to VWAP
        'Lead_Lag_Alpha':   (0.80, 0.60),  # Transient edge — tight bounds
        'PoNR_Expansion':   (1.20, 1.10),  # Structural VA rejection — moderate
        'Flashpoint':       (1.30, 0.90),  # Mid-candle autonomous — slightly aggressive
        'Absorption_Reversal': (0.60, 0.70),  # Phase 116A: Probe-sized reversal, scalp to VAL
    }

    def __init__(self, kelly_lambda: float = 0.05, gk_vol_baseline: float = 0.005,
                 gk_vol_std: float = 0.002):
        """
        Args:
            kelly_lambda: Bayesian confidence decay rate (0.05 → full confidence at ~100 trades)
            gk_vol_baseline: Expected mean GK volatility for BTC 15m candles
            gk_vol_std: Expected standard deviation of GK volatility
        """
        self.kelly_lambda = kelly_lambda
        self.gk_vol_baseline = gk_vol_baseline
        self.gk_vol_std = gk_vol_std

        # Trade history for Kelly computation
        self.trade_results = deque(maxlen=200)  # (win: bool, pnl_pct: float)

    def record_trade(self, is_win: bool, pnl_pct: float):
        """Record a completed trade result for Kelly Criterion updates."""
        self.trade_results.append((is_win, pnl_pct))

    def _get_kelly_stats(self) -> tuple:
        """Calculate win rate, avg win, avg loss from trade history."""
        if len(self.trade_results) < 5:
            return 0.5, 0.01, 0.01, len(self.trade_results)  # Neutral defaults

        wins = [(w, p) for w, p in self.trade_results if w]
        losses = [(w, p) for w, p in self.trade_results if not w]

        win_rate = len(wins) / len(self.trade_results) if self.trade_results else 0.5
        avg_win = abs(np.mean([p for _, p in wins])) if wins else 0.01
        avg_loss = abs(np.mean([p for _, p in losses])) if losses else 0.01

        return win_rate, avg_win, avg_loss, len(self.trade_results)

    def calculate(
        self,
        atr: float,
        entry_price: float,
        regime: str = 'NORMAL',
        hurst: float = 0.5,
        gk_vol: float = 0.005,
        entry_type: str = 'Trend_Breakout',
    ) -> tuple:
        """
        Compute regime-adaptive, Kelly-adjusted, ATR-scaled TP and SL percentages.

        Args:
            atr: Current ATR in absolute price units (e.g., $450)
            entry_price: Entry price (e.g., $68,000)
            regime: HMM regime string
            hurst: Current Hurst exponent (0-1)
            gk_vol: Current Garman-Klass volatility
            entry_type: One of the 5 entry pathways

        Returns:
            (tp_pct, sl_pct) — Percentages as decimals (e.g., 0.015 = 1.5%)
        """
        # 1. Base multipliers from regime matrix
        k_tp, k_sl = self.REGIME_MATRIX.get(regime, (2.0, 1.0))

        # 2. Entry-type asymmetry
        tp_mult, sl_mult = self.ENTRY_TYPE_MATRIX.get(entry_type, (1.0, 1.0))
        k_tp *= tp_mult
        k_sl *= sl_mult

        # 3. Hurst exponent scaling
        # H > 0.5 (trending) → expand TP; H < 0.5 (reverting) → compress TP
        hurst_clamped = max(0.1, min(hurst, 0.9))
        hurst_scalar = 1.0 + 1.0 * (hurst_clamped - 0.5)
        hurst_scalar = max(0.5, min(hurst_scalar, 1.5))
        k_tp *= hurst_scalar

        # 4. Garman-Klass volatility dispersion adjustment
        gk_z_proxy = (gk_vol - self.gk_vol_baseline) / max(self.gk_vol_std, 0.0001)
        if gk_z_proxy > 1.5:
            # Explosive volatility: widen bounds to prevent whipsaw liquidation
            k_tp *= 1.20
            k_sl *= 1.20
        elif gk_z_proxy < -1.0:
            # Volatility compression: tighten bounds
            k_tp *= 0.80
            k_sl *= 0.80

        # 5. Bayesian Kelly shrinkage integration
        win_rate, avg_win, avg_loss, num_trades = self._get_kelly_stats()

        if avg_loss > 0 and num_trades > 0:
            b = avg_win / avg_loss
            raw_kelly = (b * win_rate - (1 - win_rate)) / b if b > 0 else 0
            raw_kelly = max(0, raw_kelly)

            # Confidence factor: approaches 1.0 at ~100 trades
            confidence = 1.0 - math.exp(-self.kelly_lambda * num_trades)

            # Shrink toward Quarter-Kelly when sample is small
            quarter_kelly = raw_kelly * 0.25
            adj_kelly = (raw_kelly * confidence) + (quarter_kelly * (1 - confidence))

            # Feedback loop: strong edge → wider TP, tighter SL
            if adj_kelly > 0.10:
                k_tp *= 1.15
                k_sl *= 0.90

        # 6. Convert ATR multipliers to percentages
        safe_price = max(entry_price, 1.0)
        raw_tp_pct = (k_tp * atr) / safe_price
        raw_sl_pct = (k_sl * atr) / safe_price

        # 7. Enforce structural constraints
        tp_pct = max(raw_tp_pct, self.MIN_TP_FLOOR)
        sl_pct = min(raw_sl_pct, self.MAX_SL_CEILING)

        # Ensure SL is at least 0.05% to avoid instant stop-outs from spread noise
        sl_pct = max(sl_pct, 0.0005)

        return float(tp_pct), float(sl_pct)

    def should_hawkes_exit(self, unrealized_pnl_pct: float) -> bool:
        """
        Phase 107: Fee-floor subordination for Logistic Exhaustion exit.
        Hawkes intensity exit is SUPPRESSED until minimum breakeven is cleared.

        Args:
            unrealized_pnl_pct: Current unrealized P&L as percentage (e.g., 0.003 = 0.3%)

        Returns:
            True if Hawkes exit is permitted (fee floor cleared), False if suppressed.
        """
        return unrealized_pnl_pct > self.MIN_TP_FLOOR

    def get_debug_info(self, atr, entry_price, regime, hurst, gk_vol, entry_type):
        """Return breakdown of all TP/SL components for logging."""
        tp_pct, sl_pct = self.calculate(atr, entry_price, regime, hurst, gk_vol, entry_type)

        win_rate, avg_win, avg_loss, num_trades = self._get_kelly_stats()
        hurst_scalar = max(0.5, min(1.0 + 1.0 * (hurst - 0.5), 1.5))

        return {
            "regime": regime,
            "entry_type": entry_type,
            "atr": round(atr, 2),
            "hurst": round(hurst, 3),
            "hurst_scalar": round(hurst_scalar, 3),
            "gk_vol": round(gk_vol, 6),
            "kelly_trades": num_trades,
            "kelly_win_rate": round(win_rate, 3),
            "tp_pct": round(tp_pct * 100, 3),
            "sl_pct": round(sl_pct * 100, 3),
            "rr_ratio": round(tp_pct / max(sl_pct, 0.0001), 2),
        }
