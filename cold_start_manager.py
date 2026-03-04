"""
VEBB-AI: Phase 113 — Unified Cold-Start Manager

Three-layer architecture (from Deep Research):
1. State Persistence: JSON serialization of all rolling buffers per candle
2. OHLCV Proxy Pre-Seeding: Continuous Tick Rule for TFI/Delta proxies
3. Exponential Time Decay: for stale state after extended downtime

Eliminates ALL arbitrary cold-start defaults. On restart, the bot either
loads its exact previous state (short downtime) or pre-seeds from
historical OHLCV proxies (first deployment / long downtime).
"""

import json
import os
import math
import time
import numpy as np
import requests
from datetime import datetime
from collections import deque


STATE_FILE = "adaptive_state.json"


class ColdStartManager:
    """
    Manages initialization and persistence of self-calibrating thresholds.
    
    Routes:
    A) State file exists + downtime < 24h → Load + exponential decay
    B) State file exists + downtime > 24h → Discard + OHLCV proxy pre-seed
    C) No state file → OHLCV proxy pre-seed (first deployment)
    """

    def __init__(self, state_dir: str = ".", decay_lambda: float = 0.05):
        self.state_path = os.path.join(state_dir, STATE_FILE)
        self.decay_lambda = decay_lambda  # Controls decay speed (0.05 → ~14h half-life)
        self.window_size = 96  # 24h at 15m

    # =========================================================================
    # Layer 1: State Persistence (JSON)
    # =========================================================================

    def save_state(self, adaptive_classes: dict):
        """
        Serialize all adaptive buffer states to JSON.
        Called at the end of every candle close.
        
        adaptive_classes: dict of name → adaptive class instance
        """
        state = {
            "timestamp": time.time(),
            "saved_at": datetime.utcnow().isoformat(),
            "buffers": {}
        }

        for name, instance in adaptive_classes.items():
            if hasattr(instance, 'get_state'):
                state["buffers"][name] = instance.get_state()

        try:
            # Atomic write: write to temp then rename
            tmp_path = self.state_path + ".tmp"
            with open(tmp_path, 'w') as f:
                json.dump(state, f, indent=None)  # Compact for speed
            os.replace(tmp_path, self.state_path)
        except Exception as e:
            print(f"[ColdStart] ⚠️ Failed to save state: {e}")

    def load_state(self) -> dict:
        """
        Load state from disk. Returns None if no state file exists.
        """
        if not os.path.exists(self.state_path):
            return None

        try:
            with open(self.state_path, 'r') as f:
                state = json.load(f)
            return state
        except Exception as e:
            print(f"[ColdStart] ⚠️ Failed to load state: {e}")
            return None

    def restore_state(self, adaptive_classes: dict):
        """
        Main entry point: Load state, apply decay if needed, or fall back to proxy pre-seeding.
        Returns the route taken: 'loaded', 'decayed', 'proxied', or 'cold'.
        """
        state = self.load_state()

        if state is None:
            print("[ColdStart] 🔴 No saved state found. Route C: First deployment.")
            return "cold"

        saved_ts = state.get("timestamp", 0)
        gap_seconds = time.time() - saved_ts
        gap_hours = gap_seconds / 3600.0

        if gap_hours > 24.0:
            print(f"[ColdStart] 🟡 State is {gap_hours:.1f}h old (>24h). Route B: Discarding + proxy pre-seed.")
            return "proxied"

        # Route A: State is valid. Apply exponential time decay.
        decay_factor = math.exp(-self.decay_lambda * gap_hours)
        print(f"[ColdStart] 🟢 Route A: Loading state ({gap_hours:.1f}h gap, decay={decay_factor:.3f})")

        buffers = state.get("buffers", {})
        restored_count = 0

        for name, instance in adaptive_classes.items():
            if name in buffers and hasattr(instance, 'set_state'):
                try:
                    buffer_data = buffers[name]
                    # Apply exponential decay to buffer weights
                    # For deque-based buffers, decay means removing oldest entries proportional to gap
                    entries_to_decay = int((1.0 - decay_factor) * self.window_size)
                    if entries_to_decay > 0 and isinstance(buffer_data, dict):
                        # Trim oldest entries from each buffer
                        for key, values in buffer_data.items():
                            if isinstance(values, list) and len(values) > entries_to_decay:
                                buffer_data[key] = values[entries_to_decay:]
                    
                    instance.set_state(buffer_data)
                    restored_count += 1
                except Exception as e:
                    print(f"[ColdStart] ⚠️ Failed to restore {name}: {e}")

        print(f"[ColdStart] ✅ Restored {restored_count}/{len(adaptive_classes)} buffers (decayed {entries_to_decay} oldest entries)")
        return "decayed" if gap_hours > 1.0 else "loaded"

    # =========================================================================
    # Layer 2: OHLCV Proxy Pre-Seeding (Continuous Tick Rule)
    # =========================================================================

    def preseed_from_ohlcv(self, adaptive_classes: dict, base_url: str, timeframe: str = "15m"):
        """
        Fetch 96 historical klines and derive microstructural proxies.
        Pre-seeds: CPR, Multipliers (exact), TFI, Delta, OBI (proxied).
        """
        klines = self._fetch_historical_klines(base_url, timeframe, limit=self.window_size + 1)
        
        if not klines or len(klines) < 10:
            print("[ColdStart] ⚠️ Failed to fetch historical klines for proxy pre-seeding.")
            return False

        # Skip last candle (current incomplete)
        klines = klines[:-1]
        
        # Extract OHLCV arrays
        opens   = np.array([float(k[1]) for k in klines])
        highs   = np.array([float(k[2]) for k in klines])
        lows    = np.array([float(k[3]) for k in klines])
        closes  = np.array([float(k[4]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
        
        epsilon = 1e-8
        
        # ---- Continuous Tick Rule: proxy for aggressive volume segmentation ----
        close_pos = (closes - lows) / (highs - lows + epsilon)  # CPR [0,1]
        buy_vol  = volumes * close_pos
        sell_vol = volumes * (1.0 - close_pos)
        
        # Proxy metrics
        delta_proxy = buy_vol - sell_vol
        tfi_proxy   = (buy_vol - sell_vol) / (volumes + epsilon)
        
        # Garman-Klass volatility proxy
        gk_vol = np.log(highs / lows) ** 2 / (4 * np.log(2)) - \
                 (2 * np.log(2) - 1) * np.log(closes / opens) ** 2
        gk_vol = np.sqrt(np.maximum(gk_vol, 0))
        
        # Hurst proxy from price ratio variance
        # Simple estimate: H ≈ 0.5 + log2(range/sigma) / 2
        log_returns = np.log(closes[1:] / closes[:-1])
        hurst_proxy = np.full(len(closes), 0.5)  # Default to random walk
        for i in range(10, len(log_returns)):
            window = log_returns[i-10:i]
            if np.std(window) > 0:
                # Rescaled range / S method (simplified)
                r = np.max(np.cumsum(window - np.mean(window))) - np.min(np.cumsum(window - np.mean(window)))
                s = np.std(window)
                if s > 0 and r > 0:
                    hurst_proxy[i+1] = 0.5 * np.log2(r / s + 1)  # Approximate

        # OBI proxy: price deviation from EMA midpoint
        typical_price = (highs + lows + closes) / 3.0
        ema_period = 10
        ema = np.copy(typical_price)
        alpha = 2.0 / (ema_period + 1)
        for i in range(1, len(ema)):
            ema[i] = alpha * typical_price[i] + (1 - alpha) * ema[i-1]
        
        # If close > EMA → bullish OBI proxy, else bearish
        obi_proxy = (closes - ema) / (highs - lows + epsilon)
        obi_proxy = np.clip(obi_proxy, -1.0, 1.0)
        
        # Hawkes intensity proxy: GK_Vol Z-score (approximates tick intensity)
        gk_mean = np.mean(gk_vol[:max(1, len(gk_vol)//2)])
        gk_std  = max(np.std(gk_vol[:max(1, len(gk_vol)//2)]), epsilon)
        hawkes_z_proxy = (gk_vol - gk_mean) / gk_std

        # ---- Feed proxies into adaptive classes ----
        n_candles = len(closes)
        print(f"[ColdStart] 📊 Pre-seeding {n_candles} candles via OHLCV proxies...")

        for i in range(n_candles):
            c, o, h, l = closes[i], opens[i], highs[i], lows[i]
            
            # Feed CPR buffer (exact — uses OHLC directly)
            if 'adaptive_cpr' in adaptive_classes:
                adaptive_classes['adaptive_cpr'].evaluate_and_update(
                    "NEUTRAL", o, h, l, c,
                    delta_proxy[i], hawkes_z_proxy[i], is_pre_emptive=True
                )
            
            # Feed Multiplier buffers (exact — uses GK_Vol and Hurst from OHLC)
            if 'adaptive_mults' in adaptive_classes:
                adaptive_classes['adaptive_mults'].evaluate_and_update(
                    gk_vol[i], hurst_proxy[i]
                )
            
            # Feed TFI buffer (proxied via Continuous Tick Rule)
            if 'adaptive_tfi' in adaptive_classes:
                adaptive_classes['adaptive_tfi'].evaluate_and_update(tfi_proxy[i])
            
            # Feed Sigmoid OBI + Z buffers (proxied)
            if 'adaptive_sigmoid' in adaptive_classes:
                adaptive_classes['adaptive_sigmoid'].get_adaptive_threshold(
                    hawkes_z_proxy[i], obi_proxy[i],
                    abs_delta=abs(delta_proxy[i])
                )
            
            # Feed Breakout Detector Z buffer (proxied)
            if 'adaptive_breakout' in adaptive_classes:
                # Calculate delta P90 from seen data so far
                seen_deltas = np.abs(delta_proxy[:i+1])
                delta_p90 = float(np.percentile(seen_deltas, 90)) if len(seen_deltas) > 5 else 500.0
                adaptive_classes['adaptive_breakout'].evaluate_and_update(
                    hawkes_z_proxy[i], abs(delta_proxy[i]), delta_p90, True
                )
            
            # Feed Hawkes EWMA (proxied)
            if 'adaptive_ewma' in adaptive_classes:
                # Use GK_Vol as a rough proxy for Hawkes lambda
                proxy_lambda = gk_vol[i] * 10000  # Scale to intensity range
                adaptive_classes['adaptive_ewma'].update(proxy_lambda)

        print(f"[ColdStart] ✅ Pre-seeded {n_candles} candles. Buffers primed.")
        return True

    def _fetch_historical_klines(self, base_url: str, timeframe: str, limit: int = 97) -> list:
        """Fetch historical klines from Binance REST API."""
        url = f"{base_url}/fapi/v1/klines"
        params = {"symbol": "BTCUSDT", "interval": timeframe, "limit": limit}
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[ColdStart] ⚠️ Klines API returned {response.status_code}")
                return []
        except Exception as e:
            print(f"[ColdStart] ⚠️ Failed to fetch klines: {e}")
            return []

    # =========================================================================
    # Unified Initialization
    # =========================================================================

    def initialize(self, adaptive_classes: dict, base_url: str, timeframe: str = "15m"):
        """
        Main entry point. Called once at bot startup.
        
        Route A: Load + decay (short downtime)
        Route B: Discard old state + proxy pre-seed (long downtime)
        Route C: Proxy pre-seed (first deployment)
        """
        route = self.restore_state(adaptive_classes)
        
        if route in ("cold", "proxied"):
            # No valid state — pre-seed from OHLCV proxies
            success = self.preseed_from_ohlcv(adaptive_classes, base_url, timeframe)
            if success:
                print(f"[ColdStart] 🚀 Phase 113 initialized via OHLCV proxy pre-seeding.")
            else:
                print(f"[ColdStart] ⚠️ Proxy pre-seeding failed. Using delta-based formula fallback.")
        else:
            print(f"[ColdStart] 🚀 Phase 113 initialized via state restoration ({route}).")
