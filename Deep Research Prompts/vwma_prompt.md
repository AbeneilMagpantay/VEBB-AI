# Deep Research Prompt: Institutional VWMA & Micro-VWAP Integration

## The Problem
Our automated trading bot (`VEBB-AI`) currently executes high-frequency trades on Binance Futures using a combination of Order Flow Imbalance, Hawkes Processes, and Dynamic Delta Thresholds. However, for baseline Mean Reversion and Trend Identification during slower regimes, it falls back on a standard **21-period Exponential Moving Average (EMA)** on the 15-minute timeframe.

This EMA is completely "blind" to volume. It treats a 10 BTC candle and a 10,000 BTC candle with the exact same weight. Our bot successfully identifies extreme volume shocks (+3,600 BTC Delta) instantly, but our baseline moving average does not retain or reflect that institutional flow.

## The Goal
I need a rigorous, mathematical investigation into replacing the retail EMA with an **Institutional Volume-Weighted Moving Average (VWMA)** or a **Rolling Composite Micro-VWAP**, specifically optimized for a 15-minute timeframe algorithmic system.

## Required Deliverables from Research
1. **Mathematical Superiority:** Provide the exact mathematical proof (with formulas) of why a VWMA or Rolling VWAP absorbs structural liquidity cascades far better than an EMA.
2. **Timeframe Independence vs Alignment (The "T" Variable):** 
   - We run the bot on a 15-minute candle evaluation loop, but also run a sub-second `Flashpoint` engine mid-candle. 
   - Should our VWMA use a fixed period (e.g., 21 periods of 15m), or should it be a "Session Volume-Weighted Average Price" (VWAP) anchored to specific macro-events (like the daily open or London/NY crossover)? Provide the quantitative reasoning for the optimal setup.
3. **VWAP Deviation Bands (Z-Scoring):** How can we mathematically construct standard deviation bands around this VWMA/VWAP based on **Cumulative Delta Volume (CVD)** rather than just price variance? Provide pseudocode for a "Volume Z-Score" channel that we can use for precise Mean Reversion entries.
4. **Computational Complexity:** Our bot must parse ticks in microseconds. Explain the absolute fastest way ( Big O memory/time limits) to maintain a rolling VWMA or Anchored VWAP inside a Python `collections.deque` or Numba `@njit` loop without lagging the asyncio thread. Can we derive it continuously from our existing `FootprintBuilder` module?

## Output Requirements
Do not output basic Retail concepts (like "when price crosses VWAP, you buy"). Output strictly institutional algorithmic structures, time-series analysis formulas, and Python implementation schemas. Focus heavily on how Volume dictates standard deviations, and how we can use this data to calculate dynamic reversion limits.
