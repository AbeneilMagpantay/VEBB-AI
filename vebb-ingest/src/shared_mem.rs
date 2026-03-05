use serde::{Deserialize, Serialize};

#[repr(C)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct MarketCoreState {
    pub sequence_id: u64,       // Increments every update
    pub timestamp_ns: i64,      // Unified PTP nanosecond timestamp
    
    // Global Aggregated Metrics
    pub global_delta: f64,      // Liquidity-Weighted Z-Score Composite Delta
    pub global_obi: f64,        // Liquidity-Weighted OBI (GOBI)
    pub global_di: f64,         // Divergence Index (Spot vs Futures Flow)
    pub global_raw_delta: f64,  // Sum of Delta BTC across all exchanges
    pub global_raw_volume: f64, // Sum of Volume BTC across all exchanges
    
    // Exchange Specifics (for Lead-Lag / Divergence)
    pub binance_price: f64,
    pub binance_delta: f64,
    pub binance_nobi: f64,
    
    pub bybit_price: f64,
    pub bybit_delta: f64,
    pub bybit_nobi: f64,
    
    pub coinbase_price: f64,
    pub coinbase_delta: f64,
    pub coinbase_nobi: f64,
    pub coinbase_vol: f64,
    
    pub binance_vol: f64,
    pub bybit_vol: f64,
    
    pub lead_lag_theta: f64,    // Coinbase-Binance Lead-Lag Score
    
    // Weighting Tracking (Phase 79)
    pub binance_weight: f64,
    pub coinbase_weight: f64,
    pub bybit_weight: f64,
}

#[repr(C)]
#[derive(Debug)]
pub struct MarketState {
    pub core: MarketCoreState,
    
    // Phase 84/85: Tick-Level Stochastic Baselines & Entropy (STRICT ATOMICS)
    pub lambda_mu: std::sync::atomic::AtomicU64,         // 24h Rolling Mean of Kyle's Lambda
    pub lambda_sigma: std::sync::atomic::AtomicU64,      // 24h Rolling StdDev of Kyle's Lambda
    pub ob_entropy: std::sync::atomic::AtomicU64,        // Shannon Entropy of L2 Order Book
    
    // Phase 86: Fractional Calculus & Temporal Alignment
    pub entropy_z_score: std::sync::atomic::AtomicU64,
    
    // Phase 102: Dynamic Global Unity Deviation Bounds (Calculated in Rust, atomic u64 fixed point bits)
    pub dynamic_tau_upper: std::sync::atomic::AtomicU64,
    pub dynamic_tau_lower: std::sync::atomic::AtomicU64,
    
    // Phase 116A: Time-at-Support Absorption Streak (consecutive 1-second windows of DID < 0.002 at high intensity)
    pub absorption_streak: std::sync::atomic::AtomicU64,
}

// Phase 81: Zero-Latency Event Bridge
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct MarketEvent {
    pub exchange: u8,   // 0: Binance, 1: Bybit, 2: Coinbase
    pub event_type: u8, // 0: Trade, 1: Depth
    pub side: i8,       // 1: Buy, -1: Sell, 0: Neutral
    pub _pad: u8,       // Alignment padding
    pub price: f64,
    pub quantity: f64,
    pub timestamp_ns: i64,
    pub bids: [f64; 10], // [p, q, p, q, p, q, p, q, p, q]
    pub asks: [f64; 10],
}

impl Default for MarketEvent {
    fn default() -> Self {
        Self {
            exchange: 0,
            event_type: 0,
            side: 0,
            _pad: 0,
            price: 0.0,
            quantity: 0.0,
            timestamp_ns: 0,
            bids: [0.0; 10],
            asks: [0.0; 10],
        }
    }
}

// Lock-Free SPSC Queue Layout for Shared Memory
const QUEUE_CAPACITY: usize = 4096; // Must be power of 2

#[repr(C)]
pub struct SPSCQueue {
    pub head: std::sync::atomic::AtomicU64,
    pub _pad1: [u64; 7], // Avoid false sharing (64-byte alignment)
    pub tail: std::sync::atomic::AtomicU64,
    pub _pad2: [u64; 7],
    pub buffer: [MarketEvent; QUEUE_CAPACITY],
}

#[repr(C)]
#[derive(Debug)]
pub struct ControlState {
    pub version: std::sync::atomic::AtomicU64, // Seqlock version counter
    pub lead_lag_threshold: f64,
    pub obi_threshold: f64,
    pub min_trade_size: f64,
    pub max_position_size: f64,
    pub toxicity_scalar: f64,
    pub sentiment_score: f64,
    pub entropy_threshold: f64,
    pub min_lambda_threshold: f64,
    pub wallet_margin_balance: f64,
    pub lambda_mu: f64,
    pub lambda_sigma: f64,
    pub hawkes_mu: f64,
    pub hawkes_alpha: f64,
    pub hawkes_beta: f64,
    // Phase 102 Dynamic Metrics Configuration
    pub z_base: f64,
    pub scaling_gamma: f64,
    pub gobi_kappa: f64,
    pub is_trading_enabled: i32, // 0 or 1
    pub _pad: [u8; 4],
}

impl Default for ControlState {
    fn default() -> Self {
        Self {
            version: std::sync::atomic::AtomicU64::new(0),
            lead_lag_threshold: 5.0,
            obi_threshold: 0.6,
            min_trade_size: 0.001,
            max_position_size: 0.1,
            toxicity_scalar: 0.0,
            sentiment_score: 0.0,
            entropy_threshold: 0.3,
            min_lambda_threshold: 1.5,
            wallet_margin_balance: 0.0,
            lambda_mu: 0.0,
            lambda_sigma: 0.0,
            hawkes_mu: 0.0,
            hawkes_alpha: 0.0,
            hawkes_beta: 0.0,
            z_base: 3.0,
            scaling_gamma: 0.5,
            gobi_kappa: 0.5,
            is_trading_enabled: 0,
            _pad: [0; 4],
        }
    }
}

impl SPSCQueue {
    pub fn push(&self, event: MarketEvent) -> bool {
        let h = self.head.load(std::sync::atomic::Ordering::Relaxed);
        let t = self.tail.load(std::sync::atomic::Ordering::Acquire);
        
        if h - t >= QUEUE_CAPACITY as u64 {
            return false; // Queue full
        }
        
        // Write to the buffer using raw pointer arithmetic to avoid UB with shared references
        let index = (h as usize) & (QUEUE_CAPACITY - 1);
        unsafe {
            let buffer_ptr = std::ptr::addr_of!(self.buffer) as *mut MarketEvent;
            let item_ptr = buffer_ptr.add(index);
            std::ptr::write(item_ptr, event);
        }
        
        self.head.store(h + 1, std::sync::atomic::Ordering::Release);
        true
    }
}

impl Default for MarketCoreState {
    fn default() -> Self {
        Self {
            sequence_id: 0,
            timestamp_ns: 0,
            global_delta: 0.0,
            global_obi: 0.0,
            global_di: 0.0,
            global_raw_delta: 0.0,
            global_raw_volume: 0.0,
            binance_price: 0.0,
            binance_delta: 0.0,
            binance_nobi: 0.0,
            bybit_price: 0.0,
            bybit_delta: 0.0,
            bybit_nobi: 0.0,
            coinbase_price: 0.0,
            coinbase_delta: 0.0,
            coinbase_nobi: 0.0,
            coinbase_vol: 0.0,
            binance_vol: 0.0,
            bybit_vol: 0.0,
            lead_lag_theta: 0.0,
            binance_weight: 0.5,
            coinbase_weight: 0.3,
            bybit_weight: 0.2,
        }
    }
}

impl Default for MarketState {
    fn default() -> Self {
        Self {
            core: MarketCoreState::default(),
            lambda_mu: std::sync::atomic::AtomicU64::new(0),
            lambda_sigma: std::sync::atomic::AtomicU64::new(0),
            ob_entropy: std::sync::atomic::AtomicU64::new(0),
            entropy_z_score: std::sync::atomic::AtomicU64::new(0),
            dynamic_tau_upper: std::sync::atomic::AtomicU64::new(0),
            dynamic_tau_lower: std::sync::atomic::AtomicU64::new(0),
            absorption_streak: std::sync::atomic::AtomicU64::new(0),
        }
    }
}

