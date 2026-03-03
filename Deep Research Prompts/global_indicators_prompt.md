# Deep Research Prompt: Institutional Multi-Exchange Aggregation & Lead-Lag Alpha

## Objective
Analyze the mathematical and architectural validity of a "Global Nervous System" for a BTC-USDT HFT/Scalping bot. The system aggregates real-time WebSocket data from Binance Futures, Bybit Linear, and Coinbase Advanced Trade into a single POSIX Shared Memory bridge for a Python execution engine.

---

## Core Research Areas

### 1. Multi-Exchange Delta Weighting (Global CVD)
*   **Problem**: Binance has ~10x the retail volume of Coinbase, but Coinbase is often where institutional "Smart Money" enters. A simple 1:1 sum of Delta is likely suboptimal.
*   **Research Question**: What are the industry-standard formulas for "Weighted Global Delta"? Should it be weighted by:
    *   Exchange Market Share (Volume-Weighted)?
    *   Tick Size / Spread (Liquidity-Weighted)?
    *   Predictive Power (Leading Indicator weighting)?
*   **Outcome**: Provide a specific formula for `global_delta = (w1 * B_delta) + (w2 * BY_delta) + (w3 * CB_delta)`.

### 2. Lead-Lag Signal Detection (The "Theta" Metric)
*   **Problem**: Coinbase (Spot) often moves seconds before Binance (Futures) during institutional accumulation.
*   **Research Question**: Analyze the most robust ways to calculate a "Lead-Lag Theta" signal. Compare:
    *   **Price Divergence Z-Score**: Measuring the spread between $P_{Coinbase}$ and $P_{Binance}$ relative to its 60-second rolling mean.
    *   **Cross-Correlation Coefficient**: Real-time sliding window correlation between exchange price series.
    *   **Order Book Imbalance (OBI) Divergence**: Detecting when Coinbase OBI is skewed heavily bullish while Binance is neutral.
*   **Outcome**: Which method provides the highest signal-to-noise ratio for 1-minute to 5-minute scalping?

### 3. Institutional Guard (Coinbase Absorption)
*   **Problem**: During "Crises" or "Flash Crashes," Binance often goes into a feedback loop of liquidations.
*   **Research Question**: How can Coinbase Advanced Trade data act as a "Stabilization Filter"?
    *   If Binance is dropping but Coinbase Delta is neutral/positive, what is the probability that the drop is a "Liquidation Hunt" rather than a "Regime Shift"?
    *   How to mathematically define an "Institutional Wall" using Coinbase ticker data?

### 4. Technical Constraints & Latency
*   **Problem**: The bot uses a Rust ingestor feeding a Python brain via `mmap`.
*   **Research Question**: What is the maximum "polling slack" a Python loop can have before Lead-Lag Alpha is lost to the market? Is 10ms-50ms acceptable, or must the indicator calculation be moved entirely into the Rust layer?

---

## Deliverables
1.  **The Weighted Delta Formula**: Best coefficients for Binance vs. Coinbase vs. Bybit.
2.  **The Lead-Lag Threshold**: A specific "Theta" calculation (e.g., Spread / Volatility).
3.  **Validation**: Documentation of the "Coinbase Leap" phenomenon (where US Spot leads Asian Futures).
4.  **Risk Audit**: Identify scenarios where multi-exchange data could produce "False Positives" (e.g., API desync or exchange maintenance).
