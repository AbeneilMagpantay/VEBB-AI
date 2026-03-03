import mmap
import struct
import time
import os

class ControlBridge:
    """
    Python Supervisor -> C++ Hot-Path Command Bridge
    Matches ABI of vebb::ControlState in spsc_bridge.hpp
    """
    # Q=version, 17 doubles, i=isEnabled, 4s=pad
    STRUCT_FORMAT = "=Q d d d d d d d d d d d d d d d d d i 4s"
    SIZE = struct.calcsize(STRUCT_FORMAT)

    def __init__(self, shm_name="/vebb_control_s"):
        self.shm_path = f"/dev/shm/{shm_name.lstrip('/')}"
        self.fd = None
        self.mm = None
        
        # Initial wait for SHM to be created by Ingestor
        for _ in range(5):
            if os.path.exists(self.shm_path):
                break
            print(f"  [Control] Waiting for SHM {shm_name}...")
            time.sleep(1)

        try:
            self.fd = os.open(self.shm_path, os.O_RDWR)
            self.mm = mmap.mmap(self.fd, self.SIZE)
            print(f"  [Control] Bridge linked to {shm_name}")
        except Exception as e:
            print(f"  [Control] Error linking bridge: {e}")

    def update_params(self, 
                      lead_lag_threshold=5.0, 
                      obi_threshold=0.6, 
                      min_trade_size=0.001, 
                      max_position_size=0.1, 
                      toxicity_scalar=0.0,
                      sentiment_score=0.0,
                      entropy_threshold=0.3,
                      min_lambda_threshold=1.5,
                      wallet_margin_balance=0.0,
                      z_base=3.0,
                      scaling_gamma=0.5,
                      gobi_kappa=0.5,
                      is_trading_enabled=False):
        if not self.mm:
            return

        # Read current version
        data = self.mm[:8]
        current_version = struct.unpack("Q", data)[0]
        
        # 1. Start Write (Set version to odd)
        next_version = current_version + 1
        if next_version % 2 == 0: next_version += 1
        
        # 2. Pack Data
        # Ensure we fill all 14 doubles to match C++ ABI exactly
        # Parameters we don't actively update from python are zeroed (they aren't used effectively via python currently)
        packed = struct.pack(self.STRUCT_FORMAT,
                             next_version,
                             float(lead_lag_threshold),
                             float(obi_threshold),
                             float(min_trade_size),
                             float(max_position_size),
                             float(toxicity_scalar),
                             float(sentiment_score),
                             float(entropy_threshold),
                             float(min_lambda_threshold),
                             float(wallet_margin_balance),
                             0.0, # lambda_mu
                             0.0, # lambda_sigma
                             0.0, # hawkes_mu
                             0.0, # hawkes_alpha
                             0.0, # hawkes_beta
                             float(z_base),
                             float(scaling_gamma),
                             float(gobi_kappa),
                             1 if is_trading_enabled else 0,
                             b'\x00\x00\x00\x00')
        
        # 3. Commit Write
        self.mm[:self.SIZE] = packed
        
        # 4. End Write (Set version to even)
        final_version = next_version + 1
        self.mm[:8] = struct.pack("Q", final_version)

    def close(self):
        if self.mm:
            self.mm.close()
        if self.fd:
            os.close(self.fd)

if __name__ == "__main__":
    bridge = ControlBridge()
    while True:
        bridge.update_params(is_trading_enabled=True, obi_threshold=0.55, wallet_margin_balance=141.0)
        time.sleep(2)
