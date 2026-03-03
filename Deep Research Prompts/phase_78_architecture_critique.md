# PROMPT: VEBB-AI PHASE 78 - GLOBAL UNITY ARCHITECTURE CRITIQUE & OPTIMIZATION

## CONTEXT
You are a Lead Quantitative Research Engineer specializing in high-frequency trading (HFT) and cross-exchange arbitrage. You are reviewing a cryptocurrency trading bot ("VEBB-AI") that has recently transitioned from a single-exchange (Binance) observer to a multi-exchange "Nervous System" architecture (Phase 78).

## CURRENT STACK
- **Ingestion**: Rust-based engine (tokio/tungstenite) handling Binance Futures, Bybit Futures, and Coinbase Spot (BTC-USD).
- **Bridge**: Zero-copy Shared Memory (RAM) using `mmap` for sub-microsecond state transfer between Rust and Python.
- **Orchestration**: Python 3.12 with `uvloop` for ultra-low latency event handling.
- **Cognition**: Gemini 2.5 Flash as the final decision arbiter, injected with global microstructure metrics.

## CORE ALPHA SIGNALS
1. **Global Delta Fusion**: A synthetic volume-weighted aggregate of aggression across 3 exchanges.
2. **Lead-Lag Theta**: A rolling Z-score of the log-spread between Coinbase Spot and Binance Futures (Detecting Institutional Lead).
3. **Divergence Index (DI)**: Mathematical delta between Spot (CB) flow and Futures (BN+BY) flow to detect "Liquidation Hunts."
4. **GOBI (Global OBI)**: Standardized Z-Score of the top-5 depth levels of all 3 order books simultaneously.
5. **Alpha Hysteresis**: A 1.0-point "Dead Zone" filter on directional signals to prevent high-frequency jitter/flicker.

## THE MISSION
Analyze this architecture and provide a deep research report covering the following:

### 1. Latency & Race Conditions
- Evaluate the risk of "Adverse Selection" where the bot enters a 15m breakout on Binance but is "late" compared to the Coinbase lead signal.
- How can we optimize the `mmap` transition further? Should we move the HMM logic or signal calculations entirely into the Rust layer?

### 2. Signal Robustness (Hysteresis & Decay)
- Critique the currently implemented 1.0-point Hysteresis filter. Does it introduce too much "Signal Lag" for a 15m breakout strategy? 
- Suggest a dynamic decay function for the Lead-Lag Theta. Should it be time-weighted or volume-weighted?

### 3. The "Institutional Bias" Weighting
- Currently, the weighting is: Binance (50%), Coinbase (30%), Bybit (20%). 
- Research the "Information Leadership" between these three entities. Should the Coinbase weight dynamiclly increase during US Market Hours (NYSE Open)?

### 4. Expansion Path (Phase 79)
- Suggest 2 novel multi-exchange metrics that could be added to this Nervous System.
- Examples to explore: "Cross-Exchange Liquidation Contagion" or "Global Order Book Skewness (GOS)."

## OUTPUT REQUIREMENTS
- Provide your analysis in a structured technical report.
- Include mathematical formulas (LaTeX) for any proposed signal improvements.
- Identify the "Weakest Link" in the current Phase 78 chain.
