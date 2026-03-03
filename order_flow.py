"""
VEBB-AI: Order Flow Analysis Module
Builds footprint chart data from aggTrades stream.
"""

import os
import asyncio
import collections
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Callable
import json
import msgspec
import websockets

# Bypass proxy for WebSocket
os.environ["NO_PROXY"] = "*"
for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(proxy_var, None)


@dataclass
class PriceLevel:
    """Volume data at a specific price level."""
    price: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    
    @property
    def delta(self) -> float:
        return self.buy_volume - self.sell_volume
    
    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume
    
    @property
    def imbalance(self) -> str:
        """Return imbalance indicator if significant."""
        if self.total_volume < 0.1:  # Minimum volume threshold
            return ""
        ratio = self.buy_volume / max(self.sell_volume, 0.001)
        if ratio > 2.0:
            return "🟢 BUY"
        elif ratio < 0.5:
            return "🔴 SELL"
        return ""


@dataclass
class FootprintData:
    """Complete footprint analysis for a candle."""
    levels: dict = field(default_factory=dict)  # price -> PriceLevel
    cumulative_delta: float = 0.0
    total_buy_volume: float = 0.0
    total_sell_volume: float = 0.0
    vwap_numerator: float = 0.0  # sum(price * volume)
    vwap_denominator: float = 0.0  # sum(volume)
    
    @property
    def poc(self) -> float:
        """Point of Control - price level with highest volume."""
        if not self.levels:
            return 0.0
        return max(self.levels.values(), key=lambda x: x.total_volume).price
    
    @property
    def vwap(self) -> float:
        """Volume Weighted Average Price."""
        if self.vwap_denominator == 0:
            return 0.0
        return self.vwap_numerator / self.vwap_denominator
    
    @property
    def delta_direction(self) -> str:
        """Bullish or Bearish based on cumulative delta."""
        if self.cumulative_delta > 0:
            return "Bullish"
        elif self.cumulative_delta < 0:
            return "Bearish"
        return "Neutral"
    
    def get_top_levels(self, n: int = 10) -> list:
        """Get top N price levels by volume."""
        sorted_levels = sorted(
            self.levels.values(),
            key=lambda x: x.total_volume,
            reverse=True
        )
        return sorted_levels[:n]


class FootprintBuilder:
    """
    Connects to Binance aggTrades stream and builds footprint data.
    
    Aggregates individual trades into price buckets and tracks:
    - Buy vs Sell volume at each level
    - Cumulative delta
    - POC (Point of Control)
    - VWAP
    """
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        bin_size: float = 10.0,  # Price bucket size in dollars
        testnet: bool = True
    ):
        self.symbol = symbol.lower()
        self.bin_size = bin_size
        self.testnet = testnet
        
        # Callbacks
        self.on_trade: Optional[Callable] = None  # Hook for Microstructure Engine
        
        # Current candle footprint
        self.current: FootprintData = FootprintData()
        
        # Previous candle footprint (for analysis)
        self.previous: Optional[FootprintData] = None
        
        self._running = False
        self._ws = None
        
        # Use futures stream for testnet
        base_url = "wss://stream.binancefuture.com" if testnet else "wss://fstream.binance.com"
        self.ws_url = f"{base_url}/ws/{self.symbol}@aggTrade"
        
        # Phase 59: Queue-based processing to prevent 1011 Timeouts AND 'resume_reading' crashes
        self.message_queue = asyncio.Queue()
        self._worker_task = None

    async def _message_worker(self):
        """Background worker to process messages from the queue safely."""
        while self._running:
            try:
                # Wait for a message
                message = await self.message_queue.get()
                
                # Parse JSON synchronously inside the worker task (safer than to_thread)
                try:
                    trade = msgspec.json.decode(message)
                except msgspec.DecodeError:
                    self.message_queue.task_done()
                    continue
                    
                self._process_trade(trade)
                self.message_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[FootprintBuilder] Worker Error: {e}")

    def _get_price_bin(self, price: float) -> float:
        """Round price to nearest bin."""
        return round(price / self.bin_size) * self.bin_size
    
    def _process_trade(self, trade: dict):
        """Process a single aggTrade message."""
        if 'p' not in trade or 'q' not in trade:
            return
            
        price = float(trade["p"])
        qty = float(trade["q"])
        is_seller_maker = trade["m"]  # True = buyer was taker (aggressive buy)
        
        # Get price bin
        price_bin = self._get_price_bin(price)
        
        # Create level if doesn't exist
        if price_bin not in self.current.levels:
            self.current.levels[price_bin] = PriceLevel(price=price_bin)
        
        level = self.current.levels[price_bin]
        
        # Seller maker = buyer was aggressive (market buy)
        # Buyer maker = seller was aggressive (market sell)
        if is_seller_maker:
            level.sell_volume += qty
            self.current.total_sell_volume += qty
            self.current.cumulative_delta -= qty
        else:
            level.buy_volume += qty
            self.current.total_buy_volume += qty
            self.current.cumulative_delta += qty
        
        # Update VWAP
        self.current.vwap_numerator += price * qty
        self.current.vwap_denominator += qty
        
        # Phase 40: Microstructure Hook
        if self.on_trade:
            self.on_trade(price, qty, not is_seller_maker)
    
    def reset(self):
        """Reset footprint for new candle. Saves current to previous."""
        self.previous = self.current
        self.current = FootprintData()
    
    def get_summary(self) -> dict:
        """Get formatted footprint summary for Gemini."""
        fp = self.current
        
        return {
            "poc": fp.poc,
            "vwap": fp.vwap,
            "cumulative_delta": fp.cumulative_delta,
            "delta_direction": fp.delta_direction,
            "total_buy_volume": fp.total_buy_volume,
            "total_sell_volume": fp.total_sell_volume,
            "top_levels": [
                {
                    "price": level.price,
                    "buys": level.buy_volume,
                    "sells": level.sell_volume,
                    "delta": level.delta,
                    "imbalance": level.imbalance
                }
                for level in fp.get_top_levels(10)
            ]
        }
    
    def format_for_gemini(self) -> str:
        """Format footprint data as text for Gemini prompt."""
        fp = self.current
        
        if not fp.levels:
            return "FOOTPRINT: No data yet"
        
        lines = []
        lines.append(f"FOOTPRINT ANALYSIS (Current Candle):")
        lines.append(f"")
        lines.append(f"POC: ${fp.poc:,.0f} | VWAP: ${fp.vwap:,.2f}")
        lines.append(f"Cumulative Delta: {fp.cumulative_delta:+.3f} BTC ({fp.delta_direction})")
        lines.append(f"Aggressive Buys: {fp.total_buy_volume:.3f} BTC | Sells: {fp.total_sell_volume:.3f} BTC")
        lines.append(f"")
        lines.append(f"{'Level':<12} | {'Buys':>10} | {'Sells':>10} | {'Delta':>10} | Signal")
        lines.append("-" * 65)
        
        top_levels = fp.get_top_levels(8)
        # Sort by price descending for display
        top_levels_sorted = sorted(top_levels, key=lambda x: x.price, reverse=True)
        
        for level in top_levels_sorted:
            poc_marker = " ← POC" if level.price == fp.poc else ""
            imbalance = level.imbalance
            lines.append(
                f"${level.price:<10,.0f} | {level.buy_volume:>10.3f} | "
                f"{level.sell_volume:>10.3f} | {level.delta:>+10.3f} | {imbalance}{poc_marker}"
            )
        
        return "\n".join(lines)
    
    async def start(self):
        """Start the aggTrades stream connection."""
        self._running = True
        print(f"[FootprintBuilder] Connecting to {self.ws_url}...")
        
        # Start the message processing worker
        self._worker_task = asyncio.create_task(self._message_worker())
        
        while self._running:
            try:
                # Disable strict client-side pings to prevent 1011 Timeout exceptions
                async with websockets.connect(
                    self.ws_url, 
                    ping_interval=None
                ) as ws:
                    self._ws = ws
                    print(f"[FootprintBuilder] Connected! Building footprint data...")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        # Queue the raw text immediately so the library can instantly check for pings
                        self.message_queue.put_nowait(message)
                            
            except websockets.exceptions.ConnectionClosed as e:
                print(f"[FootprintBuilder] Connection closed: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except asyncio.TimeoutError:
                print(f"[FootprintBuilder] Timeout during connection. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"[FootprintBuilder] Error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
    
    async def stop(self):
        """Stop the stream connection."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass # Expected
        if self._ws:
            await self._ws.close()
        print("[FootprintBuilder] Stopped.")


@dataclass
class VolumeProfile:
    """
    Composite Volume Profile for a range of candles.
    Calculates Value Area (VAH/VAL), POC, and Session VWAP.
    """
    levels: defaultdict = field(default_factory=lambda: defaultdict(float))
    delta_levels: defaultdict = field(default_factory=lambda: defaultdict(float))
    total_volume: float = 0.0
    net_delta: float = 0.0
    total_pv: float = 0.0  # Total Price * Volume for VWAP
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    vwap: float = 0.0  # Session VWAP
    
    def reset(self):
        """Reset the profile for a new session."""
        self.levels = defaultdict(float)
        self.delta_levels = defaultdict(float)
        self.total_volume = 0.0
        self.net_delta = 0.0
        self.total_pv = 0.0
        self.poc = 0.0
        self.vah = 0.0
        self.val = 0.0
        self.vwap = 0.0

    def decay(self, factor: float):
        """Scale all historical volume and delta by a factor (Bayesian Decay)."""
        if factor >= 1.0 or factor <= 0:
            return
            
        # Scale all price buckets
        for price in self.levels:
            self.levels[price] *= factor
            self.delta_levels[price] *= factor
        
        # Scale global accumulators
        self.total_volume *= factor
        self.net_delta *= factor
        self.total_pv *= factor
        
        # Force immediate re-calculation of tactical levels (POC, VAH, VAL)
        self.calculate()

    def add_candle(self, candle: dict, delta: Optional[float] = None):
        """
        Estimate volume and delta at price from a candle.
        If delta is not provided, it assumes delta is 0 for historical distribution.
        """
        high = float(candle["high"])
        low = float(candle["low"])
        vol = float(candle["volume"])
        close = float(candle["close"])
        typical_price = (high + low + close) / 3
        
        candle_delta = delta if delta is not None else 0.0
        
        # Accumulate for VWAP
        self.total_pv += typical_price * vol
        self.net_delta += candle_delta
        
        if high == low:
            self.levels[high] += vol
            self.delta_levels[high] += candle_delta
            self.total_volume += vol
        else:
            # Distribute into $10 bins
            bin_size = 10.0
            start_bin = round(low / bin_size) * bin_size
            end_bin = round(high / bin_size) * bin_size
            
            # Number of bins covered
            num_bins = int((end_bin - start_bin) / bin_size) + 1
            vol_per_bin = vol / num_bins
            delta_per_bin = candle_delta / num_bins
            
            curr = start_bin
            while curr <= end_bin:
                self.levels[curr] += vol_per_bin
                self.delta_levels[curr] += delta_per_bin
                self.total_volume += vol_per_bin
                curr += bin_size
                
    def calculate(self, value_area_pct: float = 0.70):
        """Calculate POC, VAH, VAL, and VWAP."""
        if not self.levels:
            return

        # Calculate VWAP
        if self.total_volume > 0:
            self.vwap = self.total_pv / self.total_volume

        # 1. Find POC
        sorted_levels = sorted(self.levels.items(), key=lambda x: x[0])  # Sort by price
        self.poc = max(self.levels.items(), key=lambda x: x[1])[0]  # Price with max vol
        
        # 2. Integrate to find VA (70%)
        target_vol = self.total_volume * value_area_pct
        try:
            current_vol = self.levels[self.poc]
            levels_dict = dict(self.levels)
            up_idx = sorted_levels.index((self.poc, levels_dict[self.poc]))
            down_idx = up_idx
            
            while current_vol < target_vol:
                vol_up = sorted_levels[up_idx + 1][1] if up_idx + 1 < len(sorted_levels) else 0
                vol_down = sorted_levels[down_idx - 1][1] if down_idx - 1 >= 0 else 0
                
                if vol_up == 0 and vol_down == 0:
                    break
                    
                if vol_up > vol_down:
                    current_vol += vol_up
                    up_idx += 1
                else:
                    current_vol += vol_down
                    down_idx -= 1
            
            self.val = sorted_levels[down_idx][0]
            self.vah = sorted_levels[up_idx][0]
        except Exception:
            # Fallback if binary search or index fails
            self.val = sorted_levels[0][0]
            self.vah = sorted_levels[-1][0]
            
    def format_for_gemini(self) -> str:
        """Format the Volume Profile into text for Gemini LLM context."""
        lines = []
        lines.append("VOLUME PROFILE (Session):")
        lines.append(f"POC (Point of Control): ${self.poc:,.0f}")
        lines.append(f"VAH (Value Area High): ${self.vah:,.0f}")
        lines.append(f"VAL (Value Area Low): ${self.val:,.0f}")
        lines.append(f"VWAP (Volume Weighted Avg Price): ${self.vwap:,.2f}")
        
        lvns = self.get_lvns(threshold=0.3)
        if lvns:
            lvn_str = ", ".join([f"${l:,.0f}" for l in lvns[:5]])
            lines.append(f"LVNs (Liquidity Vacuums): {lvn_str}")
        else:
            lines.append("LVNs (Liquidity Vacuums): None")
            
        return "\n".join(lines)
        
    def get_context(self, price: float) -> str:
        """Return position relative to Value Area."""
        if price > self.vah:
            return "PREMIUM"
        elif price < self.val:
            return "DISCOUNT"
        else:
            return "FAIR_VALUE"

    def get_lvns(self, threshold: float = 0.3) -> list[float]:
        """
        Identify Low Volume Nodes (LVNs).
        Returns a list of price levels where volume is < threshold * POC volume.
        These are 'Liquidity Vacuums' where price can move rapidly.
        """
        if not self.levels or self.poc == 0:
            return []
            
        poc_vol = self.levels[self.poc]
        lvns = []
        
        # Only check levels within the current VAH/VAL range for relevance
        for price, vol in self.levels.items():
            if self.val <= price <= self.vah:
                if vol < (poc_vol * threshold):
                    lvns.append(price)
        
        return sorted(lvns)

    def get_delta_gravity(self, n: int = 3) -> list[dict]:
        """
        Identify price levels with the highest 'Unfilled Aggression' (Net Delta).
        Returns top N levels by absolute delta.
        """
        if not self.delta_levels:
            return []
            
        # Sort by absolute delta descending
        sorted_deltas = sorted(
            self.delta_levels.items(),
            key=lambda x: abs(x[1]),
            reverse=True
        )
        
        return [{"price": p, "delta": d} for p, d in sorted_deltas[:n]]


if __name__ == "__main__":
    asyncio.run(test_footprint())
