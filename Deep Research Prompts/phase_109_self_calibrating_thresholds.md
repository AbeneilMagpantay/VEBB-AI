# Deep Research: Self-Calibrating Threshold Framework for BTC HFT (Phase 109)

## Objective

Replace ALL remaining hardcoded numerical thresholds in VEBB-AI's entry/exit pipeline with self-calibrating, statistically derived alternatives. The bot currently has 12+ fixed constants that were chosen during development and never adapted to evolving market microstructure. These stale values caused the bot to miss a +3.4% ($66,500 → $68,800) rally across 10 consecutive candles.

**Core Principle**: Every threshold must be derived from observable, rolling market statistics — never from a developer's intuition.

---

## System Context

- **Asset**: BTC/USDT Perpetual Futures (Binance)
- **Timeframe**: 15-minute candles
- **Leverage**: 75×
- **Available rolling data**: 24h buffer of GK Volatility, Hurst Exponent, Hawkes λ, CVD, Delta, OFI, OBI
- **Regime Detector**: Rolling Z-Score of GK Volatility + Rolling Hurst Exponent (R/S Analysis)
- **Existing self-calibrating modules**: Bayesian Sparsity Gate (Phase 106), Dynamic TP/SL (Phase 107)

---

## Hardcoded Constants to Replace

### 1. Regime Detector (`regime_detector.py`)

Current implementation uses fixed Z-score and Hurst thresholds:

```python
# Volatility Regime (Z-Score of GK Volatility)
if z_score > 3.0:     vol_regime = "CRISIS"
elif z_score > 1.5:   vol_regime = "HIGH_VOL"
else:                  vol_regime = "NORMAL"

# Trend Regime (Hurst Exponent)
if hurst < 0.45:      trend_regime = "RANGE"
elif hurst > 0.55:    trend_regime = "TREND"
else:                  trend_regime = "TRANSITION"
```

**Problem**: These thresholds are asset-agnostic and timeframe-agnostic. BTC's 15m volatility distribution has fat tails — a Z=1.5 cutoff may be too low during consolidation periods and too high during structural regime shifts. The Hurst boundaries (0.45/0.55) assume a symmetric random walk prior, ignoring BTC's empirically observed persistence characteristics.

**Research Required**:
- Should the Z-score thresholds be percentile-based (e.g., 90th/99th percentile of rolling 24h Z-scores)?
- Should we use **hysteresis bands** to prevent regime oscillation (e.g., enter CRISIS at Z>3.0 but exit only at Z<2.5)?
- Should the Hurst boundaries adapt to the **rolling standard deviation of Hurst estimates** (i.e., H > μ_H + 0.5σ_H → TREND)?
- What is the empirically optimal window size for Hurst calculation on BTC 15m data? (Currently 20 lags)

---

### 2. Sniper Filter Value Area Boundaries (`main.py`)

```python
ENTRY_BUFFER = 25  # Fixed 25% buffer for VA position classification
```

This means:
- `pct_va < 25%` → DISCOUNT (long reversion allowed)
- `pct_va > 75%` → PREMIUM (short reversion allowed)
- `25% ≤ pct_va ≤ 75%` → FAIR VALUE (no entry allowed)

**Problem**: During trending markets, the Value Area continuously shifts. A 25% buffer is too wide during trends (blocks valid entries) and potentially too narrow during ranges (allows premature entries).

**Research Required**:
- Should ENTRY_BUFFER scale with the **Hurst exponent**? (e.g., `ENTRY_BUFFER = 25 * (1 - H)` → wider entry zone in trends, tighter in ranges)
- Should it scale with the **width of the Value Area** relative to ATR? (Narrow VA → tighter buffer, Wide VA → wider buffer)
- Should we use the **rate of change of the Value Area boundaries** to detect VA expansion during breakouts?

---

### 3. Circuit Breaker (`main.py`, line 1657)

```python
if circuit_breaker:  # Fires when regime_detector returns CB=True (Z > 3.0)
    # Close all positions, block all entries
```

**Problem**: The CB fires at the Z > 3.0 boundary (same as CRISIS regime). During the missed rally, the CB activated twice at Z=3.04 and Z=3.51 — both during valid bullish breakouts, not actual crashes. The CB cannot distinguish between constructive volatility (breakout) and destructive volatility (crash).

**Research Required**:
- Should the CB threshold be **adaptive** based on rolling volatility regime? (e.g., in a period of sustained high vol, Z=3.0 is normal — the CB should fire at a higher percentile)
- Should the CB incorporate **directional context**? (e.g., if HTF bias is Strong Bullish and Delta is massively positive, Z=3.0 is likely a breakout, not a crash)
- Should we use a **dual-threshold CB**: one for undirected crises (high Z + low absolute Delta) and one for directed breakouts (high Z + high absolute Delta)?
- What about a **probabilistic CB** using the Hawkes process? (CB fires only when Hawkes λ exceeds its 99th percentile AND the derivative dλ/dt < 0, indicating decelerating intensity)

---

### 4. DYN-DI Guard — Liquidation Hunt Detection (`main.py`, line ~1234)

```python
if is_trap and delta > 0:  # is_trap fires when DI Z-score < -2.50
    result["reason"] = "Liquidation Hunt detected (Z < -2.50). Blocking LONG."
```

**Problem**: The -2.50 threshold is fixed. During volatile periods, DI Z-scores regularly exceed -2.50 without indicating liquidation hunts. This blocked a valid entry at $68,437 with Z=-6.86 during a genuine breakout.

**Research Required**:
- Should the DI threshold be **percentile-based** from the rolling 24h DI distribution?
- Should it incorporate **regime context**? (e.g., in HIGH_VOL, the threshold should be wider like Z < -4.0)
- Should the guard use a **Bayesian approach** similar to the sparsity gate — maintaining a prior over "normal" DI ranges and only flagging statistically anomalous deviations?

---

### 5. Absorption Guard Thresholds (`main.py`, line 1760)

```python
is_absorbing = micro_metrics.absorption_probability > 0.85
```

**Problem**: Fixed 0.85 threshold for absorption detection.

**Research Required**:
- Should this scale with regime? (In HIGH_VOL, absorption events are more common and 0.85 may be too sensitive)
- Should it use the **rolling distribution of absorption_probability** to set an adaptive percentile threshold?

---

### 6. Trend Breakout Constants (`main.py`, line 1245-1257)

```python
base_trend = 125.0 if self.timeframe == "5m" else 250.0
trend_threshold = base_trend * (1.0 + math.log1p(intensity / 25000.0))
# GOBI threshold: 0.1 (hardcoded)
if delta > trend_threshold and obi_fused > 0.1:
```

**Problem**: `base_trend=250.0`, `intensity_divisor=25000.0`, `GOBI_threshold=0.1` are all hardcoded.

**Research Required**:
- Should `base_trend` be derived from the **rolling percentile of absolute delta**? (e.g., 95th percentile of |Δ| over 24h)
- Should the GOBI threshold be the **rolling mean of |GOBI|** plus some adaptive margin?

---

### 7. Mean Reversion Thresholds (`main.py`, lines 1316-1318)

```python
price_extended_long = price <= lower_band  # VWAP - 2σ
sellers_exhausted = cvd_z_score > -1.5
standard_long = (delta > 1.0) and (obi_fused >= obi_thresh_long) and price_extended_long and sellers_exhausted
```

**Problem**: `2.0` (Bollinger width), `-1.5` (CVD Z exhaustion), `1.0` (minimum delta) are all hardcoded.

**Research Required**:
- Should the Bollinger multiplier scale with regime? (Wider in HIGH_VOL, tighter in NORMAL)
- Should `cvd_z_score > -1.5` be replaced with a percentile-based threshold from rolling CVD Z distribution?

---

## Deliverables

For each of the 7 constants groups above, provide:

1. **Mathematical formula** for the self-calibrating replacement
2. **Rolling window size** recommendation (with justification)
3. **Warm-up strategy** for cold start (what default to use before enough data accumulates)
4. **Hysteresis mechanism** to prevent rapid oscillation between states
5. **Python pseudocode** showing the implementation

### Design Constraints

- All calibration must use data already available in the bot (no new API calls)
- Rolling windows should align with existing buffers (24h = 96 candles on 15m)
- Cold-start defaults should be the current hardcoded values (graceful degradation)
- Changes must be **monotonically improving** — each self-calibrating threshold should be independently deployable
- No external dependencies beyond numpy

### Evaluation Criteria

For each proposed replacement, explain:
- Under what market conditions does the **current hardcoded value fail**?
- How does the **proposed adaptive version** behave differently in those conditions?
- What is the **worst-case failure mode** of the adaptive version?
