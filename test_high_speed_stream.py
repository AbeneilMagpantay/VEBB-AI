import asyncio
import os
import sys

# Add venv to path
venv_path = os.path.join(os.getcwd(), "vebb_core", "venv", "Lib", "site-packages")
sys.path.append(venv_path)

from data_stream import DataStream

async def test_high_speed_stream():
    """Test the combined stream with Rust-based OBI calculation."""
    stream = DataStream(testnet=True)
    
    async def on_depth(data):
        print(f"[DEPTH] OBI: {data['obi']:+.4f} | Bids: {len(data['bids'])} | Asks: {len(data['asks'])} | ID: {data['last_update_id']}")
        
    async def on_candle(candle):
        print(f"[CANDLE] {candle['ts']} | Close: ${candle['close']:,.2f}")

    stream.on_depth_update = on_depth
    stream.on_candle_update = on_candle
    
    print("🚀 Starting High-Speed DataStream Test (Rust + Combined Streams)...")
    
    try:
        # Run for 30 seconds
        await asyncio.wait_for(stream.start(), timeout=30)
    except asyncio.TimeoutError:
        print("\n✅ Test completed (30s timeout reached).")
    except Exception as e:
        print(f"❌ Error during stream test: {e}")
    finally:
        await stream.stop()

if __name__ == "__main__":
    asyncio.run(test_high_speed_stream())
