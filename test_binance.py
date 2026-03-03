"""
Test Binance Futures Testnet Connection
"""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv('.env.5m')

import ccxt.async_support as ccxt

async def test_connection():
    api_key = os.getenv('BINANCE_API_KEY')
    secret = os.getenv('BINANCE_SECRET')
    
    print(f"API Key: {api_key[:15]}...")
    print(f"Secret: {secret[:15]}...")
    
    # Create exchange
    exchange = ccxt.binanceusdm({
        "apiKey": api_key,
        "secret": secret,
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
        "enableRateLimit": True,
    })
    
    # Set sandbox mode
    exchange.set_sandbox_mode(True)
    
    # Fix URLs like in our exchange_client.py
    testnet_base = "https://testnet.binancefuture.com"
    exchange.urls['api']['fapiPublic'] = f"{testnet_base}/fapi/v1"
    exchange.urls['api']['fapiPrivate'] = f"{testnet_base}/fapi/v1"
    exchange.urls['api']['fapiPublicV2'] = f"{testnet_base}/fapi/v2"
    exchange.urls['api']['fapiPrivateV2'] = f"{testnet_base}/fapi/v2"
    exchange.urls['api']['public'] = f"{testnet_base}/fapi/v1"
    exchange.urls['api']['private'] = f"{testnet_base}/fapi/v1"
    
    print("\nURLs after fix:")
    print(f"  fapiPublic: {exchange.urls['api'].get('fapiPublic')}")
    print(f"  fapiPrivate: {exchange.urls['api'].get('fapiPrivate')}")
    
    try:
        # Test 1: Fetch exchange info (public endpoint)
        print("\n1. Testing public endpoint (exchangeInfo)...")
        markets = await exchange.load_markets()
        print(f"   ✅ Loaded {len(markets)} markets")
        
        # Test 2: Fetch balance (private endpoint)
        print("\n2. Testing private endpoint (balance)...")
        balance = await exchange.fetch_balance()
        usdt = balance.get('USDT', {})
        print(f"   ✅ USDT Balance: {usdt.get('free', 0):.2f} (free) / {usdt.get('total', 0):.2f} (total)")
        
        # Test 3: Fetch ticker
        print("\n3. Testing ticker...")
        ticker = await exchange.fetch_ticker('BTC/USDT:USDT')
        print(f"   ✅ BTC Price: ${ticker['last']:,.2f}")
        
        # Test 4: Try small order (will likely fail due to minimum size)
        print("\n4. Testing order placement...")
        try:
            order = await exchange.create_order(
                symbol='BTC/USDT:USDT',
                type='market',
                side='buy',
                amount=0.001  # Small test amount
            )
            print(f"   ✅ Order placed! ID: {order['id']}")
        except Exception as e:
            print(f"   ❌ Order failed: {e}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(test_connection())
