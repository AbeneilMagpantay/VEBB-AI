#pragma once
#include <atomic>
#include <cstdint>
#include <array>

namespace vebb {

// ABI-stable Market Event Struct (Matches Rust shared_mem.rs)
struct alignas(8) MarketEvent {
    uint8_t exchange;   // 0: Binance, 1: Bybit, 2: Coinbase
    uint8_t event_type; // 0: Trade, 1: Depth
    int8_t side;        // 1: Buy, -1: Sell, 0: Neutral
    uint8_t _pad;       // Alignment padding
    double price;
    double quantity;
    int64_t timestamp_ns;
    double bids[10];    // [p, q, p, q, p, q, p, q, p, q]
    double asks[10];
};

// POD struct for consistent local copies of control parameters
struct ControlData {
    double lead_lag_threshold;
    double obi_threshold;
    double min_trade_size;
    double max_position_size;
    double toxicity_scalar;  // Phase 82: informs reservation spread
    double sentiment_score;   // Phase 82: shifts OBI hysteresis
    double entropy_threshold;
    double min_lambda_threshold;

    // Phase 87: Extreme Margin Defense
    double wallet_margin_balance;
    double lambda_mu;        // Phase 83: Kyle's Lambda 24h Mean
    double lambda_sigma;     // Phase 83: Kyle's Lambda 24h StdDev
    double hawkes_mu;        // Phase 83: Hawkes Background Intensity
    double hawkes_alpha;     // Phase 83: Hawkes Infectivity
    double hawkes_beta;      // Phase 83: Hawkes Decay Rate
    int32_t is_trading_enabled;
    uint8_t _pad[4];
};

struct alignas(8) ControlState {
    std::atomic<uint64_t> version;
    double lead_lag_threshold;
    double obi_threshold;
    double min_trade_size;
    double max_position_size;
    double toxicity_scalar;
    double sentiment_score;
    double entropy_threshold;
    double min_lambda_threshold;
    
    // Phase 87: Extreme Margin Defense 
    double wallet_margin_balance;
    double lambda_mu;
    double lambda_sigma;
    double hawkes_mu;
    double hawkes_alpha;
    double hawkes_beta;
    int32_t is_trading_enabled;
    uint8_t _pad[4];

    // Helper to read state consistently into a POD struct (Seqlock)
    bool read_consistent(ControlData& dest) const {
        uint64_t v1 = version.load(std::memory_order_acquire);
        if (v1 % 2 != 0) return false; // Write in progress
        
        dest.lead_lag_threshold = lead_lag_threshold;
        dest.obi_threshold = obi_threshold;
        dest.min_trade_size = min_trade_size;
        dest.max_position_size = max_position_size;
        dest.toxicity_scalar = toxicity_scalar;
        dest.sentiment_score = sentiment_score;
        dest.entropy_threshold = entropy_threshold;
        dest.min_lambda_threshold = min_lambda_threshold;
        dest.wallet_margin_balance = wallet_margin_balance;
        dest.lambda_mu = lambda_mu;
        dest.lambda_sigma = lambda_sigma;
        dest.hawkes_mu = hawkes_mu;
        dest.hawkes_alpha = hawkes_alpha;
        dest.hawkes_beta = hawkes_beta;
        dest.is_trading_enabled = is_trading_enabled;
        
        uint64_t v2 = version.load(std::memory_order_acquire);
        return v1 == v2;
    }
};

// ABI-stable Market Event Struct (Matches Rust shared_mem.rs MarketCoreState + Additions)
struct MarketState {
    // MarketCoreState (Rust)
    double global_raw_volume;
    double global_raw_delta;
    double global_obi;
    double global_di;         // Phase 46: Liquidity Divergence Index
    double local_binance_obi; // Local isolated view
    
    // Phase 79: Tri-lingual Execution Context
    double binance_vol;
    double bybit_vol;
    
    double lead_lag_theta;    // Coinbase-Binance Lead-Lag Score
    
    // Weighting Tracking (Phase 79)
    double binance_weight;
    double coinbase_weight;
    double bybit_weight;

    // Phase 84/85: Tick-Level Stochastic Baselines & Entropy (STRICT ATOMICS)
    // We use atomic<uint64_t> to prevent 64-bit floating point memory tearing
    // across the Rust/C++ boundary. The uint64_t holds the bit-representation of double.
    std::atomic<uint64_t> lambda_mu;
    std::atomic<uint64_t> lambda_sigma;
    std::atomic<uint64_t> ob_entropy;
    
    // Phase 86: Fractional Calculus & Temporal Alignment
    std::atomic<uint64_t> entropy_z_score;
};

static constexpr size_t QUEUE_CAPACITY = 4096;

// Lock-Free Single-Producer Single-Consumer (SPSC) Ring Buffer
// Designed for cross-language Shared Memory IPC.
struct SPSCQueue {
    std::atomic<uint64_t> head;
    uint64_t _pad1[7]; // Cache-line padding (64 bytes) to avoid false sharing
    std::atomic<uint64_t> tail;
    uint64_t _pad2[7];
    MarketEvent buffer[QUEUE_CAPACITY];

    // Consumer side: Try to pop an event from the queue
    bool pop(MarketEvent& event) {
        uint64_t t = tail.load(std::memory_order_relaxed);
        uint64_t h = head.load(std::memory_order_acquire);
        
        if (h == t) {
            return false; // Queue empty
        }
        
        event = buffer[t & (QUEUE_CAPACITY - 1)];
        tail.store(t + 1, std::memory_order_release);
        return true;
    }

    // Producer side: Try to push an event into the queue
    bool push(const MarketEvent& event) {
        uint64_t h = head.load(std::memory_order_relaxed);
        uint64_t t = tail.load(std::memory_order_acquire);
        
        if (h - t >= QUEUE_CAPACITY) {
            return false; // Queue full
        }
        
        buffer[h & (QUEUE_CAPACITY - 1)] = event;
        head.store(h + 1, std::memory_order_release);
        return true;
    }
};

} // namespace vebb
