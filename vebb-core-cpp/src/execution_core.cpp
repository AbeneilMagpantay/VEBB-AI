#include <iostream>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <chrono>
#include <vector>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <atomic>
#include "spsc_bridge.hpp"

using namespace vebb;

// Safely convert atomic bits to double without tearing
inline double load_shm_double(const std::atomic<uint64_t>& atomic_val) {
    uint64_t val = atomic_val.load(std::memory_order_acquire);
    double d;
    std::memcpy(&d, &val, sizeof(double));
    return d;
}

// Zero-Allocation OrderBook Snapshot (Top 5)
struct OrderBookSnapshot {
    double bids[5][2]; // [price, qty]
    double asks[5][2];
    double last_price = 0;
    double obi = 0;
    int64_t last_update_ns = 0;
    double depth_sum_ema = 0; // EMA baseline for detecting "vacuum" crashes

    // Phase 83: Kyle's Lambda (Iceberg Detection)
    double signed_vol_sqrt_sum = 0; // sum(sqrt(|V|))
    double abs_price_delta_sum = 0; // sum(|dP|)
    double kyle_lambda = 0;
    double lambda_prob = 0;        // Signal probability [0, 1]
    double last_lambda_price = 0;

    // Phase 86: Hawkes Process (O(1) Sum-of-Exponentials M=15 Approximation)
    double hawk_terms[15] = {0};
    double hawk_intensity = 0;
    double hawk_derivative = 0;
    int64_t last_hawk_ts = 0;
};

class ExecutionEngine {
private:
    MarketState* market_shm;
    SPSCQueue* event_queue;
    ControlState* control_shm;
    ControlData config; // Local consistent copy of thresholds
    
    OrderBookSnapshot binance_ob;
    OrderBookSnapshot bybit_ob;
    OrderBookSnapshot coinbase_ob;
    
    // Global rate delimiters (State variables)
    int64_t last_global_sniper_alert = 0;
    int64_t last_global_pivot_alert = 0;

    // Phase 82: Dynamic Sigmoid Hysteresis
    double get_dynamic_obi_threshold() {
        double theta_base = config.obi_threshold;
        double sigma_sen = config.sentiment_score; // [-1.0, 1.0]
        double kappa = 0.30;
        double lambda = 4.0;
        
        // Sigmoid: Th = base + (kappa / (1 + exp(-lambda * sigma_sen)) - kappa/2)
        double shift = (kappa / (1.0 + std::exp(-lambda * sigma_sen))) - (kappa / 2.0);
        return std::clamp(theta_base - shift, 0.1, 0.9); // Lowers threshold if sentiment is POSITIVE
    }

    void update_orderbook(OrderBookSnapshot& ob, const MarketEvent& event) {
        double current_depth_sum = 0;
        int valid_levels = 0;
        for (int i = 0; i < 5; ++i) {
            // Only update if the event actually provides this level (non-zero price)
            // This prevents partial "delta" updates from clearing out the local book
            if (event.bids[i*2] > 0) {
                ob.bids[i][0] = event.bids[i*2];
                ob.bids[i][1] = event.bids[i*2+1];
            }
            if (event.asks[i*2] > 0) {
                ob.asks[i][0] = event.asks[i*2];
                ob.asks[i][1] = event.asks[i*2+1];
            }
            
            if (ob.bids[i][0] > 0 && ob.asks[i][0] > 0) valid_levels++;
            current_depth_sum += (ob.bids[i][1] + ob.asks[i][1]);
        }
        
        // Flash-Crash Detection: "Vacuum" monitor using EMA
        // We trigger if depth drops below 5% of the EMA baseline
        const double alpha = 0.01; // Slow smoothing for the baseline
        if (valid_levels == 5) {
            if (ob.depth_sum_ema == 0) {
                ob.depth_sum_ema = current_depth_sum;
            } else {
                // Crisis Check: 95% evaporation
                if (current_depth_sum < (ob.depth_sum_ema * 0.05)) {
                    static uint64_t last_alert_ns = 0;
                    uint64_t now_ns = static_cast<uint64_t>(std::chrono::system_clock::now().time_since_epoch().count());
                    if (now_ns - last_alert_ns > 1000000000ULL) { // Rate limit 1s
                        std::cout << "⚠️ [CRISIS] Liquidity Vacuum Detected | Depth: " 
                                  << current_depth_sum << " (Baseline: " << ob.depth_sum_ema << ")" << std::endl;
                        last_alert_ns = now_ns;
                    }
                }
                // Update baseline only if not in a "crisis" to avoid chasing the crash
                if (current_depth_sum > (ob.depth_sum_ema * 0.1)) {
                    ob.depth_sum_ema = (alpha * current_depth_sum) + ((1.0 - alpha) * ob.depth_sum_ema);
                }
            }
        }
        
        ob.last_update_ns = event.timestamp_ns;

        // Calculate OBI
        double bid_vol = 0, ask_vol = 0;
        for (int i = 0; i < 5; ++i) {
            bid_vol += ob.bids[i][1];
            ask_vol += ob.asks[i][1];
        }
        ob.obi = (bid_vol + ask_vol > 0) ? (bid_vol - ask_vol) / (bid_vol + ask_vol) : 0;
    }

    void handle_trade(OrderBookSnapshot& ob, const MarketEvent& event) {
        // 1. Hawkes Intensity Update (O(1) Sum-of-Exponentials M=15 for Power-Law)
        double dt = (ob.last_hawk_ts == 0) ? 0 : (double)(event.timestamp_ns - ob.last_hawk_ts) / 1e9;
        
        double bg_mu = config.hawkes_mu; // Exogenous background rate
        
        // Phase 86: M=15 State-space approximation reaching 10^5 seconds
        ob.hawk_intensity = bg_mu;
        double new_derivative = 0;

        if (ob.last_hawk_ts > 0 && dt > 0) {
            for (int i = 0; i < 15; ++i) {
                // beta scales geometrically from 1.0 down to ~0.000012
                double beta_i = std::pow(10.0, -(double)i * 0.35);
                // alpha scales proportionally to maintain a safe branching ratio < 1
                double alpha_i = beta_i * 0.05;
                
                ob.hawk_terms[i] *= std::exp(-beta_i * dt);
                
                new_derivative -= beta_i * ob.hawk_terms[i];
                
                // Excitation and Phase 87 Overflow Clamp
                ob.hawk_terms[i] = std::min(ob.hawk_terms[i] + alpha_i, 10000.0);
                ob.hawk_intensity += ob.hawk_terms[i];
            }
            ob.hawk_derivative = new_derivative;
        } else {
            for (int i = 0; i < 15; ++i) {
                double beta_i = std::pow(10.0, -(double)i * 0.35);
                double alpha_i = beta_i * 0.05;
                
                // Phase 87 Overflow Clamp
                ob.hawk_terms[i] = std::min(ob.hawk_terms[i] + alpha_i, 10000.0);
                ob.hawk_intensity += ob.hawk_terms[i];
            }
            ob.hawk_derivative = 0;
        }
        
        ob.last_hawk_ts = event.timestamp_ns;

        // 2. Kyle's Lambda Accumulation (Iceberg Detection)
        if (ob.signed_vol_sqrt_sum == 0) ob.last_lambda_price = event.price;
        
        ob.signed_vol_sqrt_sum += std::sqrt(event.quantity);
        double price_delta = std::abs(event.price - ob.last_lambda_price);
        ob.abs_price_delta_sum += price_delta;
        ob.last_lambda_price = event.price;

        // Update Lambda every 100 trades (Rolling Window approximation)
        static int trade_count = 0;
        if (++trade_count % 100 == 0) {
            if (ob.signed_vol_sqrt_sum > 0) {
                ob.kyle_lambda = ob.abs_price_delta_sum / ob.signed_vol_sqrt_sum;
                
                // Z-Score and Probability mapping
                // Phase 85: Safe Atomic Deserialization
                double current_mu = load_shm_double(market_shm->lambda_mu);
                double current_sigma = load_shm_double(market_shm->lambda_sigma);
                
                if (current_sigma > 0) {
                    double z = (ob.kyle_lambda - current_mu) / current_sigma;
                    // Absorption is negative Z (high volume, low price impact)
                    double k_steep = 2.0;
                    double gamma_offset = 2.5;
                    ob.lambda_prob = 1.0 / (1.0 + std::exp(-k_steep * (-z - gamma_offset)));
                }
            }
            // Reset window
            ob.signed_vol_sqrt_sum = 0;
            ob.abs_price_delta_sum = 0;
        }

        ob.last_price = event.price;
        ob.last_update_ns = event.timestamp_ns;
        
        if (!config.is_trading_enabled) return;

        // Phase 87: Extreme Margin Defense - Funding Bleed Guard
        // If 50x leverage funding fees have eroded our $141 wallet near the $110 Binance minimum,
        // block all autonomous C++ firing to prevent instant liquidation.
        if (config.wallet_margin_balance > 0.0 && config.wallet_margin_balance < 115.0) {
            static uint64_t last_margin_alert = 0;
            if (event.timestamp_ns - last_margin_alert > 5000000000ULL) { // 5s rate limit
                std::cout << "🛡️ [Risk Block] Margin Bleed Critical! 50x liquidation risk high. Wallet = $" << config.wallet_margin_balance << std::endl;
                last_margin_alert = event.timestamp_ns;
            }
            return;
        }

        // Phase 82: Toxicity-Adjusted Logic
        if (config.toxicity_scalar > 0.85) return;

        // Phase 86: Dynamic Entropy Z-Score Guard (Relative Toxicity)
        // Block execution if the entropy is below the 5th percentile (-1.64) of the 24h rolling baseline
        double current_entropy_z = load_shm_double(market_shm->entropy_z_score);
        if (current_entropy_z != 0.0 && current_entropy_z < -1.64) {
             return;
        }

        // Phase 88: Absolute Entropy Poisoning Guard (Adversarial Defense)
        // Defends against "boiling the frog" where a 12-hour spoofing attack drags the 24-hour baseline down.
        // A hard floor of 0.15 guarantees the bot will never trade in extreme absolute toxicity.
        double raw_entropy = load_shm_double(market_shm->ob_entropy);
        if (raw_entropy != 0.0 && raw_entropy < 0.15) {
             std::cout << "🛡️ [Entropy Block] ABSOLUTE Toxic Spoofer Trap Detected! (H: " << raw_entropy << ")" << std::endl;
             return;
        }

        // Phase 82: Sigmoid Thresholding
        double dynamic_threshold = get_dynamic_obi_threshold();
        
        // Phase 83/84: Stochastic Execution Gate
        // 1. Iceberg Detection: Absorption Probability > 0.8?
        if (ob.lambda_prob > 0.8) {
             std::cout << "🐋 [Iceberg] Institutional Absorption Detected (Prob: " << ob.lambda_prob << ")" << std::endl;
        }

        // 2. Climax Exhaustion: Intensity > 3.5s AND Derivative < 0 (Deceleration)
        if (ob.hawk_intensity > 500.0 && ob.hawk_derivative < 0) {
            std::cout << "⚠️ [Exhaustion] Market Climax Snapping - Intensity: " << ob.hawk_intensity 
                      << " | dL/dt: " << ob.hawk_derivative << std::endl;
        }

        // Example Logic: Trigger if OBI > Threshold
        if (std::abs(ob.obi) > dynamic_threshold) {
            if (event.timestamp_ns - last_global_sniper_alert > 1000000000LL) { // 1s rate limit
                 std::cout << "🎯 [Sniper] Triggered - OBI: " << ob.obi 
                           << " (Threshold: " << dynamic_threshold 
                           << " | Toxicity: " << config.toxicity_scalar << ")" << std::endl;
                 last_global_sniper_alert = event.timestamp_ns;
            }
        }

        // Phase 82: 50ms Structural Pivot (Coinbase Lead-Lag)
        if (event.exchange == 0 || event.exchange == 1) { // Binance or Bybit lagging?
            if (coinbase_ob.last_update_ns > 0) {
                int64_t latency_ns = event.timestamp_ns - coinbase_ob.last_update_ns;
                if (latency_ns > 50000000) { // > 50ms
                    if (event.timestamp_ns - last_global_pivot_alert > 1000000000LL) { // 1s rate limit
                        std::cout << "⚡ [Pivot] Coinbase Leads by " << (latency_ns / 1000000) << "ms" << std::endl;
                        last_global_pivot_alert = event.timestamp_ns;
                    }
                }
            }
        }
    }

public:
    ExecutionEngine(MarketState* m, SPSCQueue* q, ControlState* c) : market_shm(m), event_queue(q), control_shm(c), 
        binance_ob{}, bybit_ob{}, coinbase_ob{} {
        // Initialize local config with safe defaults
        config.is_trading_enabled = 0;
        config.obi_threshold = 0.6;
        config.toxicity_scalar = 0.0;
        config.sentiment_score = 0.0;
    }

    void run() {
        std::cout << "🚀 C++ Execution Core Started (Busy-Polling)..." << std::endl;
        MarketEvent event;
        uint64_t loop_count = 0;
        uint64_t processed_events = 0;
        auto last_heartbeat = std::chrono::system_clock::now();
        
        while (true) {
            // Periodic config sync from Supervisor (every 10k iterations to save cycles)
            if (loop_count % 10000 == 0) {
                ControlData current;
                if (control_shm->read_consistent(current)) {
                    config = current;
                }
            }

            if (event_queue->pop(event)) {
                OrderBookSnapshot* target_ob = nullptr;
                if (event.exchange == 0) target_ob = &binance_ob;
                else if (event.exchange == 1) target_ob = &bybit_ob;
                else if (event.exchange == 2) target_ob = &coinbase_ob;

                if (target_ob) {
                    if (event.event_type == 0) { // Trade
                        handle_trade(*target_ob, event);
                    } else if (event.event_type == 1) { // Depth
                        update_orderbook(*target_ob, event);
                    }
                    processed_events++;
                }
            }

            // Periodic Heartbeat (every 5 seconds)
            auto now = std::chrono::system_clock::now();
            if (std::chrono::duration_cast<std::chrono::seconds>(now - last_heartbeat).count() >= 5) {
                if (processed_events > 0) {
                    std::cout << "💓 [Heartbeat] " << processed_events << " events processed in last 5s" << std::endl;
                    processed_events = 0;
                } else {
                    std::cout << "💤 [Idle] No data from Ingestor yet..." << std::endl;
                }
                last_heartbeat = now;
            }

            loop_count++;
        }
    }
};

int main() {
    // 1. Map Event Queue
    const char* q_shm_name = "vebb_event_q";
    int q_fd = shm_open(q_shm_name, O_RDWR, 0666);
    if (q_fd < 0) {
        std::cerr << "❌ Failed to open Event SHM: " << q_shm_name << std::endl;
        return 1;
    }
    void* q_ptr = mmap(NULL, sizeof(SPSCQueue), PROT_READ | PROT_WRITE, MAP_SHARED, q_fd, 0);
    
    // 2. Map Control State
    const char* c_shm_name = "vebb_control_s";
    int c_fd = shm_open(c_shm_name, O_RDWR, 0666);
    if (c_fd < 0) {
        std::cerr << "❌ Failed to open Control SHM: " << c_shm_name << std::endl;
        return 1;
    }
    void* c_ptr = mmap(NULL, sizeof(ControlState), PROT_READ | PROT_WRITE, MAP_SHARED, c_fd, 0);

    // 3. Map Market State (The source of real-time metrics, not just events)
    const char* m_shm_name = "vebb_shm";
    int m_fd = shm_open(m_shm_name, O_RDWR, 0666);
    if (m_fd < 0) {
        std::cerr << "❌ Failed to open Market SHM: " << m_shm_name << std::endl;
        return 1;
    }
    void* m_ptr = mmap(NULL, sizeof(MarketState), PROT_READ | PROT_WRITE, MAP_SHARED, m_fd, 0);

    if (q_ptr == MAP_FAILED || c_ptr == MAP_FAILED || m_ptr == MAP_FAILED) {
        std::cerr << "❌ mmap failed" << std::endl;
        return 1;
    }

    SPSCQueue* q = static_cast<SPSCQueue*>(q_ptr);
    ControlState* c = static_cast<ControlState*>(c_ptr);
    MarketState* m = static_cast<MarketState*>(m_ptr);
    
    ExecutionEngine engine(m, q, c);
    engine.run();

    return 0;
}
