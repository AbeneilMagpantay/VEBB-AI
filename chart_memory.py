"""
VEBB-AI: Algorithmic Supply & Demand Zone Mapper
Maps historical institutional footprints (CVD Shocks/Intensity Climaxes) into Memory.
Uses Volume-Weighted Decay and Absorption metrics for continuous probabilistic management.
"""

import numpy as np
from collections import namedtuple
from typing import List, Dict, Set, Optional
import time

# Immutable structure for memory locality and speed
Zone = namedtuple('Zone', ['id', 'type', 'top', 'bottom', 'poc', 'base_volume', 'creation_vol_time'])

class SupplyDemandMapper:
    """
    Maintains the state of historical Supply and Demand zones.
    Utilizes Volume-Weighted Decay and Volume Absorption Invalidation.
    """
    def __init__(self, adv_decay_factor: float = 0.00005, absorption_multiplier: float = 0.90, timeframe: str = "15m", h24_window: int = 96):
        self.active_zones: List[Zone] = []
        self.zone_weights: Dict[int, float] = {}       # Maps zone ID to current probabilistic weight
        self.zone_traded_volume: Dict[int, float] = {} # Tracks aggressive volume executed against the zone
        self.global_cumulative_volume: float = 0.0     
        self.cvd_buffer: List[float] = [] # Rolling buffer for absolute delta (Z-score math)
        self.decay_factor = adv_decay_factor
        self.absorption_multiplier = absorption_multiplier
        self._zone_counter = 0
        self.timeframe = timeframe # Store timeframe if needed elsewhere, though not explicitly used in this change
        self.h24_window = h24_window

    def update_global_clock(self, trade_volume: float):
        """Updates the global volume clock used for stochastic decay on every tick."""
        self.global_cumulative_volume += trade_volume

    def instantiate_zone(self, z_type: str, price_high: float, price_low: float, poc: float, base_vol: float):
        """
        Triggered by external microstructure detectors identifying a CVD Shock 
        or extreme Intensity climax.
        """
        # 1. Clean overlapping zones to prevent double counting
        self._clean_overlaps(price_high, price_low)
        
        self._zone_counter += 1
        new_zone = Zone(
            id=self._zone_counter,
            type=z_type,
            top=max(price_high, price_low),
            bottom=min(price_high, price_low),
            poc=poc,
            base_volume=base_vol,
            creation_vol_time=self.global_cumulative_volume
        )
        self.active_zones.append(new_zone)
        self.zone_weights[new_zone.id] = 1.0  # Initial conviction weight is 100%
        self.zone_traded_volume[new_zone.id] = 0.0
        
        print(f"  [MEMORY] 🧠 NEW {z_type} ZONE MAPPED: ${new_zone.bottom:,.0f} - ${new_zone.top:,.0f} (Vol: {base_vol:.1f} BTC)")

    def evaluate_cvd_shock(self, delta: float, price_high: float, price_low: float, poc: float, timeframe: str = "15m") -> bool:
        """
        Calculates dynamic threshold using a rolling Z-Score and Fractional Power-Law scaling.
        Timeframe agnostic. Replaces the hardcoded 250 BTC trigger.
        Returns True if a zone was mapped.
        """
        abs_delta = abs(delta)
        
        # 1. Update the rolling CVD buffer (Limit to ~24 hours dynamic)
        self.cvd_buffer.append(abs_delta)
        if len(self.cvd_buffer) > self.h24_window:
            self.cvd_buffer.pop(0)
            
        # 2. Need minimum sample size to calculate stats (e.g. 10 candles)
        if len(self.cvd_buffer) < 10:
            return False
            
        # 3. Time Frame Scaling (Fractional Power-Law H=0.75)
        # Convert string to minutes
        tf_mins = 15
        if timeframe.endswith("m"): tf_mins = int(timeframe[:-1])
        elif timeframe.endswith("h"): tf_mins = int(timeframe[:-1]) * 60
        
        # Base threshold was empirically 250 BTC for 15m. Scale it generically.
        # But since we use Z-Score, scaling applies to the minimum required raw volume to even check.
        # So we skip the math if delta is less than a base minimum power-law scaled threshold.
        min_threshold = 250.0 * ((tf_mins / 15.0) ** 0.75)
        
        if abs_delta < (min_threshold * 0.5): # Use 50% as a baseline to let Z-score do the heavy lifting
            return False
            
        # 4. Calculate Rolling Z-Score
        mean_cvd = np.mean(self.cvd_buffer[:-1]) # Don't include current tick in mean yet
        std_cvd = np.std(self.cvd_buffer[:-1])
        
        if std_cvd == 0:
            return False
            
        z_score = (abs_delta - mean_cvd) / std_cvd
        
        # 5. Execution Threshold (3.5 sigma event)
        if z_score >= 3.5:
            zone_type = "DEMAND" if delta < 0 else "SUPPLY" # Negative Delta = Limit Buyers (Demand)
            self.instantiate_zone(zone_type, price_high, price_low, poc, abs_delta)
            print(f"  [MEMORY] 🔍 CVD Shock Authenticated: Z-Score={z_score:.2f} | Local Mean={mean_cvd:.1f}")
            return True
        elif z_score > 2.0:
            print(f"  [MEMORY] ⚠️ High Volume ({abs_delta:.1f} BTC) rejected. Z-Score {z_score:.2f} < 3.5 (Inflated Volatility)")
            
        return False

    def _clean_overlaps(self, high: float, low: float):
        """Removes existing zones that overlap with the new macro footprint."""
        overlap_ids = set()
        for z in self.active_zones:
            if not (high < z.bottom or low > z.top):
                overlap_ids.add(z.id)
        if overlap_ids:
            self._purge_zones(overlap_ids)

    def apply_probabilistic_decay(self):
        """
        Applies continuous volume-weighted exponential decay to all active zones.
        Should be called periodically (e.g. every 15m candle close).
        """
        zones_to_purge = set()
        
        for z in self.active_zones:
            vol_elapsed = self.global_cumulative_volume - z.creation_vol_time
            # Exponential decay: weight decreases as market volume transacts over time
            current_weight = np.exp(-self.decay_factor * vol_elapsed)
            
            if current_weight < 0.15: # Minimum threshold of institutional relevance
                zones_to_purge.add(z.id)
                print(f"  [MEMORY] 🚮 {z.type} Zone #{z.id} decayed naturally.")
            else:
                self.zone_weights[z.id] = float(current_weight)
                
        self._purge_zones(zones_to_purge)

    def process_zone_interaction(self, price: float, volume: float, is_aggressor_buy: bool):
        """
        Evaluates real-time price action to spatially invalidate zones.
        Avoids false invalidations caused by purely volumetric assumptions (Iceberg survival).
        Called on EVERY tick from data_stream.
        """
        if not self.active_zones:
            return
            
        exhausted_zones = set()
        
        for z in self.active_zones:
            # Phase 63 Spatial Displacement Rule:
            # A zone is ONLY breached if the price structurally passes completely through it.
            # Volume counts are irrelevant due to Hidden Reserve Iceberg orders.
            
            is_spatial_breach = False
            if z.type == 'SUPPLY' and price > z.top:
                is_spatial_breach = True
            elif z.type == 'DEMAND' and price < z.bottom:
                is_spatial_breach = True
                
            if is_spatial_breach:
                exhausted_zones.add(z.id)
                print(f"  [MEMORY] 💥 {z.type} ZONE BREACHED at ${price:,.0f}! Spatial displacement confirmed.")
                
            # Still track volume for summary analytics, but do NOT use it for invalidation
            elif z.bottom <= price <= z.top:
                if (z.type == 'SUPPLY' and is_aggressor_buy) or (z.type == 'DEMAND' and not is_aggressor_buy):
                    self.zone_traded_volume[z.id] += volume
                        
        self._purge_zones(exhausted_zones)

    def get_confluence_modifiers(self, current_price: float, hmm_multiplier: float = 1.0) -> dict:
        """
        Returns dynamic parameter discounts based on zone proximity.
        Used by the main Sniper to execute Pre-Emptive Reversals.
        """
        modifiers = {'long_obi_discount': 0.0, 'short_obi_discount': 0.0, 'exhaustion_target': None}
        
        # Add a 0.25% buffer around the zone to start fading slightly early
        buffer_pct = 0.0025
        
        for z in self.active_zones:
            padded_bottom = z.bottom * (1 - buffer_pct)
            padded_top = z.top * (1 + buffer_pct)
            
            if padded_bottom <= current_price <= padded_top:
                weight = self.zone_weights.get(z.id, 0.0)
                
                # Max discount is usually ~0.35 threshold points
                discount = 0.35 * weight * hmm_multiplier
                
                if z.type == 'DEMAND':
                    # Discount Long OBI requirement significantly to anticipate bounce
                    modifiers['long_obi_discount'] = discount
                    modifiers['exhaustion_target'] = z.poc
                    modifiers['zone_type'] = 'DEMAND'
                elif z.type == 'SUPPLY':
                    # Discount Short OBI requirement and flag for Pre-emptive Exhaustion Short
                    modifiers['short_obi_discount'] = discount
                    modifiers['exhaustion_target'] = z.poc
                    modifiers['zone_type'] = 'SUPPLY'
                break # Non-overlapping
                
        return modifiers

    def _purge_zones(self, zone_ids: Set[int]):
        """Helper method to cleanly remove invalidated or decayed zones from memory."""
        if not zone_ids: 
            return
        
        # Filter active zones
        self.active_zones = [z for z in self.active_zones if z.id not in zone_ids]
        
        # Remove hashes safely
        for z_id in zone_ids:
            self.zone_weights.pop(z_id, None)
            self.zone_traded_volume.pop(z_id, None)

    def get_active_zone_summary(self) -> str:
        """Formats active zones for Gemini and Logging."""
        if not self.active_zones:
            return "No Active S&D Zones in Memory."
            
        summary = ["--- 🧠 ACTIVE CHART MEMORY ---"]
        for z in sorted(self.active_zones, key=lambda x: x.bottom):
            weight = self.zone_weights.get(z.id, 0.0)
            absorbed = self.zone_traded_volume.get(z.id, 0.0)
            pct_absorbed = (absorbed / z.base_volume) * 100 if z.base_volume > 0 else 0
            
            strength = "⭐⭐⭐" if weight > 0.75 else "⭐⭐" if weight > 0.4 else "⭐"
            summary.append(f"[{z.type}] ${z.bottom:,.0f}-${z.top:,.0f} | POC: ${z.poc:,.0f} | W: {strength} | Absorbed: {pct_absorbed:.1f}%")
        return "\n".join(summary)
