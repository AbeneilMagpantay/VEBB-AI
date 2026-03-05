# Handoff: VEBB-AI Execution Engine (Phase 116 -> Phase 116.5)

**Context:** The previous session became too long and laggy. This document serves as a full context restore for the new AI assistant.

---

## 1. Current State of the System
We have successfully designed, built, and deployed **Phase 116: Full Real-Time Execution Engine**. The bot now operates on a **dual-path architecture**:

1. **Rust (`vebb-ingest`)**: Operates on a 10ms math loop, processing sub-second microstructure.
2. **Python (`main.py`)**: Polls the Rust Shared Memory (SHM) every 100μs, handles position management, and standard 15m candle closes.
3. **Execution**: Rust fires `EXECUTE_NOW` signals via SHM. Python executes "Probe Positions" (scaling 33%-75% of max size based on confidence) with a tight 0.5% exchange-level stop loss. At the 15m candle close, Python either validates the probe (converting it to a standard position) or invalidates it (liquidating).

---

## 2. Key Features Implemented

*   **100ms DID Tracker**: Tracks consecutive 100ms epochs of absorption (DID < 0.002, intensity > 0.25 BTC/100ms).
*   **Hawkes EWMA Tracker**: Calculates rolling intensity ($\lambda$), a 60-second rolling percentile rank ($P$), and derivative ($d\lambda/dt$) from raw volume.
*   **Integrated OFI**: Depth-weighted OFI across 5 limit order book levels, aggregated across Binance, Bybit, and Coinbase.
*   **Breakout Triggers**: Fires when Hawkes P95+ AND accelerating AND OFI is aligned.
*   **Reversal Triggers**: Fires when absorption is sustained (3s+) AND intensity is decelerating AND cross-exchange delta agrees.

*Recent bug fixes applied:*
*   Fixed a Hawkes cold-start bug by adding a **60-second warmup guard** in `vebb-ingest/src/main.rs`.
*   Fixed `_min_qty` AttributeError in Python and prevented signal loops by moving SHM acknowledgment before order execution.

---

## 3. Files Modified (Core Focus Areas)
To regain exact context, you should scan these files:

*   `vebb-ingest/src/main.rs` *(Specifically the 10ms math loop and `can_signal` execution triggers)*
*   `vebb-ingest/src/shared_mem.rs` *(Look at the `MarketState` struct for Phase 116 fields)*
*   `shm_bridge.py` *(Ctpyes definition of `MarketState` matching Rust)*
*   `main.py` *(Specifically `_check_realtime_execution_signal` and `_reconcile_probe` methods)*

---

## 4. Next Step: Phase 116.5 (Hawkes Calibration)
At the end of the previous session, we drafted a Deep Research Prompt to evaluate our **hardcoded Hawkes parameters**:
*   `hawkes_decay`: 0.05 per 10ms (~138ms half-life)
*   `hawkes_alpha`: 0.3
*   `hawkes_buffer_size`: 6000 samples (60 seconds)

The prompt is located at: `Deep Research Prompts/phase_116.5_hawkes_parameter_calibration.md`

### 🎯 Immediate Mission for New Session:
1. The user will provide the output from running the Phase 116.5 Deep Research prompt through Gemini Advanced/Deep Research.
2. You must analyze the academic consensus provided in that research.
3. You must implement the recommended mathematical adjustments to the Rust `hawkes_decay`, `hawkes_alpha`, and the rolling window buffer to make them empirically sound for high-frequency BTC/USDT Limit Order Book data, ideally establishing a low-CPU dynamic adaptation mechanism.
