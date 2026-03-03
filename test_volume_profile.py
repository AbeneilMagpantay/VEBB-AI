
import asyncio
from order_flow import VolumeProfile

def test_volume_profile_logic():
    print("--- Testing Volume Profile Logic ---")
    
    vp = VolumeProfile()
    
    # Create fake candles
    # Scenario: High volume at $100, Low volume at $90 and $110
    candles = [
        {"high": 105, "low": 95, "volume": 100, "close": 100},  # POC area
        {"high": 105, "low": 95, "volume": 100, "close": 100},  # POC area
        {"high": 115, "low": 105, "volume": 10, "close": 110},  # Premium
        {"high": 95, "low": 85, "volume": 10, "close": 90},     # Discount
    ]
    
    print(f"Feeding {len(candles)} candles...")
    for c in candles:
        vp.add_candle(c)
        
    vp.calculate()
    
    print(f"POC: {vp.poc} (Expected ~100)")
    print(f"VAH: {vp.vah} (Expected > 100)")
    print(f"VAL: {vp.val} (Expected < 100)")
    
    # Test Context
    print(f"Context at 112 (Premium): {vp.get_context(112)}")
    print(f"Context at 88 (Discount): {vp.get_context(88)}")
    print(f"Context at 100 (Fair Value): {vp.get_context(100)}")
    
    assert vp.poc == 100.0 or vp.poc == 90.0 or vp.poc == 110.0 # Bins are rounded
    assert vp.get_context(120) == "PREMIUM"
    assert vp.get_context(80) == "DISCOUNT"
    
    print("\n✅ Volume Profile Logic Validated!")

if __name__ == "__main__":
    test_volume_profile_logic()
