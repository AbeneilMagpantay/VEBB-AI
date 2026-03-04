"""
VEBB-AI: Real-Time Data Stream Module
Connects to Binance WebSocket for live BTCUSDT candle data.
"""

import asyncio
import json
import msgspec
import os
import numpy as np
from datetime import datetime
from typing import Callable, Optional

# Bypass system proxy for WebSocket connections
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
# Also clear any existing proxy settings that might interfere
for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(proxy_var, None)

import websockets
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from liquidation_stream import LiquidationStream

load_dotenv()

import sys

# Add the venv path to sys.path to find the built module
venv_path = os.path.join(os.getcwd(), "vebb_core", "venv", "Lib", "site-packages")
sys.path.append(venv_path)

import vebb_core  # Built with PyO3 in vebb_core/

# Binance WebSocket endpoints
BINANCE_WS_MAINNET = "wss://fstream.binance.com/ws"
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com/ws"


class DataStream:
    """Real-time candle and depth data stream from Binance Futures."""
    
    def __init__(self, symbol: str = "btcusdt", interval: str = "15m", testnet: bool = True):
        self.symbol = symbol.lower()
        self.interval = interval
        self.testnet = testnet
        self.ws_url = BINANCE_WS_TESTNET if testnet else BINANCE_WS_MAINNET
        
        # Streams: kline and depth
        self.kline_stream = f"{self.symbol}@kline_{self.interval}"
        self.depth_stream = f"{self.symbol}@depth20@100ms"
        
        # Callbacks
        self.on_candle_close: Optional[Callable] = None
        self.on_candle_update: Optional[Callable] = None
        self.on_depth_update: Optional[Callable] = None
        self.on_liquidation: Optional[Callable] = None
        self.on_sentinel_update: Optional[Callable] = None # For SOL lead-lag
        self.on_agg_trade: Optional[Callable] = None # For unified footprint feeding
        
        # Rust LOB Reconstructor
        self.lob = vebb_core.OrderBook()
        
        # Liquidation Stream
        self.liq_stream = LiquidationStream(symbol=symbol, testnet=testnet)
        self.liq_stream.on_liquidation = self._handle_liquidation
        
        # Internal state
        self._running = False
        self._ws = None
        self.secondary_symbol = "solusdt"
        
        # Phase 59: Queue-based processing to prevent 1011 Timeouts AND 'resume_reading' crashes
        self.message_queue = asyncio.Queue()
        self._worker_task = None
        
    async def _message_worker(self):
        """Background worker to process messages from the queue safely."""
        while self._running:
            try:
                # Wait for a message
                message = await self.message_queue.get()
                
                # Parse JSON synchronously using msgspec for ultra-low latency C-struct decoding
                try:
                    payload = msgspec.json.decode(message)
                except msgspec.DecodeError:
                    self.message_queue.task_done()
                    continue
                    
                await self._handle_message(payload)
                self.message_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[DataStream] Worker Error: {e}")

    async def start(self):
        """Connect to WebSockets (Combined Streams + Liquidation) and start receiving data."""
        self._running = True
        
        # Start Liquidation Stream in parallel
        asyncio.create_task(self.liq_stream.start())
        
        # Start the message processing worker
        self._worker_task = asyncio.create_task(self._message_worker())
        
        # Combined stream URL (Strip /ws if present for combined stream format)
        base_url = self.ws_url.replace("/ws", "")
        
        # Phase 56: Add SOL Sentinel streams
        sol_kline = f"{self.secondary_symbol}@kline_1m"
        sol_agg = f"{self.secondary_symbol}@aggTrade"
        btc_agg = f"{self.symbol}@aggTrade" # Also needed for Magnet/Lead-Lag
        
        url = f"{base_url}/stream?streams={self.kline_stream}/{self.depth_stream}/{sol_kline}/{sol_agg}/{btc_agg}"
        
        print(f"[DataStream] Connecting to {url}...")
        
        while self._running:
            try:
                # Disable strict client-side pings to prevent 1011 Timeout exceptions
                async with websockets.connect(
                    url, 
                    ping_interval=None
                ) as ws:
                    self._ws = ws
                    print(f"[DataStream] Connected! Streaming Kline & Depth for {self.symbol.upper()}...")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        # Queue the raw text immediately so the library can instantly check for pings
                        self.message_queue.put_nowait(message)
                        
            except websockets.exceptions.ConnectionClosed as e:
                print(f"[DataStream] Connection closed: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except asyncio.TimeoutError:
                print(f"[DataStream] Timeout during connection. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"[DataStream] Error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
    
    async def stop(self):
        """Stop the data streams."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
        if self._ws:
            await self._ws.close()
        await self.liq_stream.stop()
        print("[DataStream] Stopped.")
    
    async def _handle_message(self, payload: dict):
        """Process pre-parsed dictionary and trigger callbacks."""
        try:
            stream = payload.get("stream", "")
            data = payload.get("data", {})
            
            # 1. Handle Depth Updates (Rust Core)
            if "@depth20" in stream:
                # Direct JSON string to Rust. We re-stringify it here since the worker parsed the outer payload.
                self.lob.update(json.dumps(data))
                
                if self.on_depth_update:
                    # Calculate OBI synchronously inside a thread so we don't block the ping loop
                    def get_depth_data():
                        # Rust get_top_levels returns (bids_list, asks_list) as a single tuple
                        top_bids, top_asks = self.lob.get_top_levels(5)
                        return {
                            "obi": self.lob.calculate_obi(20),
                            "bids": top_bids,
                            "asks": top_asks,
                            "last_update_id": self.lob.last_update_id
                        }
                    
                    depth_data = await asyncio.to_thread(get_depth_data)
                    await self.on_depth_update(depth_data)
                        
            # 2. Handle Kline Updates (Primary Symbol only)
            elif f"{self.symbol}@kline" in stream:
                kline = data.get("k", {})
                candle = {
                    "ts": datetime.fromtimestamp(kline["t"] / 1000),
                    "open": float(kline["o"]),
                    "high": float(kline["h"]),
                    "low": float(kline["l"]),
                    "close": float(kline["c"]),
                    "volume": float(kline["v"]),
                    "is_closed": kline["x"]
                }
                
                # Synthetic Close: If Binance drops the `x=True` event, detect the timestamp shift
                if getattr(self, "current_candle_ts", None) and candle["ts"] > self.current_candle_ts:
                    if getattr(self, "last_candle_data", None) and not getattr(self, "last_candle_closed", False):
                        print(f"\n[DataStream] ⚠️ Missing 'Close' event from Binance. Triggering Synthetic Close for {self.current_candle_ts.time()}")
                        if self.on_candle_close:
                            # Force the last known state to close
                            synthetic_candle = self.last_candle_data.copy()
                            synthetic_candle["is_closed"] = True
                            await self.on_candle_close(synthetic_candle)
                
                # Update tracker
                self.current_candle_ts = candle["ts"]
                self.last_candle_data = candle
                self.last_candle_closed = candle["is_closed"]
                
                if candle["is_closed"]:
                    if self.on_candle_close:
                        await self.on_candle_close(candle)
                else:
                    if self.on_candle_update:
                        await self.on_candle_update(candle)
            
            # Phase 56: Handle SOL (Secondary) Kline Updates
            elif f"{self.secondary_symbol}@kline" in stream:
                # We can use this for specific SOL impulse logic later
                pass
            
            # 3. Handle Sentinel / AggTrade Updates
            elif "@aggTrade" in stream:
                if self.on_sentinel_update:
                    agg_data = {
                        "symbol": stream.split("@")[0].upper(),
                        "price": float(data["p"]),
                        "qty": float(data["q"]),
                        "side": "SELL" if data["m"] else "BUY", # m=True means buyer-maker (Market Sell)
                        "ts": datetime.fromtimestamp(data["T"] / 1000)
                    }
                    await self.on_sentinel_update(agg_data)

                # Phase 78c Unity: Feed Footprint directly from unified stream
                if self.on_agg_trade and "BTCUSDT" in stream.upper():
                    self.on_agg_trade(data)
            
            elif "solusdt@kline_1m" in stream:
                # Handle 1m SOL candles if needed for impulse tracking
                pass
                        
        except Exception as e:
            print(f"[DataStream] Error parsing message: {e}")

    async def _handle_liquidation(self, data: dict):
        """Relay liquidation data to main callback."""
        if self.on_liquidation:
            await self.on_liquidation(data)


# Feature calculation functions (from thesis)
def calculate_garman_klass(candle: dict) -> float:
    """Computes Garman-Klass Volatility Proxy for a single candle."""
    log_hl = np.log(candle["high"] / candle["low"])
    log_co = np.log(candle["close"] / candle["open"])
    gk_var = 0.5 * (log_hl ** 2) - (2 * np.log(2) - 1) * (log_co ** 2)
    return np.sqrt(max(gk_var, 0))  # Ensure non-negative


def calculate_yang_zhang(candle_buffer, window: int = 20) -> float:
    """
    Phase 115: Yang-Zhang Volatility Estimator (replaces Garman-Klass).
    
    Decomposes variance into three components:
    1. Overnight (close-to-open) — captures virtual gaps, API disconnects
    2. Open-to-close (trend) — directional drift component
    3. Rogers-Satchell (intraday) — drift-independent extreme excursions
    
    Combined via variance-minimizing weight k derived from α=1.34.
    Requires N≥2 candles. Falls back to GK for insufficient buffer.
    """
    n = len(candle_buffer)
    if n < 2:
        # Fallback to single-candle GK when buffer is too small
        return calculate_garman_klass(candle_buffer[-1])
    
    # Use at most `window` recent candles
    start = max(0, n - window)
    candles = [candle_buffer[i] for i in range(start, n)]
    N = len(candles)
    
    if N < 2:
        return calculate_garman_klass(candles[-1])
    
    # Normalized log prices (skip first candle for overnight component)
    log_oc = []  # Overnight: ln(Open_i / Close_{i-1})
    log_ho = []  # ln(High / Open)
    log_lo = []  # ln(Low / Open)
    log_co = []  # ln(Close / Open)
    
    for i in range(1, N):
        prev_close = candles[i - 1]["close"]
        o, h, l, c = candles[i]["open"], candles[i]["high"], candles[i]["low"], candles[i]["close"]
        
        if prev_close <= 0 or o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        
        log_oc.append(np.log(o / prev_close))
        log_ho.append(np.log(h / o))
        log_lo.append(np.log(l / o))
        log_co.append(np.log(c / o))
    
    n_valid = len(log_oc)
    if n_valid < 2:
        return calculate_garman_klass(candle_buffer[-1])
    
    log_oc = np.array(log_oc)
    log_ho = np.array(log_ho)
    log_lo = np.array(log_lo)
    log_co = np.array(log_co)
    
    # Component 1: Overnight variance (close-to-open)
    oc_mean = np.mean(log_oc)
    sigma_o_sq = np.sum((log_oc - oc_mean) ** 2) / (n_valid - 1)
    
    # Component 2: Open-to-close variance (trend)
    co_mean = np.mean(log_co)
    sigma_c_sq = np.sum((log_co - co_mean) ** 2) / (n_valid - 1)
    
    # Component 3: Rogers-Satchell variance (drift-independent intraday)
    rs_terms = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)
    sigma_rs_sq = np.mean(rs_terms)
    
    # Variance minimization weight k (α=1.34 from Yang & Zhang 2000 proof)
    alpha = 1.34
    k = (alpha - 1.0) / (alpha + (n_valid + 1.0) / (n_valid - 1.0))
    
    # Combined Yang-Zhang variance
    yz_var = sigma_o_sq + k * sigma_c_sq + (1.0 - k) * sigma_rs_sq
    
    return float(np.sqrt(max(yz_var, 0)))


def calculate_log_return(close: float, prev_close: float) -> float:
    """Computes log return between two closes."""
    if prev_close <= 0:
        return 0.0
    return np.log(close / prev_close)


# Test harness
async def test_stream():
    """Test the data stream connection."""
    stream = DataStream(testnet=True)
    
    async def on_close(candle):
        gk = calculate_garman_klass(candle)
        print(f"[CLOSED] {candle['ts']} | Close: ${candle['close']:,.2f} | GK Vol: {gk:.6f}")
    
    async def on_update(candle):
        print(f"[LIVE] Close: ${candle['close']:,.2f}", end="\r")
    
    stream.on_candle_close = on_close
    stream.on_candle_update = on_update
    
    try:
        await stream.start()
    except KeyboardInterrupt:
        await stream.stop()


if __name__ == "__main__":
    print("Starting VEBB-AI Data Stream Test...")
    asyncio.run(test_stream())
