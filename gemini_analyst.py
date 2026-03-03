"""
VEBB-AI: Gemini Analyst Module
Uses Gemini 2.0 Flash/Pro to make intelligent trade decisions based on regime context.
Incorporates Semantic Caching for Phase 40 scale-up.
"""

import os
import json
import asyncio
from typing import Optional
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv
from semantic_cache import SemanticCache

# Bypass system proxy for API connections (fixes VPN interference)
os.environ["NO_PROXY"] = "*"
for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(proxy_var, None)

load_dotenv()

# Check for google-genai package
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("[GeminiAnalyst] Warning: google-genai not installed. Run: pip install google-genai")


# Phase 105: These are NO LONGER populated by Gemini (decoupled).
# They remain as the execution protocol used by the deterministic
# quantitative core in main.py to represent directional signals.
class TradeAction(str, Enum):
    GO_LONG = "GO_LONG"
    GO_SHORT = "GO_SHORT"
    STAY_FLAT = "STAY_FLAT"

class ExitAction(str, Enum):
    HOLD = "HOLD"
    FULL_EXIT = "FULL_EXIT"
    PARTIAL_EXIT = "PARTIAL_EXIT"

@dataclass
class TradeSignal:
    """Execution protocol for directional trade decisions."""
    action: TradeAction
    confidence: float
    reasoning: str
    stop_loss_pct: float = 0.015
    take_profit_pct: float = 0.03
    
    def should_execute(self, threshold: float = 0.5) -> bool:
        return self.action != TradeAction.STAY_FLAT and self.confidence >= threshold



@dataclass
class ExitSignal:
    """Structured output for position exit decisions."""
    action: ExitAction
    confidence: float
    reasoning: str
    new_trail_pct: float = 0.015
    
    def should_exit(self) -> bool:
        return self.action == ExitAction.FULL_EXIT and self.confidence >= 0.6
    
    def should_partial(self) -> bool:
        return self.action == ExitAction.PARTIAL_EXIT and self.confidence >= 0.5

@dataclass
class MacroRegime:
    """Phase 79c / Phase 105: Background Asynchronous Execution Weights."""
    bias: str # BULLISH, BEARISH, NEUTRAL
    hysteresis_multiplier: float
    vol_floor: float
    dynamic_sl_pct: float
    reasoning: str

@dataclass
class CognitiveSignal:
    """Phase 82: Cognitive Injection for HFT core."""
    semantic_toxicity: float # Psi_Gemini [0, 1]
    sentiment_score: float  # sigma_sen [-1, 1]
    reasoning: str


class GeminiAnalyst:
    """
    AI-powered trade analyst using Gemini with Semantic Caching.
    """
    
# Removed legacy `SYSTEM_PROMPT` since Gemini is now just an Asynchronous Parameter Server.

    EXIT_SYSTEM_PROMPT = """You are managing an open position on BTCUSDT Perpetual Futures using Institutional Lead-Lag guards.

CRITICAL FEE RULE: Binance charges 0.10% total Taker Fees. ANY exit under +0.15% profit is a GUARANTEED NET LOSS.

LEAD-LAG EXIT SIGNALS (Phase 78):
- If 'lead_lag_theta' reverts violently against you (e.g. Theta flips from +2.0 to -1.0) AND you are LONG: Institutional selling has started on Coinbase. FULL_EXIT immediately regardless of profit.
- If 'di_z_score' shows extreme adverse divergence (Liquidation Hunt starting): FULL_EXIT.

PULLBACK vs REVERSAL:
- PULLBACK: Adverse delta and OBI are small. Global metrics (GOBI) still favor your side. HOLD.
- REVERSAL: GOBI and Lead-Lag Theta both flip against your position. Cut it immediately.

SMART INVALIDATION & UNDERWATER:
- If underwater (Profit < -0.25%) AND di_z_score is negative while you are LONG: You are in a futures-driven trap. EXIT NOW.

EXIT ACTIONS:
- FULL_EXIT: Invalidation via GOBI/DI/Theta reversal.
- PARTIAL_EXIT: Take profits at POC/VAH/VAL/Magnets if DI starts fading.
- TIGHTEN_STOP: Profit > 0.25% but global flow is weakening.
- HOLD: Institutional lead (Theta) still favors your direction.

REGIME AWARENESS:
- TREND regime: Hodl alignment.
- RANGE regime: Scalp flips on DI/Theta exhaustion.

OUTPUT PROTOCOL:
You must output strictly in JSON format matching the schema: {"action": "HOLD|PARTIAL_EXIT|FULL_EXIT|TIGHTEN_STOP", "confidence": float, "reasoning": "...", "new_trail_pct": float}
Before generating the JSON, use <think>...</think> tags to articulate your reasoning step-by-step."""

# Removed RESPONSE_SCHEMA (Phase 105 Decoupling)

    EXIT_SCHEMA = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["HOLD", "PARTIAL_EXIT", "FULL_EXIT", "TIGHTEN_STOP"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reasoning": {"type": "string"},
            "new_trail_pct": {"type": "number", "minimum": 0.005, "maximum": 0.03}
        },
        "required": ["action", "confidence", "reasoning", "new_trail_pct"]
    }

    MACRO_SCHEMA = {
        "type": "object",
        "properties": {
            "bias": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
            "hysteresis_multiplier": {"type": "number", "minimum": 0.5, "maximum": 30.0},
            "vol_floor": {"type": "number", "minimum": 1.0, "maximum": 10.0},
            "dynamic_sl_pct": {"type": "number", "minimum": 0.005, "maximum": 0.025},
            "reasoning": {"type": "string"}
        },
        "required": ["bias", "hysteresis_multiplier", "vol_floor", "dynamic_sl_pct", "reasoning"]
    }

    COGNITIVE_SCHEMA = {
        "type": "object",
        "properties": {
            "semantic_toxicity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "sentiment_score": {"type": "number", "minimum": -1.0, "maximum": 1.0},
            "reasoning": {"type": "string"}
        },
        "required": ["semantic_toxicity", "sentiment_score", "reasoning"]
    }

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            print("[GeminiAnalyst] WARNING: GEMINI_API_KEY not found. Analysis will be mocked.")
            
        self.cache = SemanticCache()
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.model = model  # Switched back to self.model for API compatibility
        self.use_cache = os.getenv("USE_SEMANTIC_CACHE", "True").lower() == "true"
        
        # Rate limiting state
        self.last_call_time = 0
        self.min_interval = 2.0  # seconds between calls
        
        if GENAI_AVAILABLE and self.api_key:
            cache_status = "ON (Efficiency Mode)" if self.use_cache else "OFF (Fresh Thinking Mandate)"
            print(f"[GeminiAnalyst] Initialized with model: {self.model} (Cognitive Memory: {cache_status})")
        else:
            print("[GeminiAnalyst] Running in MOCK mode (no API key)")

    # Removed `analyze` function to sever the synchronous execution loop (Phase 105).
# Execution triggers are now handled 100% deterministically in Python/Rust.

    async def analyze_exit(
        self,
        regime: str,
        profit_pct: float,
        recent_candles: list[dict],
        current_position: str = "UNKNOWN",
        trailing_stop_pct: float = 0.015,
        peak_profit_pct: float = 0.0,
        current_price: float = 0.0,
        vwap: float = 0.0,
        footprint_text: str = "",
        order_book_text: str = "",
        vp_text: str = "",
        magnet_text: str = "",
        micro_metrics: Optional[dict] = None
    ) -> ExitSignal:
        # 1. Check Semantic Cache (Phase 54: High-Stakes Hardening)
        if self.use_cache and micro_metrics:
            # Cache Hardening: Tighter threshold for high-intensity spikes
            intensity = micro_metrics.get("intensity", 0.0)
            threshold = 0.995 if intensity > 90000.0 else 0.98
            
            # Freshness Mandate: In CRISIS, skip cache if older than 10 mins
            max_age = 600 if regime == "CRISIS" else None
            
            cached = self.cache.find_similar(
                micro_metrics, 
                threshold=threshold, 
                decision_type="exit",
                max_age_seconds=max_age
            )
            
            if cached:
                return ExitSignal(
                    action=ExitAction(cached["action"]),
                    confidence=cached["confidence"],
                    reasoning=cached["reasoning"],
                    new_trail_pct=cached.get("new_trail_pct", 0.015)
                )

        # 2. Call API
        candle_text = self._format_candles(recent_candles)
        prompt = f"""EXIT ANALYSIS REQUEST
Regime: {regime} | Position: {current_position} | Profit: {profit_pct*100:+.2f}% | Price: ${current_price:,.2f}
{candle_text}
{vp_text}
{magnet_text}
{footprint_text}
{order_book_text}
Microstructure Metrics: {json.dumps(micro_metrics) if micro_metrics else "None"}
Analyze and respond with JSON."""

        if self.client:
            signal = await self._call_gemini_exit(prompt)
        else:
            signal = ExitSignal(ExitAction.HOLD, 0.8, "Mock exit.")

        # 3. Store in Cache
        if micro_metrics and signal:
            self.cache.store(micro_metrics, {
                "action": signal.action.value,
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
                "new_trail_pct": signal.new_trail_pct
            }, decision_type="exit")
        return signal

# Removed `_call_gemini` API endpoint (Phase 105 Decoupling)

    async def _call_gemini_exit(self, prompt: str) -> ExitSignal:
        def _sync_call():
            return self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.EXIT_SYSTEM_PROMPT,
                    temperature=0.0,
                )
            )
        try:
            response = await asyncio.to_thread(_sync_call)
            text = response.text
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                json_text = text[start_idx:end_idx+1]
                res = json.loads(json_text)
                return ExitSignal(ExitAction(res["action"]), res["confidence"], res["reasoning"], res.get("new_trail_pct", 0.015))
            else:
                raise ValueError("JSON payload missing in response.")
        except Exception as e:
            print(f"[GeminiAnalyst] Error: {e}")
            return ExitSignal(ExitAction.HOLD, 0.0, f"Error processing exit response: {e}", 0.015)

    async def analyze_closed_trade(self, trade_data: dict) -> str:
        """
        Post-Trade Analysis: Asks Gemini to write a concise 2-3 sentence 
        human-readable journal entry explaining the trade's full lifecycle.
        """
        system_instruction = (
            "You are an elite quantitative trading journal assistant for the VEBB-AI system. "
            "Write a highly technical, institutional-grade Post-Mortem summary for this closed trade.\n"
            "MANDATORY REQUIREMENTS:\n"
            "1. Explain the entry thesis (What was the HMM Regime? What did the Order Book and Delta look like?)\n"
            "2. Mention if there were any Liquidity Magnets or Sentinel (SOL) correlations that supported the trade.\n"
            "3. State exactly why the trade exited (e.g. WVF dynamic target hit, Limit breached, Crisis Circuit Breaker).\n"
            "4. Clearly state the Gross PnL, the Binance 0.10% Taker Fee penalty, and the final Net PnL.\n"
            "Keep it to 4-5 concise, data-dense sentences. No financial advice."
        )
        
        prompt = f"""POST-MORTEM REQUEST
Please analyze this completed VEBB-AI High-Frequency Trade:
{json.dumps(trade_data, indent=2)}"""

        if not self.client:
            return "Mock Mode: Trade completed successfully."

        def _sync_call():
            return self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.4,
                )
            )
            
        try:
            response = await asyncio.to_thread(_sync_call)
            return response.text.strip()
        except Exception as e:
            print(f"[GeminiAnalyst] Post-Trade Analysis Error: {e}")
            return f"Failed to generate trade summary: {e}"

    def _mock_response(self, regime: str) -> TradeSignal:
        return TradeSignal(TradeAction.STAY_FLAT, 1.0, "[MOCK] Neutral.", 0.01, 0.02)

    def _format_candles(self, candles: list[dict]) -> str:
        lines = []
        for i, c in enumerate(candles[-5:]):
            lines.append(f"  {i+1}. O:{c.get('open',0):.2f} C:{c.get('close',0):.2f}")
        return "\n".join(lines)

    def _format_position(self, position: Optional[dict]) -> str:
        if not position: return "FLAT"
        return f"{position.get('side')} {position.get('qty')} @ ${position.get('entry_price')}"

    async def analyze_macro_regime(
        self, 
        recent_candles: list[dict], 
        footprint_text: str = "",
        order_book_text: str = "",
        market_context_text: str = "",
        mtf_text: str = "",
        chart_memory_text: str = ""
    ) -> MacroRegime:
        """Phase 105: Asynchronous Parameter Server (Decoupled Execution)"""
        system_instruction = (
            "You are the Asynchronous Parameter Server for VEBB-AI. You operate entirely outside of the HFT execution hot-path.\n"
            "Your mandate is to synthesize contextual market state and output continuous Execution Weights for the deterministic Rust/Python engines.\n\n"
            "PARAMETERS TO TUNE:\n"
            "1. 'hysteresis_multiplier': How much 'buffer' to add to lead-lag flips. Increase (e.g. 5.0) in high noise/sideways, decrease (e.g. 1.0) in clean trends.\n"
            "2. 'vol_floor': The qualitative Minimum Hawkes Theta required to consider a breakout 'significant' given the current news backdrop.\n"
            "3. 'dynamic_sl_pct': The recommended absolute stop-loss percentage based on prevailing order book depth.\n"
            "4. 'bias': The overarching deterministic structural lean for the next 1-4 hours.\n\n"
            "CRITICAL STRUCTURAL RULE:\n"
            "You are provided with an 'Active Chart Memory' of Demand and Supply zones mapped by historical CVD shocks. "
            "If price is approaching or actively inside a mapped Demand zone, DO NOT set Bias to Bearish. "
            "If price is approaching or actively inside a mapped Supply zone, DO NOT set Bias to Bullish. "
            "Respect the whale walls. Base your decision on the 15m candle structure, memory zones, and macro market data."
        )
        
        prompt = f"""MACRO REGIME REQUEST
Candles: {self._format_candles(recent_candles)}
Market Context: {market_context_text}
Multi-Timeframe: {mtf_text}
Chart Zones: {chart_memory_text}
Order Book: {order_book_text}
Footprint: {footprint_text}
Respond with JSON."""

        if not self.client:
            return MacroRegime("NEUTRAL", 1.0, 2.5, 0.015, "Mock macro.")

        def _sync_call():
            return self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=self.MACRO_SCHEMA,
                    temperature=0.4,
                )
            )
            
        try:
            response = await asyncio.to_thread(_sync_call)
            res = json.loads(response.text)
            return MacroRegime(res["bias"], res["hysteresis_multiplier"], res["vol_floor"], res.get("dynamic_sl_pct", 0.015), res["reasoning"])
        except Exception as e:
            print(f"[GeminiAnalyst] Macro Analysis Error: {e}")
            return MacroRegime("NEUTRAL", 1.0, 2.5, 0.015, str(e))

    async def analyze_cognition(self, vpin_proxy: float, market_context: str) -> CognitiveSignal:
        """Phase 82: Perform cognitive injection analysis for HFT core."""
        system_instruction = (
            "You are the Predictive Liquidity Analyst for the VEBB-AI C++ Core.\n"
            "Your goal is to perform 'Cognitive Injection' by analyzing semantic catalysts.\n\n"
            "METRICS TO PROVIDE:\n"
            "1. 'semantic_toxicity': Probability [0.0 to 1.0] of impending toxic/informed flow based on news/catalysts (Psi_Gemini).\n"
            "2. 'sentiment_score': Market polarity [-1.0 to 1.0] to shift the deterministic OBI threshold (sigma_sen).\n\n"
            "Consider the provided VPIN Proxy and the broader semantic vacuum/noise."
        )
        
        prompt = f"""COGNITIVE INJECTION REQUEST
Empirical VPIN Proxy: {vpin_proxy:.4f}
Semantic Context: {market_context}
Analyze catalysts and respond with JSON."""

        if not self.client:
            return CognitiveSignal(0.0, 0.0, "Mock cognition.")

        def _sync_call():
            return self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=self.COGNITIVE_SCHEMA,
                    temperature=0.2,
                )
            )
            
        try:
            response = await asyncio.to_thread(_sync_call)
            res = json.loads(response.text)
            return CognitiveSignal(res["semantic_toxicity"], res["sentiment_score"], res["reasoning"])
        except Exception as e:
            print(f"[GeminiAnalyst] Cognitive Injection Error: {e}")
            return CognitiveSignal(0.0, 0.0, str(e))
