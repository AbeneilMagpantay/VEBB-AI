"""
VEBB-AI: Exchange Client
Binance Futures API wrapper using direct REST calls.
Bypasses CCXT entirely to avoid testnet sandbox detection issues.
"""

import os
import asyncio
import time
import hmac
import hashlib
import requests
from typing import Optional
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# Bypass proxy settings
os.environ["NO_PROXY"] = "*"
for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(proxy_var, None)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: Optional[str]
    side: str
    qty: float
    price: float
    message: str


class ExchangeClient:
    """
    Binance Futures API client using direct REST calls.
    
    Bypasses CCXT to avoid testnet detection issues.
    Uses proper HMAC SHA256 signature for authenticated endpoints.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        testnet: bool = True,
        symbol: str = "BTC/USDT:USDT"
    ):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY")
        self.secret = secret or os.getenv("BINANCE_SECRET")
        self.testnet = testnet if os.getenv("BINANCE_TESTNET", "true").lower() == "true" else False
        self.symbol = symbol
        self.binance_symbol = "BTCUSDT"  # Binance format
        
        # Set base URL
        if self.testnet:
            self.base_url = "https://testnet.binancefuture.com"
            print("[ExchangeClient] Using FUTURES TESTNET (direct REST)")
        else:
            self.base_url = "https://fapi.binance.com"
            print("[ExchangeClient] Using MAINNET")
        
        self._initialized = bool(self.api_key and self.secret)
        if not self._initialized:
            print("[ExchangeClient] Running in MOCK mode (no API keys)")
        else:
            print(f"[ExchangeClient] Initialized for {self.symbol}")
    
    def _sign(self, params: dict) -> str:
        """Generate HMAC SHA256 signature."""
        query_string = urlencode(params)
        signature = hmac.new(
            self.secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_timestamp(self) -> int:
        """Get server timestamp to avoid sync issues."""
        try:
            response = requests.get(f"{self.base_url}/fapi/v1/time", timeout=5)
            return response.json()["serverTime"]
        except:
            return int(time.time() * 1000)
    
    def _request(self, method: str, endpoint: str, params: dict = None, signed: bool = False) -> dict:
        """Make API request."""
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        
        if params is None:
            params = {}
        
        if signed:
            params["timestamp"] = self._get_timestamp()
            params["recvWindow"] = 60000  # 60 second window for clock sync
            params["signature"] = self._sign(params)
        
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=10)
            elif method == "POST":
                response = requests.post(url, params=params, headers=headers, timeout=10)
            elif method == "DELETE":
                response = requests.delete(url, params=params, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unknown method: {method}")
            
            if response.status_code == 200:
                return response.json()
            else:
                error = response.json() if response.text else {"msg": response.text}
                raise Exception(f"API Error {response.status_code}: {error.get('msg', error)}")
                
        except requests.exceptions.RequestException as e:
            raise Exception(f"Request failed: {e}")
    
    async def close(self):
        """Close - no persistent connection to close."""
        pass
    
    async def set_leverage(self, leverage: int = 20) -> bool:
        """Set leverage for the trading pair."""
        if not self._initialized:
            print(f"[ExchangeClient] Set leverage to {leverage}x (mock)")
            return True
        
        try:
            params = {
                "symbol": "BTCUSDT",
                "leverage": leverage
            }
            result = await asyncio.to_thread(
                self._request, "POST", "/fapi/v1/leverage", params, True
            )
            print(f"[ExchangeClient] Leverage set to {leverage}x")
            return True
        except Exception as e:
            print(f"[ExchangeClient] Failed to set leverage: {e}")
            return False
    
    async def get_balance(self) -> float:
        """Get USDT balance."""
        if not self._initialized:
            return 100.0  # Mock balance
        
        try:
            data = await asyncio.to_thread(
                self._request, "GET", "/fapi/v2/balance", {}, True
            )
            for asset in data:
                if asset.get("asset") == "USDT":
                    return float(asset.get("balance", 0))
            return 0.0
        except Exception as e:
            print(f"[ExchangeClient] Error fetching balance: {e}")
            return 0.0
    
    async def get_position(self) -> Optional[dict]:
        """Get current position for symbol."""
        if not self._initialized:
            return None
        
        try:
            data = await asyncio.to_thread(
                self._request, "GET", "/fapi/v2/positionRisk", {"symbol": self.binance_symbol}, True
            )
            for pos in data:
                qty = float(pos.get("positionAmt", 0))
                if pos.get("symbol") == self.binance_symbol and qty != 0:
                    return {
                        "side": "LONG" if qty > 0 else "SHORT",
                        "qty": abs(qty),
                        "entry_price": float(pos.get("entryPrice", 0)),
                        "unrealized_pnl": float(pos.get("unRealizedProfit", 0))
                    }
            return None
        except Exception as e:
            print(f"[ExchangeClient] Error fetching position: {e}")
            return None
    
    async def get_current_price(self) -> float:
        """Get current market price."""
        if not self._initialized:
            return 68000.0  # Mock price
        
        try:
            data = await asyncio.to_thread(
                self._request, "GET", "/fapi/v1/ticker/price", {"symbol": self.binance_symbol}, False
            )
            return float(data.get("price", 0))
        except Exception as e:
            print(f"[ExchangeClient] Error fetching price: {e}")
            return 0.0
    
    async def get_klines(self, symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 500, start_time: Optional[int] = None) -> list:
        """Fetch historical klines (OHLCV)."""
        try:
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
            if start_time:
                params["startTime"] = start_time
                
            data = await asyncio.to_thread(
                self._request, "GET", "/fapi/v1/klines", params, False
            )
            return data
        except Exception as e:
            print(f"[ExchangeClient] Error fetching klines: {e}")
            return []
    
    async def place_market_order(self, side: OrderSide, qty: float, estimated_price: float = 0.0) -> OrderResult:
        """Place a market order."""
        if not self._initialized:
            return OrderResult(
                success=True,
                order_id="MOCK_ORDER_123",
                side=side.value,
                qty=qty,
                price=estimated_price or 68000.0,
                message="[MOCK] Order would be placed"
            )
        
        try:
            # Round quantity to 3 decimal places (BTCUSDT minimum step is 0.001)
            qty = round(qty, 3)
            
            params = {
                "symbol": self.binance_symbol,
                "side": side.value.upper(),
                "type": "MARKET",
                "quantity": qty,
            }
            
            order = await asyncio.to_thread(
                self._request, "POST", "/fapi/v1/order", params, True
            )
            
            # Avg Price usually present, but fallback to estimated_price if 0
            avg_price = float(order.get("avgPrice", 0))
            if avg_price == 0 and estimated_price > 0:
                print(f"[ExchangeClient] Warning: avgPrice=0, using estimated price ${estimated_price:.2f}")
                avg_price = estimated_price
            
            return OrderResult(
                success=True,
                order_id=str(order.get("orderId")),
                side=side.value,
                qty=float(order.get("executedQty", qty)),
                price=avg_price,
                message="Order executed successfully"
            )
            
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                side=side.value,
                qty=qty,
                price=0.0,
                message=f"Order failed: {str(e)}"
            )
    
    async def place_limit_order(self, side: OrderSide, qty: float, price: float) -> OrderResult:
        """Place a limit order."""
        if not self._initialized:
            return OrderResult(
                success=True,
                order_id="MOCK_LIMIT_456",
                side=side.value,
                qty=qty,
                price=price,
                message="[MOCK] Limit order would be placed"
            )
        
        try:
            params = {
                "symbol": self.binance_symbol,
                "side": side.value.upper(),
                "type": "LIMIT",
                "quantity": qty,
                "price": price,
                "timeInForce": "GTC",
            }
            
            order = await asyncio.to_thread(
                self._request, "POST", "/fapi/v1/order", params, True
            )
            
            return OrderResult(
                success=True,
                order_id=str(order.get("orderId")),
                side=side.value,
                qty=float(order.get("origQty", qty)),
                price=float(order.get("price", price)),
                message="Limit order placed"
            )
            
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                side=side.value,
                qty=qty,
                price=price,
                message=f"Order failed: {str(e)}"
            )
    
    async def cancel_all_orders(self) -> bool:
        """Cancel all open orders for symbol."""
        if not self._initialized:
            print("[ExchangeClient] [MOCK] Would cancel all orders")
            return True
        
        try:
            await asyncio.to_thread(
                self._request, "DELETE", "/fapi/v1/allOpenOrders", {"symbol": self.binance_symbol}, True
            )
            print(f"[ExchangeClient] Cancelled all orders for {self.symbol}")
            return True
        except Exception as e:
            print(f"[ExchangeClient] Error cancelling orders: {e}")
            return False
    
    async def get_last_trade_pnl(self, limit: int = 5) -> dict:
        """Fetch real PnL and fees for the last closed trade from Binance."""
        if not self._initialized:
            return {"realized_pnl": 0.0, "commission": 0.0}
            
        try:
            params = {
                "symbol": self.binance_symbol,
                "limit": limit
            }
            trades = await asyncio.to_thread(
                self._request, "GET", "/fapi/v1/userTrades", params, True
            )
            
            if not trades:
                return {"realized_pnl": 0.0, "commission": 0.0}
                
            # Sort by time descending to find the most recent trade that realized PnL
            # (Note: entry trades have 0 realized PnL, exit trades have the PnL)
            total_realized = 0.0
            total_commission = 0.0
            
            # Find the most recent sequence of trades with the same orderId or just the last few
            # We want the *last* realized PnL event
            for trade in reversed(trades):
                pnl = float(trade.get("realizedPnl", 0))
                comm = float(trade.get("commission", 0))
                if pnl != 0:
                    # We found an exit trade
                    return {
                        "realized_pnl": pnl,
                        "commission": comm,
                        "order_id": trade.get("orderId"),
                        "time": trade.get("time")
                    }
            
            return {"realized_pnl": 0.0, "commission": 0.0}
            
        except Exception as e:
            print(f"[ExchangeClient] Error fetching trade PnL: {e}")
            return {"realized_pnl": 0.0, "commission": 0.0}

    async def close_position(self) -> OrderResult:
        """Close current position with market order."""
        position = await self.get_position()
        
        if not position:
            return OrderResult(
                success=True,
                order_id=None,
                side="",
                qty=0.0,
                price=0.0,
                message="No position to close"
            )
        
        # Opposite side to close
        close_side = OrderSide.SELL if position["side"] == "LONG" else OrderSide.BUY
        
        return await self.place_market_order(close_side, position["qty"])


# Test harness
async def test_client():
    """Test the exchange client."""
    client = ExchangeClient(testnet=True)
    
    print("\n--- Exchange Client Test (Direct REST) ---")
    
    # Test price (public endpoint)
    price = await client.get_current_price()
    print(f"Current Price: ${price:,.2f}")
    
    # Test balance (private endpoint)
    balance = await client.get_balance()
    print(f"Balance: ${balance:.2f}")
    
    # Test position
    position = await client.get_position()
    print(f"Position: {position}")
    
    # Test mock order
    result = await client.place_market_order(OrderSide.BUY, 0.001)
    print(f"Order Result: {result}")


if __name__ == "__main__":
    asyncio.run(test_client())
