import sys
import os

# Add the venv path to sys.path to find the built module
venv_path = os.path.join(os.getcwd(), "vebb_core", "venv", "Lib", "site-packages")
sys.path.append(venv_path)

try:
    import vebb_core
    print("✅ vebb_core imported successfully!")
    
    lob = vebb_core.OrderBook()
    print("✅ OrderBook initialized.")
    
    # Mock depth update
    mock_json = '{"e":"depthUpdate","E":1625000000000,"s":"BTCUSDT","U":100,"u":110,"pu":99,"b":[["34000.0", "1.5"], ["33990.0", "2.0"]],"a":[["34010.0", "1.0"], ["34020.0", "2.5"]]}'
    lob.update(mock_json)
    print("✅ OrderBook updated with mock JSON.")
    
    obi = lob.calculate_obi(5)
    print(f"✅ OBI calculated: {obi:.4f}")
    
    top_bids, top_asks = lob.get_top_levels(1)
    print(f"✅ Top Bid: {top_bids}")
    print(f"✅ Top Ask: {top_asks}")
    
except ImportError as e:
    print(f"❌ Failed to import vebb_core: {e}")
except Exception as e:
    print(f"❌ An error occurred: {e}")
