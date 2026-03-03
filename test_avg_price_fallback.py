
import asyncio
from exchange_client import ExchangeClient, OrderSide, OrderResult

class MockResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
    
    def json(self):
        return self.data

async def test_avg_price_fallback():
    print("=" * 60)
    print("  VEBB-AI: Exchange Client Avg Price Fallback Test")
    print("=" * 60)
    
    client = ExchangeClient(api_key="TEST_API_KEY", secret="TEST_SECRET", testnet=True)
    
    # Mock the _request method to return avgPrice=0
    def mock_request(method, endpoint, params, signed):
        print(f"[Mock] Request: {method} {endpoint} params={params}")
        if endpoint == "/fapi/v1/order":
            return {
                "orderId": "12345678",
                "symbol": "BTCUSDT",
                "status": "FILLED",
                "executedQty": "0.001",
                "avgPrice": "0.00000",  # SIMULATING THE ERROR CONDITION
                "origQty": "0.001",
                "price": "0.00000",
                "reduceOnly": False,
                "side": "BUY",
                "positionSide": "BOTH",
                "stopPrice": "0.00000",
                "closePosition": False,
                "time": 1234567890,
                "timeInForce": "GTC",
                "type": "MARKET",
                "activatePrice": None,
                "priceRate": None,
                "updateTime": 1234567890,
                "workingType": "CONTRACT_PRICE",
                "priceProtect": False
            }
        return {}

    # Override the request method
    client._request = mock_request
    
    estimated_price = 69420.0
    print(f"\n[Test] Placing Market Order with Estimated Price: ${estimated_price}")
    
    # Passing the fallback price
    result = await client.place_market_order(
        side=OrderSide.BUY, 
        qty=0.001, 
        estimated_price=estimated_price
    )
    
    print("\n[Result]")
    print(f"  Success: {result.success}")
    print(f"  Qty: {result.qty}")
    print(f"  Price (Result): ${result.price:.2f}")
    
    if result.price == 0.0:
        print("  ❌ FAILED: Price is still 0.0 (Fallback ignored)")
    elif result.price == estimated_price:
        print(f"  ✅ PASSED: Automatically used fallback price ${estimated_price:.2f}")
    else:
        print(f"  ⚠️ UNEXPECTED: Got ${result.price:.2f}")

if __name__ == "__main__":
    asyncio.run(test_avg_price_fallback())
