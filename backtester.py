"""
VEBB-AI: Backtester
Tests the Volume Profile strategy on historical data.
"""

import asyncio
import pandas as pd
import numpy as np
import requests
from datetime import datetime
from dataclasses import dataclass, field
from typing import List
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from order_flow import VolumeProfile
from microstructure import MicrostructureEngine
from gemini_analyst import GeminiAnalyst
import math

def calculate_ema(data: pd.Series, span: int) -> pd.Series:
    """Calculate EMA using pandas."""
    return data.ewm(span=span, adjust=False).mean()

# Bypass proxy
os.environ["NO_PROXY"] = "*"
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class BacktestTrade:
    """Record of a backtested trade."""
    entry_time: datetime
    exit_time: datetime
    side: str  # LONG or SHORT
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    exit_reason: str


class VolumeProfileBacktester:
    """
    Backtest the Volume Profile strategy.
    
    Logic:
    - Build VP from lookback window
    - Calculate VAL/VAH (70% Value Area)
    - Entry:
        - Long if Price < VAL (Discount) + Bullish Delta
        - Short if Price > VAH (Premium) + Bearish Delta
    """
    
    def __init__(
        self,
        timeframe: str = "5m",
        initial_capital: float = 100.0,
        leverage: int = 20,
        sl_pct: float = 0.02,
        tp_pct: float = 0.01,
        lookback_candles: int = 100
    ):
        self.timeframe = timeframe
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.lookback = lookback_candles
        
        self.capital = initial_capital
        self.trades: List[BacktestTrade] = []
        self.position = None
        self.entry_price = 0.0
        self.entry_time = None
        self.side = None
        
        # New for Phase 40
        self.micro = MicrostructureEngine()
        self.gemini = GeminiAnalyst()
        self.obi_multiplier = 1.0 # default
    
    def download_data(self, start_date: str = "2025-01-01", end_date: str = "2025-12-31") -> pd.DataFrame:
        """Download historical klines from Binance."""
        print(f"\n📥 Downloading {self.timeframe} data from {start_date} to {end_date}...")
        
        tf_ms = {"5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000}
        interval_ms = tf_ms.get(self.timeframe, 5 * 60 * 1000)
        
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
        now_ts = int(datetime.now().timestamp() * 1000)
        end_ts = min(end_ts, now_ts)
        
        all_klines = []
        current_ts = start_ts
        
        while current_ts < end_ts:
            url = f"https://api.binance.com/api/v3/klines"
            params = {
                "symbol": "BTCUSDT",
                "interval": self.timeframe,
                "startTime": current_ts,
                "limit": 1000
            }
            
            try:
                response = requests.get(url, params=params, timeout=10, verify=False)
                if response.status_code != 200:
                    print(f"Warning: API returned status {response.status_code}")
                    break
                
                klines = response.json()
                if not klines:
                    break
                
                all_klines.extend(klines)
                current_ts = klines[-1][0] + interval_ms
                print(f"  Downloaded {len(all_klines)} candles...", end="\r")
                
            except Exception:
                break
        
        print(f"\n✅ Downloaded {len(all_klines)} candles")
        
        df = pd.DataFrame(all_klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
            
        return df
    
    def simulate_delta(self, candle: pd.Series, prev_candles: pd.DataFrame) -> float:
        """Simulate delta (approximate)."""
        if candle["high"] == candle["low"]:
            return 0.0
        
        move = candle["close"] - candle["open"]
        range_size = candle["high"] - candle["low"]
        
        avg_volume = prev_candles["volume"].mean() if len(prev_candles) > 0 else candle["volume"]
        vol_ratio = candle["volume"] / avg_volume if avg_volume > 0 else 1.0
        
        # Delta = Direction * Volume Ratio
        delta = (move / range_size) * vol_ratio * 5  # Scale factor
        return delta
    
    async def run(self, df: pd.DataFrame, entry_buffer: int = 25):
        """Run backtest on dataframe."""
        self.capital = self.initial_capital
        self.trades = []
        self.position = None
        
        # print(f"   Simulating {len(df)} candles with Lookback={self.lookback} and Buffer={entry_buffer}%...")
        
        for i in range(self.lookback, len(df)):
            current_candle = df.iloc[i]
            window = df.iloc[i-self.lookback:i]
            
            # 1. Update Microstructure & VP
            self.micro.update_with_candle(current_candle.to_dict())
            metrics = self.micro.calculate_metrics(current_candle["close"], self.simulate_delta(current_candle, window))
            
            vp = VolumeProfile()
            for _, row in window.iterrows():
                vp.add_candle({
                    "high": row["high"], 
                    "low": row["low"], 
                    "close": row["close"],
                    "volume": row["volume"]
                })
            vp.calculate()
            
            # 2. Simulate Metrics for AI/Sniper
            price = current_candle["close"]
            obi = (self.simulate_delta(current_candle, window) / 10.0) * self.obi_multiplier
            
            # 3. Sniper Strategy (Phase 47: Percentage-Based Aggression)
            if self.position is None:
                context = vp.get_context(price)
                pct_va = (price - vp.val) / (vp.vah - vp.val) * 100 if vp.vah != vp.val else 50.0
                
                # Check for Long at Discount or Bottom buffer% of VA
                is_long_zone = (context == "DISCOUNT") or (context == "FAIR_VALUE" and pct_va < entry_buffer)
                # Check for Short at Premium or Top buffer% of VA
                is_short_zone = (context == "PREMIUM") or (context == "FAIR_VALUE" and pct_va > (100 - entry_buffer))
                
                if is_long_zone or is_short_zone:
                    # Simulation Heuristic: OBI > 0.5 for Long, OBI < -0.5 for Short
                    if is_long_zone and obi > 0.5:
                        self._open_trade("LONG", price, current_candle["timestamp"])
                    elif is_short_zone and obi < -0.5:
                        self._open_trade("SHORT", price, current_candle["timestamp"])
            
            # 4. Manage Position
            else:
                self._manage_position(current_candle)

        return self._calculate_results()

    def _open_trade(self, side, price, time):
        self.position = True
        self.side = side
        self.entry_price = price
        self.entry_time = time
        
    def _manage_position(self, candle):
        low = candle["low"]
        high = candle["high"]
        timestamp = candle["timestamp"]
        
        if self.side == "LONG":
            sl = self.entry_price * (1 - self.sl_pct)
            tp = self.entry_price * (1 + self.tp_pct)
            
            if low <= sl:
                self._close_trade(timestamp, sl, "SL")
            elif high >= tp:
                self._close_trade(timestamp, tp, "TP")
                
        else: # SHORT
            sl = self.entry_price * (1 + self.sl_pct)
            tp = self.entry_price * (1 - self.tp_pct)
            
            if high >= sl:
                self._close_trade(timestamp, sl, "SL")
            elif low <= tp:
                self._close_trade(timestamp, tp, "TP")

    def _close_trade(self, time, price, reason):
        pnl = (price - self.entry_price) / self.entry_price
        if self.side == "SHORT":
            pnl = -pnl
            
        pnl_val = 100 * pnl * self.leverage
        self.capital += pnl_val
        
        self.trades.append(BacktestTrade(
            self.entry_time, time, self.side, self.entry_price, price,
            pnl * self.leverage, pnl_val, reason
        ))
        self.position = None

    def _calculate_results(self) -> dict:
        """Calculate backtest statistics."""
        if not self.trades:
            return {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "pnl": 0.0,
                "final_capital": self.capital
            }
        
        wins = [t for t in self.trades if t.pnl_usd > 0]
        losses = [t for t in self.trades if t.pnl_usd <= 0]
        return {
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins)/len(self.trades)*100 if self.trades else 0,
            "pnl": self.capital - self.initial_capital,
            "final_capital": self.capital
        }

async def run_parameter_sweep():
    # Setup
    tester = VolumeProfileBacktester(timeframe="5m")
    
    # Download or generate data
    df = tester.download_data(start_date="2025-01-01")
    if len(df) == 0:
        # Generate Sine Wave pattern for testing
        n = 5000
        x = np.linspace(0, 20 * np.pi, n)
        price = 95000 + 1000 * np.sin(x) + np.random.normal(0, 50, n)
        df = pd.DataFrame({
            "timestamp": pd.date_range(start="2025-01-01", periods=n, freq="5min"),
            "open": price, "high": price + 50, "low": price - 50, "close": price,
            "volume": np.random.lognormal(5, 1, n) * 10
        })

    if len(df) > 5000:
        df = df.iloc[-5000:]
        
    print(f"\n🚀 Phase 40: Starting Multi-Dimensional Parameter Sweep")
    print(f"   Target: Lookback (5 or 10) x OBI Multiplier (0.5 to 2.0)")
    
    results = []
    lookbacks = [300] # Focus on the best lookback from previous run
    obi_multipliers = [1.0] # Standardize OBI for the buffer test
    entry_buffers = [5, 10, 15, 20, 25]
    
    for lb in lookbacks:
        tester.lookback = lb
        for mult in obi_multipliers:
            tester.obi_multiplier = mult
            for buffer in entry_buffers:
                # We need to temporarily modify the internal logic for the sweep
                # This requires a slight refactor of the run method or passing buffer as an argument
                # For this sweep, we'll use a specific tester attribute
                tester.entry_buffer = buffer
                print(f"\n🔍 Testing: Buffer={buffer}% (LB={lb}, OBI_Mult={mult})")
                res = await tester.run(df, entry_buffer=buffer)
                res["lookback"] = lb
                res["obi_mult"] = mult
                res["buffer"] = buffer
                results.append(res)
                print(f"   📊 Res: PnL=${res['pnl']:.2f} | WR={res['win_rate']:.1f}% ({res['trades']} trades)")
        
    print("\n" + "="*50)
    print("🏆 BUFFER OPTIMIZATION SUMMARY")
    print("="*50)
    results.sort(key=lambda x: x['pnl'], reverse=True)
    for r in results:
        print(f"Buffer: {r['buffer']}% | LB: {r['lookback']} => PnL: ${r['pnl']:.2f} (WR: {r['win_rate']:.1f}%)")
        
if __name__ == "__main__":
    asyncio.run(run_parameter_sweep())
