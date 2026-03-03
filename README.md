# VEBB-AI: Volatility-Enhanced Bollinger Bot (AI Edition)

## Overview
VEBB-AI is a next-generation hybrid algorithmic trading system designed for Bitcoin scalping. It combines robust statistical methods with cutting-edge Generative AI to make high-probability trading decisions.

**Core Philosophy:** 
*"Data-Driven Foundation + AI Intuition"*

The system uses **Live 24-Hour Z-Score & Hurst Exponent Mathematics** to deterministically understand the market regime (Trend vs. Range vs. Crisis) and **Gemini 2.5 Pro** to analyze complex order flow dynamics (Footprint charts, Delta, and Liquidity Walls) that traditional algos miss.

---

## 🧠 System Architecture

### 1. The "Brain" (Gemini 2.5 Pro)
- **Role:** Chief Analyst.
- **Input:** 
  - Price Action (Candles).
  - Macro Regime (Rolling Volatility Z-Score and Hurst Exponent).
  - Order Flow (Delta, CVD, Aggie Trade).
  - **NEW (Phase 8):** Order Book Depth (Liquidity Walls & OBI).
- **Output:** structured trade decisions (Long/Short/Hold) with confidence scores and dynamic stop-losses.

### 2. The "Nervous System" (Order Flow)
- **`FootprintBuilder`**: Tracks aggressive buying/selling (Delta) in real-time. Detects absorption and exhaustion.
- **`OrderBookBuilder`**: Tracks passive liquidity (Limit Orders). Calculates **Order Book Imbalance (OBI)** and identifies support/resistance walls.
- **`VolumeProfile`**: Identifies Value Areas (VAH/VAL) and Point of Control (POC) to filter bad trades.

### 3. The "Safety Net" (Risk Management)
- **Continuous Regime Detector**: Automatically switches strategies based on mathematically determined microstructural states (Phase 61):
  - *Calm:* Standard scalping (Z-score < 1.5).
  - *High Vol:* Wider thresholds (Z-score > 1.5)
  - *Crisis:* **Circuit Breaker** (Triggers Pre-Emptive Exhaustion sniper logic if Z-score > 3.0).
  - *Range/Trend:* Dynamic scaling via Rolling Hurst Exponents.
- **Hard Limits**:
  - Daily Loss Limit: Stops trading if equity drops >15%.
  - Max Position Size: Caps risk per trade.
  - Stop Loss: Hard stop enforced on every trade (Max 1.5%).

---

## 🚀 Key Features (Development Phases)

| Phase | Feature | Validated |
| :--- | :--- | :--- |
| **1** | **Data Stream & HMM** | ✅ |
| **2** | **Gemini Integration** | ✅ |
| **3** | **Execution Engine** | ✅ |
| **4** | **Safety Systems** | ✅ |
| **5** | **Volume Profile** | ✅ |
| **6** | **Robustness** | ✅ |
| **7** | **Smart Exit (Flow-Aware)** | ✅ |
| **8** | **Order Book "Future Vision"** | ✅ |

### New in Phase 8: "Future Vision"
The bot now sees **Liquidity Walls** in the Order Book (`btcusdt@depth20`). 
- If Gemini sees a **Buy Wall** below, it knows support is strong.
- If it sees a **Sell Wall** above, it avoids longing into resistance.

---

## 🛠️ Installation & Setup

### 1. Requirements
- Python 3.10+
- Binance User API Key (Futures Enabled)
- Google Gemini API Key
- `pip install -r requirements.txt`

### 2. Configuration (`.env`)
Create a `.env` file:

```ini
# Binance Keys
BINANCE_API_KEY=your_key
BINANCE_SECRET=your_secret
BINANCE_TESTNET=false  # Set to false for Mainnet

# Gemini Key
GEMINI_API_KEY=your_gemini_key

# Trading Settings
initial_capital=200.0
leverage=20
max_position_pct=1.0  # Percentage of capital to use per trade
daily_loss_limit_pct=0.15
```

---

## 🖥️ Usage

### Run the Bot
The VEBB-AI system runs natively on the **15-Minute Timeframe**.

```bash
python main_instance.py
```

### Monitoring
Check the logs in `logs/trading.log` for real-time decision making.
- Look for `📚 Order Book: OBI=...` to see depth analysis.
- Look for `🧠 Consulting Gemini...` to see AI reasoning.

---

## ⚠️ Disclaimer
This software is for educational purposes. Cryptocurrency trading involves high risk. **Use at your own risk.** The authors are not responsible for financial losses.
