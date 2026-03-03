"""
VEBB-AI: Multi-Timeframe Context Module
Fetches and analyzes higher timeframe data for trend direction.
Includes HMM regime detection for 1H and 4H timeframes.
"""

import os
import asyncio
import requests
import pickle
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from pathlib import Path
from regime_detector import RegimeDetector

# Bypass proxy
os.environ["NO_PROXY"] = "*"

# Model paths
MODEL_DIR = Path(__file__).parent
MODEL_30M_PATH = MODEL_DIR / "hmm_model_30m.pkl"
MODEL_1H_PATH = MODEL_DIR / "hmm_model_1h.pkl"
MODEL_4H_PATH = MODEL_DIR / "hmm_model_4h.pkl"


class TrendDirection(Enum):
    STRONG_BULLISH = "Strong Bullish ⬆️⬆️"
    BULLISH = "Bullish ⬆️"
    NEUTRAL = "Neutral ➡️"
    BEARISH = "Bearish ⬇️"
    STRONG_BEARISH = "Strong Bearish ⬇️⬇️"


@dataclass
class TimeframeTrend:
    """Trend analysis for a single timeframe."""
    timeframe: str
    direction: TrendDirection
    price_vs_ma20: str  # "above" or "below"
    momentum: str  # "rising", "falling", "flat"
    last_3_candles: str  # e.g., "2 green, 1 red"
    key_level: float  # Nearest significant support/resistance
    hmm_regime: str = "unknown"  # HMM regime: CALM, TRENDING, CRISIS
    

@dataclass
class MultiTimeframeContext:
    """Complete multi-timeframe analysis."""
    htf_30m: Optional[TimeframeTrend] = None
    htf_1h: Optional[TimeframeTrend] = None
    htf_4h: Optional[TimeframeTrend] = None
    htf_daily: Optional[TimeframeTrend] = None
    overall_bias: TrendDirection = TrendDirection.NEUTRAL
    alignment_score: str = "0/3"  # How many timeframes align
    regime_consensus: str = "unknown"  # Overall HMM regime consensus


class MultiTimeframeFetcher:
    """
    Fetches and analyzes higher timeframe data.
    
    Provides:
    - 1H trend direction
    - 4H trend direction  
    - Daily trend direction
    - Overall market bias
    """
    
    def __init__(self, symbol: str = "BTCUSDT", testnet: bool = True):
        self.symbol = symbol
        
        if testnet:
            self.base_url = "https://testnet.binancefuture.com"
        else:
            self.base_url = "https://fapi.binance.com"
        
        self.current: MultiTimeframeContext = MultiTimeframeContext()
        
        # Load HMM models
        self.hmm_30m = None
        self.hmm_1h = None
        self.hmm_4h = None
        self.hmm_labels_30m = {}
        self.hmm_labels_1h = {}
        self.hmm_labels_4h = {}
        self._load_hmm_models()
    
    def _load_hmm_models(self):
        """DEPRECATED (Phase 61): Macro Regime is now handled via the 15m Z-Score."""
        pass
    
    def _predict_regime(self, candles: List[dict], detector: Optional[RegimeDetector]) -> str:
        """DEPRECATED (Phase 61): Macro Regime is now handled directly by the Live 15m Z-Score governor."""
        return "NORMAL"

    def _calculate_quick_hurst(self, prices: List[float]) -> float:
        """Utility for multi-timeframe Hurst detection."""
        if len(prices) < 20: return 0.5
        series = np.array(prices)
        lags = range(2, min(20, len(series) // 2))
        try:
            tau = [np.sqrt(np.std(np.subtract(series[lag:], series[:-lag]))) for lag in lags]
            poly = np.polyfit(np.log(lags), np.log(tau), 1)
            return poly[0] * 2.0
        except:
            return 0.5
    
    async def _fetch_candles(self, interval: str, limit: int = 50) -> List[dict]:
        """Fetch candles for a specific timeframe without blocking the event loop."""
        url = f"{self.base_url}/fapi/v1/klines"
        params = {
            "symbol": self.symbol,
            "interval": interval,
            "limit": limit
        }
        
        try:
            # Phase 59: Offload synchronous requests to prevent 1011 websocket ping timeouts
            response = await asyncio.to_thread(
                lambda: requests.get(url, params=params, timeout=10)
            )
            
            if response.status_code == 200:
                data = response.json()
                candles = []
                for c in data:
                    candles.append({
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5])
                    })
                return candles
            return []
        except Exception as e:
            print(f"[MTF] Error fetching {interval} candles: {e}")
            return []
    
    def _calculate_ma(self, candles: List[dict], period: int = 20) -> float:
        """Calculate simple moving average."""
        if len(candles) < period:
            return 0.0
        closes = [c["close"] for c in candles[-period:]]
        return sum(closes) / period
    
    def _analyze_momentum(self, candles: List[dict], period: int = 5) -> str:
        """Analyze price momentum."""
        if len(candles) < period:
            return "unknown"
        
        recent = candles[-period:]
        first_close = recent[0]["close"]
        last_close = recent[-1]["close"]
        
        change_pct = ((last_close - first_close) / first_close) * 100
        
        if change_pct > 0.5:
            return "rising"
        elif change_pct < -0.5:
            return "falling"
        return "flat"
    
    def _count_candles(self, candles: List[dict], count: int = 3) -> str:
        """Count bullish/bearish candles."""
        if len(candles) < count:
            return "unknown"
        
        recent = candles[-count:]
        green = sum(1 for c in recent if c["close"] > c["open"])
        red = count - green
        
        return f"{green} green, {red} red"
    
    def _find_key_level(self, candles: List[dict]) -> float:
        """Find nearest significant support/resistance level."""
        if len(candles) < 20:
            return 0.0
        
        # Use recent swing highs/lows
        highs = [c["high"] for c in candles[-20:]]
        lows = [c["low"] for c in candles[-20:]]
        current_price = candles[-1]["close"]
        
        # Find nearest resistance (high above current)
        resistances = [h for h in highs if h > current_price]
        nearest_resistance = min(resistances) if resistances else max(highs)
        
        # Find nearest support (low below current)
        supports = [l for l in lows if l < current_price]
        nearest_support = max(supports) if supports else min(lows)
        
        # Return whichever is closer
        if abs(current_price - nearest_resistance) < abs(current_price - nearest_support):
            return nearest_resistance
        return nearest_support
    
    def _determine_trend(self, candles: List[dict]) -> TrendDirection:
        """Determine overall trend direction."""
        if len(candles) < 20:
            return TrendDirection.NEUTRAL
        
        current_price = candles[-1]["close"]
        ma20 = self._calculate_ma(candles, 20)
        momentum = self._analyze_momentum(candles, 5)
        
        # Count recent green candles
        recent = candles[-5:]
        green_count = sum(1 for c in recent if c["close"] > c["open"])
        
        # Strong bullish: above MA, rising momentum, mostly green
        if current_price > ma20 and momentum == "rising" and green_count >= 4:
            return TrendDirection.STRONG_BULLISH
        
        # Bullish: above MA or rising
        if current_price > ma20 or (momentum == "rising" and green_count >= 3):
            return TrendDirection.BULLISH
        
        # Strong bearish: below MA, falling momentum, mostly red
        if current_price < ma20 and momentum == "falling" and green_count <= 1:
            return TrendDirection.STRONG_BEARISH
        
        # Bearish: below MA or falling
        if current_price < ma20 or (momentum == "falling" and green_count <= 2):
            return TrendDirection.BEARISH
        
        return TrendDirection.NEUTRAL
    
    async def _analyze_timeframe(self, interval: str) -> Optional[TimeframeTrend]:
        """Full analysis for one timeframe."""
        candles = await self._fetch_candles(interval, limit=50)
        
        if not candles:
            return None
        
        current_price = candles[-1]["close"]
        ma20 = self._calculate_ma(candles, 20)
        
        # Determine HMM regime based on interval
        if interval == "30m" and self.detector_30m:
            hmm_regime = self._predict_regime(candles, self.detector_30m)
        elif interval == "1h" and self.detector_1h:
            hmm_regime = self._predict_regime(candles, self.detector_1h)
        elif interval == "4h" and self.detector_4h:
            hmm_regime = self._predict_regime(candles, self.detector_4h)
        else:
            hmm_regime = "N/A"  # No HMM for daily yet
        
        return TimeframeTrend(
            timeframe=interval,
            direction=self._determine_trend(candles),
            price_vs_ma20="above" if current_price > ma20 else "below",
            momentum=self._analyze_momentum(candles),
            last_3_candles=self._count_candles(candles, 3),
            key_level=self._find_key_level(candles),
            hmm_regime=hmm_regime
        )
    
    def _calculate_overall_bias(self) -> tuple:
        """Calculate overall market bias from all timeframes."""
        trends = []
        
        if self.current.htf_1h:
            trends.append(self.current.htf_1h.direction)
        if self.current.htf_4h:
            trends.append(self.current.htf_4h.direction)
        if self.current.htf_daily:
            trends.append(self.current.htf_daily.direction)
        
        if not trends:
            return TrendDirection.NEUTRAL, "0/0"
        
        # Count bullish/bearish
        bullish = sum(1 for t in trends if "Bullish" in t.value)
        bearish = sum(1 for t in trends if "Bearish" in t.value)
        total = len(trends)
        
        if bullish == total:
            return TrendDirection.STRONG_BULLISH, f"{bullish}/{total}"
        elif bullish > bearish:
            return TrendDirection.BULLISH, f"{bullish}/{total}"
        elif bearish == total:
            return TrendDirection.STRONG_BEARISH, f"{bearish}/{total}"
        elif bearish > bullish:
            return TrendDirection.BEARISH, f"{bearish}/{total}"
        
        return TrendDirection.NEUTRAL, f"0/{total}"
    
    async def update(self) -> MultiTimeframeContext:
        """Fetch and analyze all higher timeframes."""
        # Phase 59: Run native async fetches concurrently without outer to_thread wrapper
        results = await asyncio.gather(
            self._analyze_timeframe("30m"),
            self._analyze_timeframe("1h"),
            self._analyze_timeframe("4h"),
            self._analyze_timeframe("1d"),
            return_exceptions=True
        )
        
        # Safely assign results, ignoring exceptions for individual timeframes
        self.current.htf_30m = results[0] if not isinstance(results[0], Exception) else None
        self.current.htf_1h = results[1] if not isinstance(results[1], Exception) else None
        self.current.htf_4h = results[2] if not isinstance(results[2], Exception) else None
        self.current.htf_daily = results[3] if not isinstance(results[3], Exception) else None
        
        # Calculate overall bias
        self.current.overall_bias, self.current.alignment_score = self._calculate_overall_bias()
        
        # Calculate regime consensus from 1H and 4H
        regimes = []
        if self.current.htf_1h and self.current.htf_1h.hmm_regime != "N/A":
            regimes.append(self.current.htf_1h.hmm_regime)
        if self.current.htf_4h and self.current.htf_4h.hmm_regime != "N/A":
            regimes.append(self.current.htf_4h.hmm_regime)
        
        if regimes:
            # Prioritize: CRISIS > TRENDING > CALM
            if "CRISIS" in regimes:
                self.current.regime_consensus = "CRISIS"
            elif all(r == "TRENDING" for r in regimes):
                self.current.regime_consensus = "TRENDING"
            elif all(r == "CALM" for r in regimes):
                self.current.regime_consensus = "CALM"
            else:
                self.current.regime_consensus = "MIXED"
        
        return self.current

    async def get_regime(self, timeframe: str) -> str:
        """Get the regime for a specific timeframe."""
        if timeframe == "30m":
            return self.current.htf_30m.hmm_regime if self.current.htf_30m else "unknown"
        elif timeframe == "1h":
            return self.current.htf_1h.hmm_regime if self.current.htf_1h else "unknown"
        elif timeframe == "4h":
            return self.current.htf_4h.hmm_regime if self.current.htf_4h else "unknown"
        return "unknown"
    
    def format_for_gemini(self) -> str:
        """Format multi-timeframe context for Gemini prompt."""
        ctx = self.current
        
        lines = [
            "HIGHER TIMEFRAME CONTEXT:",
            ""
        ]
        
        if ctx.htf_1h:
            tf = ctx.htf_1h
            regime_str = f" | Regime: {tf.hmm_regime}" if tf.hmm_regime != "N/A" else ""
            lines.append(f"1H: {tf.direction.value}{regime_str} | MA20: {tf.price_vs_ma20} | Momentum: {tf.momentum}")
            lines.append(f"    Key Level: ${tf.key_level:,.0f}")
        
        if ctx.htf_4h:
            tf = ctx.htf_4h
            regime_str = f" | Regime: {tf.hmm_regime}" if tf.hmm_regime != "N/A" else ""
            lines.append(f"4H: {tf.direction.value}{regime_str} | MA20: {tf.price_vs_ma20} | Momentum: {tf.momentum}")
            lines.append(f"    Key Level: ${tf.key_level:,.0f}")
        
        if ctx.htf_daily:
            tf = ctx.htf_daily
            lines.append(f"1D: {tf.direction.value} | MA20: {tf.price_vs_ma20} | Momentum: {tf.momentum}")
            lines.append(f"    Key Level: ${tf.key_level:,.0f}")
        
        lines.append("")
        lines.append(f"OVERALL BIAS: {ctx.overall_bias.value} (Alignment: {ctx.alignment_score})")
        if ctx.regime_consensus != "unknown":
            lines.append(f"HTF REGIME: {ctx.regime_consensus}")
        
        return "\n".join(lines)


# Test harness
async def test_mtf():
    """Test multi-timeframe fetcher."""
    fetcher = MultiTimeframeFetcher(testnet=True)  # Use testnet
    
    print("Fetching multi-timeframe context...")
    await fetcher.update()
    
    print("\n" + fetcher.format_for_gemini())


if __name__ == "__main__":
    asyncio.run(test_mtf())
