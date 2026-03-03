
import asyncio
import json
import msgspec
import statistics
from typing import Dict, List, Tuple, Optional
import websockets

class OrderBookBuilder:
    """
    Maintains a local Order Book (Depth) using Binance WebSocket stream.
    Calculates OBI (Order Book Imbalance) and detects Liquidity Walls.
    """
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol.lower()
        # Use Futures WebSocket (fstream) instead of Spot (stream)
        self.ws_url = f"wss://fstream.binance.com/ws/{self.symbol}@depth20@100ms"
        self.bids: List[Tuple[float, float]] = []  # [(price, qty), ...]
        self.asks: List[Tuple[float, float]] = []  # [(price, qty), ...]
        self.obi: float = 0.0  # Order Book Imbalance (-1.0 to +1.0)
        self.last_update_id: int = 0
        self.running: bool = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the WebSocket connection."""
        self.running = True
        self._task = asyncio.create_task(self._listen())
        print(f"[OrderBook] Listening to {self.symbol}@depth20")

    async def stop(self):
        """Stop the WebSocket connection."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print(f"[OrderBook] Stopped.")

    async def _listen(self):
        """Main WebSocket loop."""
        while self.running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=None) as ws:
                    while self.running:
                        msg = await ws.recv()
                        try:
                            data = msgspec.json.decode(msg)
                            self._process_update(data)
                        except msgspec.DecodeError:
                            print(f"[OrderBook] Decode Error: {msg}")
            except Exception as e:
                print(f"[OrderBook] Connection error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    def feed(self, depth_data: dict):
        """Accept pre-processed depth data from DataStream (passive mode - no standalone WS needed).
        
        Expected keys: 'obi', 'bids', 'asks', 'last_update_id'
        This is called by main.py's _on_depth_update callback.
        """
        self.last_update_id = depth_data.get("last_update_id", 0)
        
        # Convert Rust LOB top levels format to our internal format
        # Rust returns list of [price, qty] pairs
        raw_bids = depth_data.get("bids", [])
        raw_asks = depth_data.get("asks", [])
        
        if raw_bids:
            self.bids = [(float(p), float(q)) for p, q in raw_bids]
        if raw_asks:
            self.asks = [(float(p), float(q)) for p, q in raw_asks]
        
        self._calculate_metrics()


    def _process_update(self, data: dict):
        """Update local book state from depth update."""
        # Binance depth20 stream sends the full snapshot every time
        self.last_update_id = data.get("lastUpdateId", 0)
        
        # Parse Bids and Asks: list of [price_str, qty_str]
        # Handle both formats: "bids" (Spot/std) and "b" (Futures sometimes)
        raw_bids = data.get("bids", data.get("b", []))
        raw_asks = data.get("asks", data.get("a", []))
        
        # Convert to float tuples
        self.bids = [(float(p), float(q)) for p, q in raw_bids]
        self.asks = [(float(p), float(q)) for p, q in raw_asks]
        
        self._calculate_metrics()

    def _calculate_metrics(self):
        """Calculate OBI across different depths."""
        if not self.bids or not self.asks:
            return
            
        # 1. Macro OBI (Whole Book - 20 levels)
        total_bid_vol = sum(q for _, q in self.bids)
        total_ask_vol = sum(q for _, q in self.asks)
        total_vol = total_bid_vol + total_ask_vol
        self.obi_macro = (total_bid_vol - total_ask_vol) / total_vol if total_vol > 0 else 0.0
        
        # 2. Top OBI (Immediate Pressure - Top 5 levels)
        top_bid_vol = sum(q for _, q in self.bids[:5])
        top_ask_vol = sum(q for _, q in self.asks[:5])
        top_vol = top_bid_vol + top_ask_vol
        self.obi_top = (top_bid_vol - top_ask_vol) / top_vol if top_vol > 0 else 0.0
        
        # Legacy attribute for backward compatibility
        self.obi = self.obi_macro

    def get_summary(self) -> Dict:
        """Return a structured summary of the book."""
        return {
            "obi": self.obi,
            "obi_macro": self.obi_macro,
            "obi_top": self.obi_top,
            "total_bid_vol": sum(q for _, q in self.bids),
            "total_ask_vol": sum(q for _, q in self.asks),
            "best_bid": self.bids[0][0] if self.bids else 0,
            "best_ask": self.asks[0][0] if self.asks else 0,
            "walls": self._detect_walls()
        }

    def _detect_walls(self) -> List[Dict]:
        """Identify price levels with significantly high liquidity."""
        walls = []
        if not self.bids or not self.asks:
            return walls
            
        # Calculate average volume per level to establish baseline
        all_qtys = [q for _, q in self.bids] + [q for _, q in self.asks]
        if not all_qtys:
            return walls
            
        avg_vol = statistics.mean(all_qtys)
        threshold = avg_vol * 3.0  # Wall = 3x average volume
        
        # Check Bids (Support Walls)
        for price, qty in self.bids:
            if qty > threshold:
                walls.append({"side": "BID", "price": price, "qty": qty, "strength": qty/avg_vol})
        
        # Check Asks (Resistance Walls)
        for price, qty in self.asks:
            if qty > threshold:
                walls.append({"side": "ASK", "price": price, "qty": qty, "strength": qty/avg_vol})
                
        # Sort by strength descending
        walls.sort(key=lambda x: x["strength"], reverse=True)
        return walls[:5]  # Top 5 walls

    def format_for_gemini(self) -> str:
        """Format the book state into a readable string for the LLM."""
        if not self.bids or not self.asks:
            return "Order Book: [No Data Available]"
            
        summary = self.get_summary()
        walls = summary["walls"]
        
        obi_desc = "NEUTRAL"
        if self.obi > 0.3: obi_desc = "BULLISH (Strong Demand)"
        elif self.obi > 0.1: obi_desc = "SLIGHTLY BULLISH"
        elif self.obi < -0.3: obi_desc = "BEARISH (Strong Supply)"
        elif self.obi < -0.1: obi_desc = "SLIGHTLY BEARISH"
        
        lines = [
            f"ORDER BOOK (Depth) CONTEXT:",
            f"- Macro OBI (20 levels): {self.obi_macro:+.2f}",
            f"- Top OBI (5 levels): {self.obi_top:+.2f} -> {obi_desc}",
            f"- Best Bid: ${summary['best_bid']:.2f} | Best Ask: ${summary['best_ask']:.2f}"
        ]
        
        if walls:
            lines.append("- Liquidity Walls (Potential Reversals):")
            for w in walls:
                side_icon = "🟢 Support" if w['side'] == "BID" else "🔴 Resistance"
                lines.append(f"  • {side_icon}: {w['qty']:.2f} BTC @ ${w['price']:.2f} ({w['strength']:.1f}x avg size)")
        else:
            lines.append("- No significant liquidity walls detected nearby.")
            
        return "\n".join(lines)

# Test harness
if __name__ == "__main__":
    async def test():
        ob = OrderBookBuilder()
        await ob.start()
        await asyncio.sleep(5)
        print("\n" + ob.format_for_gemini())
        await ob.stop()

    try:
        asyncio.run(test())
    except KeyboardInterrupt:
        pass
