
from order_book import OrderBookBuilder

def test_attr():
    ob = OrderBookBuilder()
    if hasattr(ob, 'obi'):
        print(f"PASS: OrderBookBuilder has 'obi' attribute. Value: {ob.obi}")
    else:
        print("FAIL: OrderBookBuilder missing 'obi' attribute.")
    
    # Simulate update
    ob.bids = [(100, 1.0)]
    ob.asks = [(101, 0.5)]
    ob._calculate_metrics()
    print(f"Updated OBI: {ob.obi:.3f}")

if __name__ == "__main__":
    test_attr()
