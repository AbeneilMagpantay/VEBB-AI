mod shared_mem;

use crate::shared_mem::{MarketState, MarketCoreState, MarketEvent, SPSCQueue, ControlState};
use futures_util::{StreamExt, SinkExt};
use serde_json::Value;
use std::sync::{Arc, Mutex};
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
// use url::Url; (Unused)
use shared_memory::*;
use chrono::{Utc, Timelike};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Phase 78d: Install default crypto provider for rustls 0.23+ 
    rustls::crypto::ring::default_provider().install_default().expect("Failed to install rustls crypto provider");

    println!("🚀 Starting VEBB-AI Nervous System (Rust Ingestor)");
    
    // Create the Shared Memory segment
    let shm_link = "vebb_shm";
    let shm_conf = ShmemConf::new()
        .size(std::mem::size_of::<MarketState>())
        .os_id(shm_link);
    
    // Attempt to create, open if already exists (Fixing E0382 ownership move)
    let shm = match shm_conf.create() {
        Ok(m) => m,
        Err(ShmemError::LinkExists) | Err(ShmemError::MappingIdExists) => {
            ShmemConf::new()
                .size(std::mem::size_of::<MarketState>())
                .os_id(shm_link)
                .open()?
        },
        Err(e) => return Err(format!("SHM Create Error: {:?}", e).into()),
    };

    println!("  [System] Shared Memory established at: {}", shm_link);

    // Initial state (uses the Core struct for fast internal cloning)
    let state = Arc::new(Mutex::new(MarketCoreState::default()));

    // Phase 81: High-Speed Event Queue for C++ Core
    let q_link = "vebb_event_q";
    let q_conf = ShmemConf::new()
        .size(std::mem::size_of::<SPSCQueue>())
        .os_id(q_link);
    
    let q_shm = match q_conf.create() {
        Ok(m) => Arc::new(m),
        Err(ShmemError::LinkExists) | Err(ShmemError::MappingIdExists) => {
            Arc::new(ShmemConf::new()
                .size(std::mem::size_of::<SPSCQueue>())
                .os_id(q_link)
                .open()?)
        },
        Err(e) => return Err(format!("Queue SHM Create Error: {:?}", e).into()),
    };
    println!("  [System] Lock-Free SPSC Bridge established at: {}", q_link);
    
    // Phase 81c: Control Bridge for Python Supervisor
    let c_link = "vebb_control_s";
    let c_conf = ShmemConf::new()
        .size(std::mem::size_of::<ControlState>())
        .os_id(c_link);
    
    let c_shm = match c_conf.create() {
        Ok(m) => Arc::new(m),
        Err(ShmemError::LinkExists) | Err(ShmemError::MappingIdExists) => {
            Arc::new(ShmemConf::new()
                .size(std::mem::size_of::<ControlState>())
                .os_id(c_link)
                .open()?)
        },
        Err(e) => return Err(format!("Control SHM Create Error: {:?}", e).into()),
    };
    println!("  [System] Control Bridge established at: {}", c_link);

    // Get raw pointers as usize to bypass Send trait limitations of Shmem
    let q_ptr_val = q_shm.as_ptr() as usize;
    let shm_ptr_val = shm.as_ptr() as usize;

    // 1. Launch Binance Futures Stream (Trades + Depth)
    let binance_state = Arc::clone(&state);
    let _binance_handle = tokio::spawn(async move {
        let url = "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/btcusdt@depth5@100ms";
        let mut local_count = 0u64;
        loop {
            if let Ok((mut ws_stream, _)) = connect_async(url).await {
                println!("  [Binance] Connected.");
                while let Some(msg) = ws_stream.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<Value>(&text) {
                            let mut s = binance_state.lock().unwrap();
                            let stream = v["stream"].as_str().unwrap_or("");
                            let data = &v["data"];
                            
                            if stream.ends_with("@aggTrade") {
                                let price_str = data["p"].as_str().unwrap_or("0");
                                s.binance_price = price_str.parse().unwrap_or(0.0);
                                let qty_str = data["q"].as_str().unwrap_or("0");
                                let qty: f64 = qty_str.parse().unwrap_or(0.0);
                                s.binance_delta += if data["m"].as_bool().unwrap_or(false) { -qty } else { qty };
                                s.binance_vol += qty;
                                s.sequence_id += 1;
                                local_count += 1;
                                
                                if local_count % 1000 == 0 {
                                    println!("  [Binance] Data Flowing. Last Price: ${}", s.binance_price);
                                }

                                // Phase 81: Push to C++ Bridge
                                let event = MarketEvent {
                                    exchange: 0, // Binance
                                    event_type: 0, // Trade
                                    side: if data["m"].as_bool().unwrap_or(false) { -1 } else { 1 },
                                    price: s.binance_price,
                                    quantity: qty,
                                    timestamp_ns: Utc::now().timestamp_nanos_opt().unwrap_or(0),
                                    ..MarketEvent::default()
                                };
                                let q_ptr = q_ptr_val as *mut SPSCQueue;
                                unsafe { (*q_ptr).push(event); }
                            } else if stream.ends_with("@depth5@100ms") {
                                // Calculate OBI from top 5 levels
                                let mut bid_vol = 0.0;
                                let mut ask_vol = 0.0;
                                let mut bids_ev = [0.0f64; 10];
                                let mut asks_ev = [0.0f64; 10];

                                if let Some(bids) = data["b"].as_array() {
                                    for (i, b) in bids.iter().take(5).enumerate() {
                                        let p: f64 = b[0].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                        let q: f64 = b[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                        bids_ev[i*2] = p;
                                        bids_ev[i*2+1] = q;
                                        bid_vol += q;
                                    }
                                }
                                if let Some(asks) = data["a"].as_array() {
                                    for (i, a) in asks.iter().take(5).enumerate() {
                                        let p: f64 = a[0].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                        let q: f64 = a[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                        asks_ev[i*2] = p;
                                        asks_ev[i*2+1] = q;
                                        ask_vol += q;
                                    }
                                }
                                if bid_vol + ask_vol > 0.0 {
                                    s.binance_nobi = (bid_vol - ask_vol) / (bid_vol + ask_vol);
                                    // Phase 116.2: Store depth snapshot
                                    s.binance_bids = bids_ev;
                                    s.binance_asks = asks_ev;
                                    
                                    // Phase 84/85: Shannon Entropy of Order Book (Chaos Metric)
                                    let total_vol = bid_vol + ask_vol;
                                    let mut entropy = 0.0;
                                    for i in 0..5 {
                                        let q_b = bids_ev[i*2+1];
                                        if q_b > 0.0 {
                                            let p = q_b / total_vol;
                                            entropy -= p * p.log2();
                                        }
                                        let q_a = asks_ev[i*2+1];
                                        if q_a > 0.0 {
                                            let p = q_a / total_vol;
                                            entropy -= p * p.log2();
                                        }
                                    }
                                    // Normalize entropy based on max possible (log2(10) levels)
                                    let final_entropy = entropy / 3.321928;
                                    
                                    // Phase 85: Direct Atomic Store to Shared Memory to prevent tearing
                                    let shm_ptr = shm_ptr_val as *mut MarketState;
                                    unsafe {
                                        (*shm_ptr).ob_entropy.store(final_entropy.to_bits(), std::sync::atomic::Ordering::Release);
                                    }
                                }

                                // Phase 81: Push Depth Event to C++ Bridge
                                let event = MarketEvent {
                                    exchange: 0, // Binance
                                    event_type: 1, // Depth
                                    bids: bids_ev,
                                    asks: asks_ev,
                                    timestamp_ns: Utc::now().timestamp_nanos_opt().unwrap_or(0),
                                    ..MarketEvent::default()
                                };
                                let q_ptr = q_ptr_val as *mut SPSCQueue;
                                unsafe { (*q_ptr).push(event); }
                            }
                        }
                    }
                }
            }
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
        }
    });

    // 2. Launch Bybit Futures Stream (Trades + Depth)
    let bybit_state = Arc::clone(&state);
    let _bybit_handle = tokio::spawn(async move {
        let url = "wss://stream.bybit.com/v5/public/linear";
        let mut local_count = 0u64;
        let mut bids_cache: std::collections::BTreeMap<String, f64> = std::collections::BTreeMap::new();
        let mut asks_cache: std::collections::BTreeMap<String, f64> = std::collections::BTreeMap::new();
        loop {
            if let Ok((mut ws_stream, _)) = connect_async(url).await {
                println!("  [Bybit] Connected.");
                // Subscribe to trades and top 5 orderbook levels
                let sub = r#"{"op": "subscribe", "args": ["publicTrade.BTCUSDT", "orderbook.50.BTCUSDT"]}"#;
                let _ = ws_stream.send(Message::Text(sub.into())).await;
                
                while let Some(msg) = ws_stream.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<Value>(&text) {
                            let topic = v["topic"].as_str().unwrap_or("");
                            if topic == "publicTrade.BTCUSDT" {
                                if let Some(data) = v["data"].as_array() {
                                    let mut s = bybit_state.lock().unwrap();
                                    for trade in data {
                                        let qty: f64 = trade["v"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                        s.bybit_price = trade["p"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                        let side_str = trade["S"].as_str().unwrap_or("");
                                        let is_buy = side_str == "Buy";
                                        s.bybit_delta += if is_buy { qty } else { -qty };
                                        s.bybit_vol += qty;
                                        s.sequence_id += 1;
                                        local_count += 1;
                                        
                                        if local_count % 500 == 0 {
                                            println!("  [Bybit] Data Flowing. Last Price: ${}", s.bybit_price);
                                        }

                                        // Phase 81: Push to C++ Bridge
                                        let event = MarketEvent {
                                            exchange: 1, // Bybit
                                            event_type: 0, // Trade
                                            side: if is_buy { 1 } else { -1 },
                                            price: s.bybit_price,
                                            quantity: qty,
                                            timestamp_ns: Utc::now().timestamp_nanos_opt().unwrap_or(0),
                                            ..MarketEvent::default()
                                        };
                                        let q_ptr = q_ptr_val as *mut SPSCQueue;
                                        unsafe { (*q_ptr).push(event); }
                                    }
                                }
                            } else if topic.starts_with("orderbook") {
                                let msg_type = v["type"].as_str().unwrap_or("");
                                if msg_type == "snapshot" {
                                    bids_cache.clear();
                                    asks_cache.clear();
                                }
                                
                                if let Some(data) = v["data"].as_object() {
                                    if let Some(bids) = data["b"].as_array() {
                                        for b in bids {
                                            let p = b[0].as_str().unwrap_or("0").to_string();
                                            let q: f64 = b[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                            if q == 0.0 { bids_cache.remove(&p); }
                                            else { bids_cache.insert(p, q); }
                                        }
                                    }
                                    if let Some(asks) = data["a"].as_array() {
                                        for a in asks {
                                            let p = a[0].as_str().unwrap_or("0").to_string();
                                            let q: f64 = a[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                            if q == 0.0 { asks_cache.remove(&p); }
                                            else { asks_cache.insert(p, q); }
                                        }
                                    }

                                    // Extract top 5 for SPSC Bridge
                                    let mut bids_ev = [0.0f64; 10];
                                    let mut asks_ev = [0.0f64; 10];
                                    let mut bid_vol = 0.0;
                                    let mut ask_vol = 0.0;

                                    // Bids: Sorted descending (highest first)
                                    let mut bids_vec: Vec<_> = bids_cache.iter().collect();
                                    bids_vec.sort_by(|a, b| b.0.parse::<f64>().unwrap_or(0.0).partial_cmp(&a.0.parse::<f64>().unwrap_or(0.0)).unwrap());
                                    
                                    for (i, (p, q)) in bids_vec.into_iter().take(5).enumerate() {
                                        let price: f64 = p.parse().unwrap_or(0.0);
                                        bids_ev[i*2] = price;
                                        bids_ev[i*2+1] = *q;
                                        bid_vol += *q;
                                    }

                                    // Asks: Sorted ascending (lowest first)
                                    let mut asks_vec: Vec<_> = asks_cache.iter().collect();
                                    asks_vec.sort_by(|a, b| a.0.parse::<f64>().unwrap_or(0.0).partial_cmp(&b.0.parse::<f64>().unwrap_or(0.0)).unwrap());

                                    for (i, (p, q)) in asks_vec.into_iter().take(5).enumerate() {
                                        let price: f64 = p.parse().unwrap_or(0.0);
                                        asks_ev[i*2] = price;
                                        asks_ev[i*2+1] = *q;
                                        ask_vol += *q;
                                    }

                                    if bid_vol + ask_vol > 0.0 {
                                        let mut s = bybit_state.lock().unwrap();
                                        s.bybit_nobi = (bid_vol - ask_vol) / (bid_vol + ask_vol);
                                        // Phase 116.2: Store depth snapshot
                                        s.bybit_bids = bids_ev;
                                        s.bybit_asks = asks_ev;
                                    }

                                    // Phase 81: Push Full Snapshot to C++ Bridge
                                    let event = MarketEvent {
                                        exchange: 1, // Bybit
                                        event_type: 1, // Depth
                                        bids: bids_ev,
                                        asks: asks_ev,
                                        timestamp_ns: Utc::now().timestamp_nanos_opt().unwrap_or(0),
                                        ..MarketEvent::default()
                                    };
                                    let q_ptr = q_ptr_val as *mut SPSCQueue;
                                    unsafe { (*q_ptr).push(event); }
                                }
                            }
                        }
                    }
                }
            }
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
        }
    });

    // 3. Launch Coinbase Spot Stream (Trades + Depth)
    let cb_state = Arc::clone(&state);
    let _cb_handle = tokio::spawn(async move {
        let url = "wss://ws-feed.exchange.coinbase.com";
        let mut local_count = 0u64;
        let mut cb_bids_cache: std::collections::BTreeMap<String, f64> = std::collections::BTreeMap::new();
        let mut cb_asks_cache: std::collections::BTreeMap<String, f64> = std::collections::BTreeMap::new();
        loop {
            if let Ok((mut ws_stream, _)) = connect_async(url).await {
                println!("  [Coinbase] Connected.");
                // Subscribe to ticker, matches (trades), and level2
                let sub = r#"{"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker", "matches", "level2"]}"#;
                let _ = ws_stream.send(Message::Text(sub.into())).await;
                
                while let Some(msg) = ws_stream.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<Value>(&text) {
                            let mut s = cb_state.lock().unwrap();
                            let msg_type = v["type"].as_str().unwrap_or("");
                            
                            if msg_type == "ticker" {
                                s.coinbase_price = v["price"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                s.sequence_id += 1;
                                local_count += 1;
                                
                                if local_count % 100 == 0 {
                                    println!("  [Coinbase] Data Flowing. Price: ${}", s.coinbase_price);
                                }
                            } else if msg_type == "match" {
                                let qty: f64 = v["size"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                let is_buy = v["side"].as_str().unwrap_or("") == "buy";
                                s.coinbase_delta += if is_buy { qty } else { -qty };
                                s.coinbase_vol += qty;
                                s.sequence_id += 1;
                                local_count += 1;

                                // Phase 81: Push Trade to C++ Bridge
                                let event = MarketEvent {
                                    exchange: 2, // Coinbase
                                    event_type: 0, // Trade
                                    side: if is_buy { 1 } else { -1 },
                                    price: s.coinbase_price,
                                    quantity: qty,
                                    timestamp_ns: Utc::now().timestamp_nanos_opt().unwrap_or(0),
                                    ..MarketEvent::default()
                                };
                                let q_ptr = q_ptr_val as *mut SPSCQueue;
                                unsafe { (*q_ptr).push(event); }
                            } else if msg_type == "l2update" || msg_type == "snapshot" {
                                if msg_type == "snapshot" {
                                    cb_bids_cache.clear();
                                    cb_asks_cache.clear();
                                    if let Some(bids) = v.get("bids").and_then(|b| b.as_array()) {
                                        for b in bids {
                                            if let (Some(p), Some(q)) = (b[0].as_str(), b[1].as_str()) {
                                                cb_bids_cache.insert(p.to_string(), q.parse().unwrap_or(0.0));
                                            }
                                        }
                                    }
                                    if let Some(asks) = v.get("asks").and_then(|a| a.as_array()) {
                                        for a in asks {
                                            if let (Some(p), Some(q)) = (a[0].as_str(), a[1].as_str()) {
                                                cb_asks_cache.insert(p.to_string(), q.parse().unwrap_or(0.0));
                                            }
                                        }
                                    }
                                } else if msg_type == "l2update" {
                                    if let Some(changes) = v.get("changes").and_then(|c| c.as_array()) {
                                        for change in changes {
                                            if let Some(arr) = change.as_array() {
                                                let side = arr[0].as_str().unwrap_or("");
                                                let p = arr[1].as_str().unwrap_or("0").to_string();
                                                let q: f64 = arr[2].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                                                if side == "buy" {
                                                    if q == 0.0 { cb_bids_cache.remove(&p); }
                                                    else { cb_bids_cache.insert(p, q); }
                                                } else {
                                                    if q == 0.0 { cb_asks_cache.remove(&p); }
                                                    else { cb_asks_cache.insert(p, q); }
                                                }
                                            }
                                        }
                                    }
                                }

                                // Extract top 5 for SPSC Bridge
                                let mut bids_ev = [0.0f64; 10];
                                let mut asks_ev = [0.0f64; 10];
                                let mut bid_vol = 0.0;
                                let mut ask_vol = 0.0;

                                // Bids: Sorted descending
                                let mut bids_vec: Vec<_> = cb_bids_cache.iter().collect();
                                bids_vec.sort_by(|a, b| b.0.parse::<f64>().unwrap_or(0.0).partial_cmp(&a.0.parse::<f64>().unwrap_or(0.0)).unwrap());
                                for (i, (p, q)) in bids_vec.into_iter().take(5).enumerate() {
                                    let price: f64 = p.parse().unwrap_or(0.0);
                                    bids_ev[i*2] = price;
                                    bids_ev[i*2+1] = *q;
                                    bid_vol += *q;
                                }

                                // Asks: Sorted ascending
                                let mut asks_vec: Vec<_> = cb_asks_cache.iter().collect();
                                asks_vec.sort_by(|a, b| a.0.parse::<f64>().unwrap_or(0.0).partial_cmp(&b.0.parse::<f64>().unwrap_or(0.0)).unwrap());
                                for (i, (p, q)) in asks_vec.into_iter().take(5).enumerate() {
                                    let price: f64 = p.parse().unwrap_or(0.0);
                                    asks_ev[i*2] = price;
                                    asks_ev[i*2+1] = *q;
                                    ask_vol += *q;
                                }

                                if bid_vol + ask_vol > 0.0 {
                                    s.coinbase_nobi = (bid_vol - ask_vol) / (bid_vol + ask_vol);
                                    // Phase 116.2: Store depth snapshot
                                    s.coinbase_bids = bids_ev;
                                    s.coinbase_asks = asks_ev;
                                }

                                // Phase 81: Push Full Snapshot to C++ Bridge
                                let event = MarketEvent {
                                    exchange: 2, // Coinbase
                                    event_type: 1, // Depth
                                    bids: bids_ev,
                                    asks: asks_ev,
                                    timestamp_ns: Utc::now().timestamp_nanos_opt().unwrap_or(0),
                                    ..MarketEvent::default()
                                };
                                let q_ptr = q_ptr_val as *mut SPSCQueue;
                                unsafe { (*q_ptr).push(event); }
                            }
                        }
                    }
                }
            }
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
        }
    });

    println!("💎 All Exchange Hydras active. Normalizing to Shared Memory...");
    
    // Rolling buffers for Z-Scores
    let mut spread_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(6000);
    let mut binance_nobi_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(6000);
    let mut bybit_nobi_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(6000);
    let mut cb_nobi_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(6000);
    
    let mut last_cb_delta = 0.0;
    let mut last_fut_delta = 0.0;
    let mut di_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(6000);
    
    // Volume-Gated Sampling Tracking (Phase 79b)
    let mut last_vol_theta = 0.0;
    let vol_step_theta = 5.0; // Sample spread every 5.0 BTC of global volume
    let mut last_theta_ts = Utc::now(); // Phase 80a: Entropy Watchdog

    // Phase 80c: Price Innovation buffers for Dynamic Leadership
    let mut bn_price_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(100);
    let mut byb_price_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(100);
    let mut cb_price_buffer: std::collections::VecDeque<f64> = std::collections::VecDeque::with_capacity(100);
    
    // Phase 86: Kyle's Lambda Baselines (True 24h Baseline & Bias Correction)
    let mut lambda_count: u64 = 0;
    
    // High-precision simulated accumulators (Double precision Kahan-like tracking to prevent f64 truncation)
    let mut lambda_mean: f64 = 0.0;
    let mut lambda_m2: f64 = 0.0;
    let mut last_lambda_price = 0.0;
    let mut lambda_vol_sum = 0.0;
    
    // Phase 101 Step 1: Time-Bucketed Down-sampling for 24h EWMV
    // Update exactly once per second (86,400 updates per 24h)
    // alpha = 1 / 86400 = 1.1574e-5
    let decay_factor: f64 = 0.9999884259;
    let update_weight: f64 = 1.0 - decay_factor;
    let mut last_baseline_update_ts = Utc::now();
    
    // Phase 86: Entropy Z-Score Stateful Accumulators
    let mut entropy_count: u64 = 0;
    let mut entropy_mean: f64 = 0.0;
    let mut entropy_m2: f64 = 0.0;
    
    // Phase 102: Dynamic Global Unity Deviation Accumulators
    let mut global_delta_count: u64 = 0;
    let mut global_delta_mean: f64 = 0.0;
    let mut global_delta_m2: f64 = 0.0;
    let mut global_vol_sum: f64 = 0.0;
    
    // Phase 116A → 116.1: Rolling 100ms DID Absorption Tracker (upgraded from 1s)
    let mut abs_last_global_delta: f64 = 0.0;
    let mut abs_last_global_vol: f64 = 0.0;
    let mut last_abs_check_ts = Utc::now();
    let mut absorption_streak: u32 = 0;
    let mut absorption_anchor_price: f64 = 0.0;
    
    // Phase 116.5: Calibrated Hawkes EWMA Intensity Tracker
    // Based on: El Karmi (2025) arXiv:2510.08085, Bacry et al. (2015), Da Fonseca & Zaatour (2014)
    let mut hawkes_intensity: f64 = 0.0;
    let mut hawkes_prev_intensity: f64 = 0.0;
    let hawkes_decay: f64 = 0.023;    // β_d per 10ms → 300ms half-life (empirical BTC/USDT optimum)
    let mut hawkes_alpha: f64 = 0.0;  // Adaptive — computed from rolling moments
    let target_branching: f64 = 0.80; // Target n* = 0.80 (subcritical, per El Karmi 2025)
    let mut last_hawkes_vol: f64 = 0.0;
    
    // Phase 116.5: O(1) rolling moment trackers (5-min half-life EWMA)
    let moment_decay: f64 = 0.00002;  // ~5.77 min half-life at 10ms ticks
    let mut vol_mean: f64 = 0.0;
    let mut vol_var: f64 = 0.0;
    let mut hawkes_warmup_ticks: u64 = 0;
    let hawkes_warmup_min: u64 = 6000; // 60s minimum before firing signals
    
    // Phase 116.5: O(1) CUSUM anomaly detection (replaces O(N) percentile buffer)
    let mut cusum_score: f64 = 0.0;
    
    // Phase 116.5: Execution signal cooldown — 6.5s (t_95 relaxation time)
    let mut last_exec_signal_ts = Utc::now();
    let exec_cooldown_ms: i64 = 6500; // Derived from β_eff = β(1-n) = 0.464/s → t_95 ≈ 6.45s
    
    // Phase 116.3: 1-second delta snapshots for cross-exchange consensus
    let mut last_consensus_ts = Utc::now();
    let mut bn_delta_1s: f64 = 0.0;
    let mut by_delta_1s: f64 = 0.0;
    let mut cb_delta_1s: f64 = 0.0;
    let mut prev_bn_delta: f64 = 0.0;
    let mut prev_by_delta: f64 = 0.0;
    let mut prev_cb_delta: f64 = 0.0;
    
    // Math & Flush Loop: Every 10ms
    loop {
        {
            let mut s = state.lock().unwrap();
            
            // Phase 80c: Variance-Based Dynamic Leadership (SOTA Alignment)
            rotate_buffer(&mut bn_price_buffer, s.binance_price, 100);
            rotate_buffer(&mut byb_price_buffer, s.bybit_price, 100);
            rotate_buffer(&mut cb_price_buffer, s.coinbase_price, 100);

            if bn_price_buffer.len() >= 100 {
                let var_bn = calculate_variance(&bn_price_buffer);
                let var_byb = calculate_variance(&byb_price_buffer);
                let var_cb = calculate_variance(&cb_price_buffer);
                
                let total_var = var_bn + var_byb + var_cb;
                if total_var > 0.0 {
                    // Update weights based on price innovation (variance)
                    s.binance_weight = var_bn / total_var;
                    s.bybit_weight = var_byb / total_var;
                    s.coinbase_weight = var_cb / total_var;
                }
            } else {
                // Initial fallback to Phase 79 Session weights if buffers not full
                let now_utc = Utc::now();
                let hour_utc = now_utc.hour();
                let min_utc = now_utc.minute();
                let is_nyse = (hour_utc > 14 || (hour_utc == 14 && min_utc >= 30)) && (hour_utc < 21);
                
                if is_nyse {
                    s.binance_weight = 0.30;
                    s.coinbase_weight = 0.50;
                    s.bybit_weight = 0.20;
                } else {
                    s.binance_weight = 0.45;
                    s.bybit_weight = 0.35;
                    s.coinbase_weight = 0.20;
                }
            }

            s.global_delta = (s.binance_weight * s.binance_delta) + 
                             (s.coinbase_weight * s.coinbase_delta) + 
                             (s.bybit_weight * s.bybit_delta);
            s.global_raw_delta = s.binance_delta + s.bybit_delta + s.coinbase_delta;
            s.global_raw_volume = s.binance_vol + s.bybit_vol + s.coinbase_vol;
            
            // 2. Lead-Lag Theta (Price Spread Z-Score)
            // 2. Lead-Lag Theta (Price Spread Z-Score) with Entropy Protection Watchdog
            if s.coinbase_price > 0.0 && s.binance_price > 0.0 {
                let now = Utc::now();
                let time_diff_ms = (now - last_theta_ts).num_milliseconds();
                let vol_diff = s.global_raw_volume - last_vol_theta;
                
                // Trigger on 5.0 BTC volume OR 2s temporal timeout (Phase 80a)
                if vol_diff >= vol_step_theta || time_diff_ms >= 2000 {
                    let current_spread = (s.coinbase_price / s.binance_price).ln();
                    rotate_buffer(&mut spread_buffer, current_spread, 6000);
                    last_vol_theta = s.global_raw_volume;
                    last_theta_ts = now;
                }
                
                if spread_buffer.len() > 100 {
                    s.lead_lag_theta = calculate_z_score(&spread_buffer);
                }
            }

            // 3. Global OBI (GOBI) with Z-Score Standardization
            rotate_buffer(&mut binance_nobi_buffer, s.binance_nobi, 6000);
            rotate_buffer(&mut bybit_nobi_buffer, s.bybit_nobi, 6000);
            rotate_buffer(&mut cb_nobi_buffer, s.coinbase_nobi, 6000);
            
            if binance_nobi_buffer.len() > 100 {
                let z_bin = calculate_z_score(&binance_nobi_buffer);
                let z_byb = calculate_z_score(&bybit_nobi_buffer);
                let z_cb = calculate_z_score(&cb_nobi_buffer);
                // Liquidity weighting: 50/30/20
                s.global_obi = (0.5 * z_bin) + (0.3 * z_cb) + (0.2 * z_byb);
            }

            // 4. Divergence Index (DI): Spot vs Futures
            let cb_delta_diff = s.coinbase_delta - last_cb_delta;
            let fut_delta_diff = (s.binance_delta + s.bybit_delta) - last_fut_delta;
            
            // Simplified DI for SHM: Relative difference in delta momentum
            let di_raw = cb_delta_diff - fut_delta_diff;
            rotate_buffer(&mut di_buffer, di_raw, 6000);
            if di_buffer.len() > 100 {
                s.global_di = calculate_z_score(&di_buffer);
            }
            
            last_cb_delta = s.coinbase_delta;
            last_fut_delta = s.binance_delta + s.bybit_delta;
            
            // Phase 101 Step 1: Time-Bucketed Down-sampling for EWMV
            let mut current_lambda_mu = 0.0;
            let mut current_lambda_sigma = 0.0;
            let mut current_entropy_z = 0.0;
            
            let now = Utc::now();
            if (now - last_baseline_update_ts).num_milliseconds() >= 1000 {
                if s.binance_price > 0.0 && last_lambda_price > 0.0 {
                    let price_diff = s.binance_price - last_lambda_price;
                    let vol_diff = (s.binance_vol + s.bybit_vol + s.coinbase_vol) - lambda_vol_sum;
                    
                    if vol_diff > 0.0 {
                        let lambda = price_diff.abs() / vol_diff.sqrt();
                        
                        lambda_count += 1;
                        let diff = lambda - lambda_mean;
                        lambda_mean += update_weight * diff;
                        let diff2 = lambda - lambda_mean;
                        lambda_m2 = decay_factor * lambda_m2 + update_weight * diff * diff2;
                    }
                }
                last_lambda_price = s.binance_price;
                lambda_vol_sum = s.binance_vol + s.bybit_vol + s.coinbase_vol;
                
                let raw_h = f64::from_bits(unsafe { (*(shm.as_ptr() as *const MarketState)).ob_entropy.load(std::sync::atomic::Ordering::Acquire) });
                if raw_h > 0.0 {
                    entropy_count += 1;
                    let diff = raw_h - entropy_mean;
                    entropy_mean += update_weight * diff;
                    let diff2 = raw_h - entropy_mean;
                    entropy_m2 = decay_factor * entropy_m2 + update_weight * diff * diff2;
                }
                
                
                last_baseline_update_ts = now;
            }
            
            // Phase 116.1: 100ms DID Absorption Check (upgraded from 1s)
            let now_abs = Utc::now();
            if (now_abs - last_abs_check_ts).num_milliseconds() >= 100 {
                let current_raw_delta = s.binance_delta + s.bybit_delta + s.coinbase_delta;
                let current_raw_vol = s.binance_vol + s.bybit_vol + s.coinbase_vol;
                
                let delta_100ms = (current_raw_delta - abs_last_global_delta).abs();
                let vol_100ms = current_raw_vol - abs_last_global_vol;
                
                let did_100ms = if vol_100ms > 0.0 { delta_100ms / vol_100ms } else { 1.0 };
                
                // Absorption: DID < 0.002 at intensity > 0.25 BTC/100ms (= 2.5 BTC/sec)
                if did_100ms < 0.002 && vol_100ms > 0.25 {
                    let current_price = s.binance_price;
                    if absorption_anchor_price > 0.0
                       && ((current_price - absorption_anchor_price).abs() / absorption_anchor_price) < 0.005 {
                        absorption_streak += 1;
                    } else {
                        absorption_streak = 1;
                        absorption_anchor_price = current_price;
                    }
                } else {
                    absorption_streak = 0;
                    absorption_anchor_price = 0.0;
                }
                
                abs_last_global_delta = current_raw_delta;
                abs_last_global_vol = current_raw_vol;
                last_abs_check_ts = now_abs;
            }
            
            // Phase 116.5: Calibrated Hawkes EWMA Intensity Tracker (every 10ms)
            let current_total_vol = s.binance_vol + s.bybit_vol + s.coinbase_vol;
            let raw_vol_tick = (current_total_vol - last_hawkes_vol).max(0.0);
            last_hawkes_vol = current_total_vol;
            
            // Step 1: Log-transform volume to enforce stationarity (Bacry et al. 2015)
            // Prevents heavy-tailed crypto volumes from causing supercritical explosion
            let bounded_vol = f64::ln(1.0 + raw_vol_tick);
            
            // Step 2: Update O(1) rolling moments (5-min EWMA)
            vol_mean = vol_mean * (1.0 - moment_decay) + bounded_vol * moment_decay;
            let vol_diff = bounded_vol - vol_mean;
            vol_var = vol_var * (1.0 - moment_decay) + (vol_diff * vol_diff) * moment_decay;
            hawkes_warmup_ticks += 1;
            
            // Step 3: Adaptive alpha — scale inversely to rolling mean volume
            // Ensures branching ratio n* = (α × E[V]) / β stays at target 0.80
            hawkes_alpha = (target_branching * hawkes_decay) / f64::max(vol_mean, 1e-6);
            
            // Step 4: Update Hawkes intensity with calibrated parameters
            hawkes_prev_intensity = hawkes_intensity;
            hawkes_intensity = hawkes_intensity * (1.0 - hawkes_decay) + hawkes_alpha * bounded_vol;
            
            // Step 5: O(1) CUSUM anomaly detection (replaces O(N) percentile buffer)
            // Drift = noise floor before CUSUM accumulates
            let cusum_drift = 0.5 * vol_mean;
            cusum_score = f64::max(0.0, cusum_score + hawkes_intensity - vol_mean - cusum_drift);
            
            // Step 6: Compute trigger threshold from rolling volatility
            let cusum_threshold = 3.0 * f64::max(vol_var.sqrt(), 1e-9);
            let breakout_detected = cusum_score > cusum_threshold;
            
            // Reset CUSUM on trigger to prevent re-firing (save peak for confidence)
            let cusum_peak = cusum_score; // Capture before reset
            if breakout_detected {
                cusum_score = 0.0;
            }
            
            // Derivative: rate of change per second (intensity units/sec)
            let hawkes_deriv = (hawkes_intensity - hawkes_prev_intensity) / 0.01; // 10ms = 0.01s
            
            // Phase 116.3: Track 1-second delta diffs per exchange for consensus
            let now_consensus = Utc::now();
            if (now_consensus - last_consensus_ts).num_milliseconds() >= 1000 {
                bn_delta_1s = s.binance_delta - prev_bn_delta;
                by_delta_1s = s.bybit_delta - prev_by_delta;
                cb_delta_1s = s.coinbase_delta - prev_cb_delta;
                prev_bn_delta = s.binance_delta;
                prev_by_delta = s.bybit_delta;
                prev_cb_delta = s.coinbase_delta;
                last_consensus_ts = now_consensus;
            }
            
            // Phase 116.2: Integrated Depth-Weighted OFI
            let depth_weights: [f64; 5] = [1.0, 0.8, 0.6, 0.4, 0.2];
            let compute_ofi = |bids: &[f64; 10], asks: &[f64; 10]| -> f64 {
                let mut wb = 0.0;
                let mut wa = 0.0;
                for i in 0..5 {
                    wb += depth_weights[i] * bids[i*2+1];
                    wa += depth_weights[i] * asks[i*2+1];
                }
                let total = wb + wa;
                if total > 0.0 { (wb - wa) / total } else { 0.0 }
            };
            let int_ofi = s.binance_weight * compute_ofi(&s.binance_bids, &s.binance_asks)
                        + s.bybit_weight * compute_ofi(&s.bybit_bids, &s.bybit_asks)
                        + s.coinbase_weight * compute_ofi(&s.coinbase_bids, &s.coinbase_asks);
            
            // Phase 102: EWMV Step for Dynamic Global Unity Deviation
            let current_global_delta = s.binance_delta + s.bybit_delta + s.coinbase_delta;
            let current_global_vol = s.binance_vol + s.bybit_vol + s.coinbase_vol;
            let vol_diff_10ms = current_global_vol - global_vol_sum;
            
            if vol_diff_10ms > 0.0 {
                global_delta_count += 1;
                let diff = current_global_delta - global_delta_mean;
                global_delta_mean += update_weight * diff;
                let diff2 = current_global_delta - global_delta_mean;
                global_delta_m2 = decay_factor * global_delta_m2 + update_weight * diff * diff2;
            }
            global_vol_sum = current_global_vol;
            
            // Calculate instantaneous states from accumulated means/variances (Phase 86 bias correction)
            if lambda_count > 0 {
                let bias_corr = 1.0 - decay_factor.powf(lambda_count as f64);
                current_lambda_mu = lambda_mean / bias_corr;
                let current_var = lambda_m2 / bias_corr;
                if lambda_count > 1 && current_var > 0.0 {
                    current_lambda_sigma = current_var.sqrt();
                }
            }
            
            let raw_h2 = f64::from_bits(unsafe { (*(shm.as_ptr() as *const MarketState)).ob_entropy.load(std::sync::atomic::Ordering::Acquire) });
            if raw_h2 > 0.0 && entropy_count > 0 {
                let bias_corr = 1.0 - decay_factor.powf(entropy_count as f64);
                let corrected_entropy_mu = entropy_mean / bias_corr;
                let corrected_entropy_var = entropy_m2 / bias_corr;
                if entropy_count > 1 && corrected_entropy_var > 0.0 {
                    let current_entropy_sigma = corrected_entropy_var.sqrt();
                    current_entropy_z = (raw_h2 - corrected_entropy_mu) / current_entropy_sigma;
                }
            }
            
            let ptr = shm.as_ptr() as *mut MarketState;
            unsafe { 
                // Copy the standard layout fields natively
                (*ptr).core = *s; 
                
                // Atomically update the stochastic Phase 85/86 variables
                if current_lambda_sigma > 0.0 {
                    (*ptr).lambda_mu.store(current_lambda_mu.to_bits(), std::sync::atomic::Ordering::Release);
                    (*ptr).lambda_sigma.store(current_lambda_sigma.to_bits(), std::sync::atomic::Ordering::Release);
                }
                if current_entropy_z != 0.0 {
                     (*ptr).entropy_z_score.store(current_entropy_z.to_bits(), std::sync::atomic::Ordering::Release);
                }
                
                // Phase 102: Synthesize Bounds via Fractional Power-Law and GOBI Normalization
                let c_ptr = c_shm.as_ptr() as *const ControlState;
                let z_base = (*c_ptr).z_base;
                let gamma = (*c_ptr).scaling_gamma;
                let kappa = (*c_ptr).gobi_kappa;
                
                let mut current_delta_mu = 0.0;
                let mut current_delta_sigma = 0.0;
                
                if global_delta_count > 0 {
                    let bias_corr = 1.0 - decay_factor.powf(global_delta_count as f64);
                    current_delta_mu = global_delta_mean / bias_corr;
                    let current_var = global_delta_m2 / bias_corr;
                    if global_delta_count > 1 && current_var > 0.0 {
                        current_delta_sigma = current_var.sqrt();
                    }
                }
                
                // Target volume baseline representation (fallback if lambda fails)
                let vol_24h_base = if lambda_vol_sum > 0.0 { lambda_vol_sum } else { 1.0 }; 
                let vol_anomaly_ratio = (current_global_vol / vol_24h_base).min(1.0); // Don't scale up infinitely
                
                // Fractional Power-Law Volumetric Scaling (Omega)
                let omega = vol_anomaly_ratio.powf(gamma).max(1.0); // Minimum 1.0 multiplier
                
                // GOBI Normalization (Phi)
                let gobi = s.global_obi;
                let phi_short = 1.0 + (kappa * gobi);
                let phi_long = 1.0 - (kappa * gobi);
                
                let tau_upper = current_delta_mu + (z_base * current_delta_sigma * omega * phi_short);
                let tau_lower = current_delta_mu - (z_base * current_delta_sigma * omega * phi_long);
                
                // Direct Atomic Store
                (*ptr).dynamic_tau_upper.store(tau_upper.to_bits(), std::sync::atomic::Ordering::Release);
                (*ptr).dynamic_tau_lower.store(tau_lower.to_bits(), std::sync::atomic::Ordering::Release);
                
                // Phase 116A/116.1: Write absorption streak
                (*ptr).absorption_streak.store(absorption_streak as u64, std::sync::atomic::Ordering::Release);
                
                // Phase 116.5: Write Hawkes metrics + CUSUM
                (*ptr).hawkes_intensity.store(hawkes_intensity.to_bits(), std::sync::atomic::Ordering::Release);
                (*ptr).hawkes_percentile.store(cusum_score.to_bits(), std::sync::atomic::Ordering::Release); // Repurposed: now stores CUSUM score
                (*ptr).hawkes_derivative.store(hawkes_deriv.to_bits(), std::sync::atomic::Ordering::Release);
                
                // Phase 116.2: Write Integrated OFI
                (*ptr).integrated_ofi.store(int_ofi.to_bits(), std::sync::atomic::Ordering::Release);
                
                // Phase 116.3: Write heartbeat
                (*ptr).heartbeat_ts.store(Utc::now().timestamp_nanos_opt().unwrap_or(0) as u64, std::sync::atomic::Ordering::Release);
                
                // Phase 116.3: Evaluate Execution Triggers
                let ack = (*ptr).exec_signal_ack.load(std::sync::atomic::Ordering::Acquire);
                let pending = (*ptr).exec_signal_type.load(std::sync::atomic::Ordering::Acquire);
                let now_signal = Utc::now();
                let cooldown_ok = (now_signal - last_exec_signal_ts).num_milliseconds() >= exec_cooldown_ms;
                let can_signal = cooldown_ok && hawkes_warmup_ticks >= hawkes_warmup_min && (pending == 0 || ack != 0);
                
                if can_signal {
                    // BREAKOUT trigger: CUSUM breach AND accelerating AND OFI aligned
                    let breakout_long = breakout_detected && hawkes_deriv > 0.0 && int_ofi > 0.15 && absorption_streak == 0;
                    let breakout_short = breakout_detected && hawkes_deriv > 0.0 && int_ofi < -0.15 && absorption_streak == 0;
                    
                    // REVERSAL trigger: Sustained absorption + Hawkes decelerating + cross-exchange consensus
                    let all_long = bn_delta_1s > 0.0 && by_delta_1s > 0.0 && cb_delta_1s > 0.0;
                    let all_short = bn_delta_1s < 0.0 && by_delta_1s < 0.0 && cb_delta_1s < 0.0;
                    let reversal_long = absorption_streak >= 30 && hawkes_deriv < 0.0 && all_long;
                    let reversal_short = absorption_streak >= 30 && hawkes_deriv < 0.0 && all_short;
                    
                    if breakout_long || breakout_short {
                        let dir: u64 = if breakout_long { 1 } else { 2 };
                        // Confidence = how far CUSUM exceeded threshold (capped at 1.0)
                        let conf = if cusum_threshold > 0.0 { (cusum_peak / cusum_threshold).min(1.0) } else { 0.5 };
                        (*ptr).exec_signal_type.store(1, std::sync::atomic::Ordering::Release); // BREAKOUT
                        (*ptr).exec_signal_dir.store(dir, std::sync::atomic::Ordering::Release);
                        (*ptr).exec_signal_confidence.store(conf.to_bits(), std::sync::atomic::Ordering::Release);
                        (*ptr).exec_signal_ts.store(now_signal.timestamp_nanos_opt().unwrap_or(0) as u64, std::sync::atomic::Ordering::Release);
                        (*ptr).exec_signal_ack.store(0, std::sync::atomic::Ordering::Release);
                        last_exec_signal_ts = now_signal;
                        let dir_str = if breakout_long { "LONG" } else { "SHORT" };
                        println!("  [🎯] EXECUTE_NOW: BREAKOUT {} (CUSUM={:.3}/{:.3}, OFI={:.3}, α={:.4}, conf={:.2})", dir_str, cusum_score, cusum_threshold, int_ofi, hawkes_alpha, conf);
                    } else if reversal_long || reversal_short {
                        let dir: u64 = if reversal_long { 1 } else { 2 };
                        let conf = (absorption_streak as f64 / 100.0).min(1.0); // More absorption = higher confidence
                        (*ptr).exec_signal_type.store(2, std::sync::atomic::Ordering::Release); // REVERSAL
                        (*ptr).exec_signal_dir.store(dir, std::sync::atomic::Ordering::Release);
                        (*ptr).exec_signal_confidence.store(conf.to_bits(), std::sync::atomic::Ordering::Release);
                        (*ptr).exec_signal_ts.store(now_signal.timestamp_nanos_opt().unwrap_or(0) as u64, std::sync::atomic::Ordering::Release);
                        (*ptr).exec_signal_ack.store(0, std::sync::atomic::Ordering::Release);
                        last_exec_signal_ts = now_signal;
                        let dir_str = if reversal_long { "LONG" } else { "SHORT" };
                        println!("  [🎯] EXECUTE_NOW: REVERSAL {} (Absorption {}x100ms, dλ/dt={:.2}, conf={:.2})", dir_str, absorption_streak, hawkes_deriv, conf);
                    }
                }
            }
        }
        tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
    }
    
    #[allow(unreachable_code)]
    Ok(())
}

fn rotate_buffer(buffer: &mut std::collections::VecDeque<f64>, val: f64, max: usize) {
    buffer.push_back(val);
    if buffer.len() > max { buffer.pop_front(); }
}

fn calculate_z_score(buffer: &std::collections::VecDeque<f64>) -> f64 {
    let mean: f64 = buffer.iter().sum::<f64>() / buffer.len() as f64;
    let variance: f64 = buffer.iter().map(|&x| (x - mean).powi(2)).sum::<f64>() / buffer.len() as f64;
    let std_dev = variance.sqrt();
    if std_dev > 0.0 { (buffer.back().unwrap_or(&0.0) - mean) / std_dev } else { 0.0 }
}

fn calculate_variance(buffer: &std::collections::VecDeque<f64>) -> f64 {
    if buffer.len() < 2 { return 0.0; }
    let mean: f64 = buffer.iter().sum::<f64>() / buffer.len() as f64;
    buffer.iter().map(|&x| (x - mean).powi(2)).sum::<f64>() / buffer.len() as f64
}
