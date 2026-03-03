
from position_manager import PositionManager, Side

def test_position_sizing():
    print("=" * 60)
    print("  VEBB-AI: Position Sizing Test ($100 Account)")
    print("=" * 60)
    
    # Simulate Account: $100 Capital, 20x Leverage (Synced)
    pm = PositionManager(initial_capital=100.0, leverage=1, max_position_pct=0.2)
    # Simulate Main.py Sync
    pm.leverage = 20 
    
    print(f"  Balance: ${pm.balance:.2f}")
    print(f"  Leverage: {pm.leverage}x")
    print(f"  Max Pos Pct: {pm.max_position_pct*100}%")
    
    # Calculate Buying Power
    buying_power = pm.balance * pm.leverage
    print(f"  Total Buying Power: ${buying_power:.2f}")
    
    # Test Price: $70,000 BTC
    price = 70000.0
    print(f"\n[Test] Calculating Max Position Size at ${price:,.2f}...")
    
    # Expected Logic:
    # Default Rule: $100 * 0.2 * 20 = $400 Notional.
    # $400 > $110 Min -> Should return ~0.0057 BTC ($400 value)
    
    qty = pm.get_max_position_size(price)
    
    notional = qty * price
    print(f"  Result Qty: {qty:.6f} BTC")
    print(f"  Notional Value: ${notional:.2f}")
    
    if notional >= 100:
        print("  ✅ PASSED: Valid trade size (> $100)")
    elif qty == 0.0:
        print("  ❌ FAILED: Size returned 0.0 (Too small)")
    else:
        print(f"  ❌ FAILED: Size {notional:.2f} < $100 (Rejected by Binance)")

    print("\n[Test 2] Small Account Stress Test ($20 Balance)")
    pm.balance = 20.0
    print(f"  Balance: ${pm.balance:.2f} (Buying Power: ${pm.balance * 20:.2f})")
    print("  Calculating size...")
    
    # Expected: 20 * 0.2 * 20 = $80 (Too Small)
    # Bump Logic: Checks capacity ($400 * 0.95 = $380 > $110) -> Should bump to $110.
    
    qty = pm.get_max_position_size(price)
    notional = qty * price
    print(f"  Result Qty: {qty:.6f} BTC")
    print(f"  Notional Value: ${notional:.2f}")
    
    if notional >= 110:
        print("  ✅ PASSED: Successfully bumped to Min Notional ($110)")
    else:
        print("  ❌ FAILED: Did not bump size correctly")

if __name__ == "__main__":
    test_position_sizing()
