"""
VEBB-AI: Liquidation Stream Module
Connects to Binance WebSocket for real-time liquidation (forceOrder) data.
"""

import asyncio
import json
import msgspec
import os
from datetime import datetime, timedelta
from collections import deque
from typing import Callable, Optional

# Bypass system proxy
os.environ["NO_PROXY"] = "*"
import websockets

class LiquidationStream:
    """Monitors real-time liquidation events for a specific symbol."""
    
    def __init__(self, symbol: str = "btcusdt", testnet: bool = True):
        self.symbol = symbol.lower()
        self.testnet = testnet
        # Note: Testnet often doesn't have a reliable liquidation stream, 
        # but we use the corresponding endpoint for consistency.
        if testnet:
            self.ws_url = f"wss://stream.binancefuture.com/ws/{self.symbol}@forceOrder"
        else:
            self.ws_url = f"wss://fstream.binance.com/ws/{self.symbol}@forceOrder"
            
        self.on_liquidation: Optional[Callable] = None
        self._running = False
        
        # 1-minute rolling window for liquidation volume
        self.liquidation_events = deque()
        self.total_liquidated_v_1m = 0.0
        
    async def start(self):
        """Start the liquidation stream."""
        self._running = True
        print(f"[LiquidationStream] Connecting to {self.ws_url}...")
        
        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=None) as ws:
                    print(f"[LiquidationStream] Connected to {self.symbol.upper()} Liquidation Stream.")
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)
            except Exception as e:
                if self._running:
                    print(f"[LiquidationStream] Error: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def stop(self):
        """Stop the stream."""
        self._running = False
        print("[LiquidationStream] Stopped.")

    async def _handle_message(self, raw_message: str):
        """Process liquidation message."""
        try:
            data = msgspec.json.decode(raw_message).get("o", {})
            if not data:
                return
                
            qty = float(data.get("q", 0))
            price = float(data.get("p", 0))
            side = data.get("S", "") # BUY or SELL
            
            # Add to rolling window
            now = datetime.now()
            self.liquidation_events.append({
                "ts": now,
                "qty": qty,
                "price": price,
                "side": side
            })
            
            # Cleanup old events (> 1m)
            self._cleanup_old_events(now)
            
            # Signal callback
            if self.on_liquidation:
                await self.on_liquidation({
                    "qty": qty,
                    "price": price,
                    "side": side,
                    "v_1m": self.total_liquidated_v_1m
                })
                
        except Exception as e:
            print(f"[LiquidationStream] Parse Error: {e}")

    def _cleanup_old_events(self, now: datetime):
        """Remove events older than 1 minute and update total volume."""
        cutoff = now - timedelta(minutes=1)
        while self.liquidation_events and self.liquidation_events[0]["ts"] < cutoff:
            self.liquidation_events.popleft()
            
        self.total_liquidated_v_1m = sum(e["qty"] for e in self.liquidation_events)

    def is_capitulating(self, threshold_btc: float = 25.0) -> bool:
        """Check if 1-minute liquidation volume exceeds threshold."""
        return self.total_liquidated_v_1m >= threshold_btc

if __name__ == "__main__":
    async def test():
        ls = LiquidationStream(testnet=False) # Use mainnet for real tests
        async def on_liq(data):
            print(f"🔥 LIQUIDATION: {data['side']} {data['qty']} BTC @ ${data['price']:,.2f} | 1m Total: {data['v_1m']:.2f}")
        ls.on_liquidation = on_liq
        await ls.start()
    
    asyncio.run(test())
