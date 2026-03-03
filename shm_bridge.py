import ctypes
import mmap
import os
import time
import sys

class MarketState(ctypes.Structure):
    """
    Mirror of the Rust #[repr(C)] struct in shared_mem.rs.
    Must match exactly for zero-copy binary compatibility.
    """
    _fields_ = [
        ("sequence_id", ctypes.c_uint64),
        ("timestamp_ns", ctypes.c_int64),
        ("global_delta", ctypes.c_double),
        ("global_obi", ctypes.c_double),
        ("global_di", ctypes.c_double),
        ("global_raw_delta", ctypes.c_double),
        ("global_raw_volume", ctypes.c_double),
        ("binance_price", ctypes.c_double),
        ("binance_delta", ctypes.c_double),
        ("binance_nobi", ctypes.c_double),
        ("bybit_price", ctypes.c_double),
        ("bybit_delta", ctypes.c_double),
        ("bybit_nobi", ctypes.c_double),
        ("coinbase_price", ctypes.c_double),
        ("coinbase_delta", ctypes.c_double),
        ("coinbase_nobi", ctypes.c_double),
        ("coinbase_vol", ctypes.c_double),
        ("binance_vol", ctypes.c_double),
        ("bybit_vol", ctypes.c_double),
        ("lead_lag_theta", ctypes.c_double),
        
        # Phase 79: Weight Tracking
        ("binance_weight", ctypes.c_double),
        ("coinbase_weight", ctypes.c_double),
        ("bybit_weight", ctypes.c_double),
        
        # Phase 84/85: Tick-Level Stochastic Baselines & Entropy
        ("lambda_mu", ctypes.c_uint64),
        ("lambda_sigma", ctypes.c_uint64),
        ("ob_entropy", ctypes.c_uint64),
        
        # Phase 86: Fractional Calculus 
        ("entropy_z_score", ctypes.c_uint64),
        
        # Phase 102: Dynamic Global Unity Deviation Bounds
        ("dynamic_tau_upper", ctypes.c_uint64),
        ("dynamic_tau_lower", ctypes.c_uint64)
    ]

class SHMReader:
    def __init__(self, shm_link="vebb_shm"):
        self.shm_link = shm_link
        self.struct_size = ctypes.sizeof(MarketState)
        self.shm_obj = None
        self.mmap_obj = None
        self._connected = False

    def connect(self):
        """Connect to the shared memory segment created by Rust."""
        try:
            if sys.platform == "win32":
                # Windows implementation
                # In Windows, the tag name is used directly
                self.mmap_obj = mmap.mmap(-1, self.struct_size, tagname=self.shm_link, access=mmap.ACCESS_WRITE)
            else:
                # Linux/Unix implementation
                shm_path = f"/dev/shm/{self.shm_link}"
                if not os.path.exists(shm_path):
                    # Fallback for some systems
                    shm_path = f"/tmp/{self.shm_link}"
                
                fd = os.open(shm_path, os.O_RDWR)
                self.mmap_obj = mmap.mmap(fd, self.struct_size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
                os.close(fd)
            
            self._connected = True
            print(f"🔗 [SHM] Connected to {self.shm_link} bridge.")
            return True
        except Exception as e:
            print(f"❌ [SHM] Connection failed: {e}")
            return False

    def read(self):
        """Read the current state from RAM without copying."""
        if not self._connected:
            return None
        
        # Point the ctypes structure at the memory map buffer
        # This is strictly zero-copy.
        return MarketState.from_buffer(self.mmap_obj)

if __name__ == "__main__":
    # Test Runner
    reader = SHMReader()
    if reader.connect():
        print("Polling SHM for updates (Ctrl+C to stop)...")
        last_seq = -1
        try:
            while True:
                state = reader.read()
                if state.sequence_id != last_seq:
                    print(f"[{state.sequence_id}] BN: ${state.binance_price:,.1f} | BY: ${state.bybit_price:,.1f} | CB: ${state.coinbase_price:,.1f} | G-Delta: {state.global_raw_delta:+.2f}")
                    last_seq = state.sequence_id
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("Stopped.")
