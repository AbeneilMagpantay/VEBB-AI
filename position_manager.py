"""
VEBB-AI: Position Manager
Tracks open positions, PnL, and enforces risk limits.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict
from enum import Enum


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass
class Position:
    """Current open position."""
    side: Side
    qty: float
    entry_price: float
    entry_time: str
    unrealized_pnl: float = 0.0
    peak_price: float = 0.0  # Highest price since entry (for trailing stop)
    trailing_stop_price: float = 0.0  # Current trailing stop level
    partial_tp_hit: bool = False  # Phase 53: Track if 50% was harvested
    break_even_hit: bool = False  # Phase 53: Track if SL moved to entry
    atr_trail_active: bool = False # Phase 58: ATR-based trailing activation
    
    def to_dict(self) -> dict:
        return {
            "side": self.side.value,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "unrealized_pnl": self.unrealized_pnl,
            "peak_price": self.peak_price,
            "trailing_stop_price": self.trailing_stop_price,
            "partial_tp_hit": self.partial_tp_hit,
            "break_even_hit": self.break_even_hit,
            "atr_trail_active": self.atr_trail_active
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(
            side=Side(data["side"]),
            qty=data["qty"],
            entry_price=data["entry_price"],
            entry_time=data["entry_time"],
            unrealized_pnl=data.get("unrealized_pnl", 0.0),
            peak_price=data.get("peak_price", data["entry_price"]),
            trailing_stop_price=data.get("trailing_stop_price", 0.0),
            partial_tp_hit=data.get("partial_tp_hit", False),
            break_even_hit=data.get("break_even_hit", False),
            atr_trail_active=data.get("atr_trail_active", False)
        )


@dataclass
class TradeRecord:
    """Historical trade record."""
    timestamp: str
    action: str
    side: str
    qty: float
    price: float
    pnl: float
    balance_after: float


class PositionManager:
    """
    Manages trading positions and enforces risk limits.
    
    Features:
    - Position tracking with state persistence
    - Daily PnL tracking
    - Risk limit enforcement
    - Trade history logging
    """
    
    def __init__(
        self,
        initial_capital: float = 100.0,
        leverage: int = 1,
        max_position_pct: float = 0.2,
        daily_loss_limit_pct: float = 0.10,
        fixed_margin: float = 0.0,  # New: Specify exact $ to use as margin (Risk Per Trade)
        fee_rate: float = 0.0005,  # Standard 0.05% Taker fee
        state_file: str = "trading_state.json"
    ):
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.max_position_pct = max_position_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.fixed_margin = fixed_margin
        self.fee_rate = fee_rate
        self.state_file = Path(state_file)
        
        # State
        self.balance = initial_capital
        self.position: Optional[Position] = None
        self.daily_pnl = 0.0
        self.total_fees_paid = 0.0
        self.daily_reset_date = datetime.now().date().isoformat()
        self.trade_history: list[TradeRecord] = []
        self.is_halted = False
        
        # Load existing state
        self._load_state()
    
    def _load_state(self):
        """Load state from file to persist PnL and positions across restarts."""
        if not self.state_file.exists():
            return
            
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                
            # Restore daily PnL if it's the same day
            saved_date = data.get("daily_reset_date", "")
            if saved_date == self.daily_reset_date:
                self.daily_pnl = data.get("daily_pnl", 0.0)
                self.is_halted = data.get("is_halted", False)
                self.balance = data.get("balance", self.initial_capital)
                self.total_fees_paid = data.get("total_fees_paid", 0.0)
            
            # Restore open position if any
            pos_data = data.get("position")
            if pos_data:
                self.position = Position.from_dict(pos_data)
                
        except Exception as e:
            print(f"[PositionManager] Error loading state: {e}")
    
    def _save_state(self):
        """Save current state to file."""
        data = {
            "daily_pnl": self.daily_pnl,
            "daily_reset_date": self.daily_reset_date,
            "is_halted": self.is_halted,
            "balance": self.balance,
            "total_fees_paid": self.total_fees_paid,
            "position": self.position.to_dict() if self.position else None,
            "last_updated": datetime.now().isoformat()
        }
        
        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=2)

    def sync_state(self, exchange_balance: float, exchange_position: Optional[dict]):
        """Synchronize local state with actual exchange state."""
        # 1. Balance Sync
        if abs(self.balance - exchange_balance) > 0.5: # 0.5 USDT threshold for sync
            print(f"[PositionManager] Syncing balance: ${self.balance:.2f} -> ${exchange_balance:.2f}")
            self.balance = exchange_balance
        
        # 2. Position Sync
        local_pos_active = self.position is not None and self.position.side != Side.FLAT
        exchange_pos_active = exchange_position is not None
        
        if local_pos_active and not exchange_pos_active:
            print(f"[PositionManager] ⚠️ GHOST POSITION DETECTED (Local thinks open, Binance says FLAT). Resetting local to FLAT.")
            self.position = None
            
        elif not local_pos_active and exchange_pos_active:
            print(f"[PositionManager] ⚠️ EXTERNAL POSITION DETECTED (Binance has {exchange_position['side']} {exchange_position['qty']}). Adopting.")
            side_map = {"LONG": Side.LONG, "SHORT": Side.SHORT}
            self.position = Position(
                side=side_map.get(exchange_position['side']),
                qty=exchange_position['qty'],
                entry_price=exchange_position['entry_price'],
                entry_time=datetime.now().isoformat(),
                unrealized_pnl=exchange_position['unrealized_pnl'],
                peak_price=exchange_position['entry_price']
            )
            
        elif local_pos_active and exchange_pos_active:
            # Check for side or qty mismatch
            if self.position.side.value != exchange_position['side'] or abs(self.position.qty - exchange_position['qty']) > 0.001:
                 print(f"[PositionManager] ⚠️ POSITION MISMATCH. Local: {self.position.side.value} {self.position.qty} | Binance: {exchange_position['side']} {exchange_position['qty']}. Updating local.")
                 side_map = {"LONG": Side.LONG, "SHORT": Side.SHORT}
                 self.position.side = side_map.get(exchange_position['side'])
                 self.position.qty = exchange_position['qty']
                 self.position.entry_price = exchange_position['entry_price']
        
        self._save_state()
    
    def _check_daily_reset(self):
        """Reset daily PnL at midnight."""
        today = datetime.now().date().isoformat()
        if today != self.daily_reset_date:
            print(f"[PositionManager] New day! Resetting daily PnL.")
            self.daily_pnl = 0.0
            self.daily_reset_date = today
            self.is_halted = False
            self._save_state()
    
    def get_max_position_size(self, current_price: float) -> float:
        """Calculate maximum position size based on risk limits."""
        # Calculate max notional including leverage
        if self.fixed_margin > 0:
            # Use specific dollar amount as MARGIN
            target_margin = self.fixed_margin
        else:
            # Traditional percentage-based sizing
            target_margin = self.balance * self.max_position_pct
            
        # SAFETY CHECK: Ensure requested margin + buffer doesn't exceed real balance
        # We use a $5 buffer to avoid "insufficient margin" errors due to rounding/fees
        SAFETY_BUFFER = 5.0
        available_margin = max(0, self.balance - SAFETY_BUFFER)
        
        final_margin = min(target_margin, available_margin)
        max_notional = final_margin * self.leverage
        
        # Enforce minimum notional (Binance/User limit)
        MIN_NOTIONAL = 110.0
        
        if max_notional < MIN_NOTIONAL:
            print(f"  ⚠️ Risk size ${max_notional:.2f} is below Binance Minimum ($110).")
            # If we have capacity to reach min notional, do it
            if (available_margin * self.leverage) >= MIN_NOTIONAL:
                print(f"  [PositionManager] Bumping size to ${MIN_NOTIONAL} to fulfill requirement.")
                max_notional = MIN_NOTIONAL
            else:
                print(f"  ❌ Balance too low to meet $110 minimum notional.")
                return 0.0

        qty = max_notional / current_price
        
        # Binance BTCUSDT minimum order is 0.001 BTC
        MIN_ORDER_QTY = 0.001
        if qty < MIN_ORDER_QTY:
             print(f"  ⚠️ Quantity {qty:.6f} below minimum {MIN_ORDER_QTY}")
             return 0.0
        
        # Round to 3 decimal places (Binance precision)
        return round(qty, 3)
    
    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed based on risk limits."""
        self._check_daily_reset()
        
        if self.is_halted:
            return False, "Trading halted due to daily loss limit"
        
        daily_loss_limit = self.initial_capital * self.daily_loss_limit_pct
        if self.daily_pnl < -daily_loss_limit:
            self.is_halted = True
            self._save_state()
            return False, f"Daily loss limit hit: ${self.daily_pnl:.2f}"
        
        return True, "OK"
    
    def open_position(self, side: Side, qty: float, price: float) -> bool:
        """Open a new position."""
        if self.position and self.position.side != Side.FLAT:
            print("[PositionManager] Already have an open position!")
            return False
        
        can_trade, reason = self.can_trade()
        if not can_trade:
            print(f"[PositionManager] Cannot open: {reason}")
            return False
        
        # Check position size limit
        max_qty = self.get_max_position_size(price)
        if qty > max_qty:
            print(f"[PositionManager] Reducing qty from {qty:.6f} to {max_qty:.6f} (risk limit)")
            qty = max_qty
        
        self.position = Position(
            side=side,
            qty=qty,
            entry_price=price,
            entry_time=datetime.now().isoformat()
        )
        
        # Calculate Entry Fee
        entry_notional = qty * price
        entry_fee = entry_notional * self.fee_rate
        self.balance -= entry_fee
        self.total_fees_paid += entry_fee
        
        # Record trade
        self.trade_history.append(TradeRecord(
            timestamp=datetime.now().isoformat(),
            action="OPEN",
            side=side.value,
            qty=qty,
            price=price,
            pnl=0.0,
            balance_after=self.balance
        ))
        
        self._save_state()
        print(f"[PositionManager] Opened {side.value} {qty:.6f} @ ${price:.2f}")
        return True
    
    def close_position(self, price: float, reason: str = "MANUAL", external_pnl: float = None, external_fees: float = None) -> float:
        """
        Close current position and return PnL.
        If external_pnl/fees are provided, they override the local estimation.
        """
        if not self.position or self.position.side == Side.FLAT:
            print("[PositionManager] No position to close!")
            return 0.0
        
        if price <= 0:
            print(f"[PositionManager] Error: Cannot close at price ${price:.2f}. Usage rejected.")
            return 0.0
        
        # Calculate PnL (Local estimation as fallback)
        if self.position.side == Side.LONG:
            gross_pnl = (price - self.position.entry_price) * self.position.qty
        else:  # SHORT
            gross_pnl = (self.position.entry_price - price) * self.position.qty
        
        exit_notional = self.position.qty * price
        
        # Override with exact figures from Binance if available
        if external_pnl is not None:
            # Note: Binance realizedPnl is Net (it excludes commission)
            net_pnl = external_pnl
            exit_fee = external_fees if external_fees is not None else (exit_notional * self.fee_rate)
            # Adjust gross for local logging if needed, but net_pnl is the ground truth
        else:
            exit_fee = exit_notional * self.fee_rate
            net_pnl = gross_pnl - exit_fee
        
        # Update balance
        self.balance += net_pnl
        self.daily_pnl += net_pnl
        self.total_fees_paid += exit_fee
        
        # Record trade
        self.trade_history.append(TradeRecord(
            timestamp=datetime.now().isoformat(),
            action=f"CLOSE_{reason}",
            side=self.position.side.value,
            qty=self.position.qty,
            price=price,
            pnl=net_pnl,
            balance_after=self.balance
        ))
        
        print(f"[PositionManager] Closed {self.position.side.value} @ ${price:.2f} | Net PnL: ${net_pnl:.2f} (Fees: ${exit_fee:.2f}) | Balance: ${self.balance:.2f}")
        
        # Clear position
        self.position = None
        self._save_state()
        
        return net_pnl
    
    def update_unrealized_pnl(self, current_price: float) -> float:
        """Update and return unrealized PnL."""
        if not self.position or self.position.side == Side.FLAT:
            return 0.0
        
        if self.position.side == Side.LONG:
            pnl = (current_price - self.position.entry_price) * self.position.qty
        else:
            pnl = (self.position.entry_price - current_price) * self.position.qty
        
        self.position.unrealized_pnl = pnl
        return pnl
    
    def check_stop_loss(self, current_price: float, stop_loss_pct: float) -> bool:
        """Check if stop loss is triggered, dynamically adjusted for margin bleed."""
        if not self.position:
            return False
            
        # Phase 87: Extreme Margin Defense
        # If funding fees have eroded our actual balance below the initial capital,
        # we dynamically tighten the stop-loss proportional to the erosion.
        erosion_ratio = 1.0
        if self.balance > 0 and self.balance < self.initial_capital:
            erosion_ratio = self.balance / self.initial_capital
            
        dynamic_sl_pct = stop_loss_pct * erosion_ratio
        
        if self.position.side == Side.LONG:
            loss_pct = (self.position.entry_price - current_price) / self.position.entry_price
        else:
            loss_pct = (current_price - self.position.entry_price) / self.position.entry_price
        
        if loss_pct >= dynamic_sl_pct:
             if erosion_ratio < 1.0:
                  print(f"  [PositionManager] 🛡️ DYNAMIC STOP-LOSS TRIGGERED: Stopped out early at {dynamic_sl_pct*100:.2f}% (instead of {stop_loss_pct*100:.2f}%) due to margin erosion.")
             return True
             
        return False
    
    def check_take_profit(self, current_price: float, take_profit_pct: float) -> bool:
        """Check if take profit is triggered."""
        if not self.position:
            return False
        
        if self.position.side == Side.LONG:
            profit_pct = (current_price - self.position.entry_price) / self.position.entry_price
        else:
            profit_pct = (self.position.entry_price - current_price) / self.position.entry_price
        
        return profit_pct >= take_profit_pct
    
    def update_trailing_stop(self, current_price: float, trail_pct: float = 0.015) -> float:
        """
        Update trailing stop based on price movement.
        Returns the current trailing stop price.
        
        Args:
            current_price: Current market price
            trail_pct: Trailing distance as percentage (default 1.5%)
        """
        if not self.position:
            return 0.0
        
        # Initialize peak price if not set
        if self.position.peak_price == 0:
            self.position.peak_price = self.position.entry_price
        
        if self.position.side == Side.LONG:
            # Long: trail stop below price
            if current_price > self.position.peak_price:
                self.position.peak_price = current_price
            new_stop = self.position.peak_price * (1 - trail_pct)
            # Only move stop UP, never down
            if new_stop > self.position.trailing_stop_price:
                self.position.trailing_stop_price = new_stop
        else:
            # Short: trail stop above price
            if current_price < self.position.peak_price or self.position.peak_price == 0:
                self.position.peak_price = current_price
            new_stop = self.position.peak_price * (1 + trail_pct)
            # Only move stop DOWN, never up
            if self.position.trailing_stop_price == 0 or new_stop < self.position.trailing_stop_price:
                self.position.trailing_stop_price = new_stop
        
        self._save_state()
        return self.position.trailing_stop_price

    def update_atr_trailing_stop(self, current_price: float, atr: float):
        """
        ATR-Activated Trailing Stop logic (Phase 58).
        Activate @ 2.5x ATR profit, Trail @ 1.75x ATR distance.
        """
        if not self.position or atr <= 0:
            return
            
        # 1. Calculate current distance from entry
        if self.position.side == Side.LONG:
            distance = current_price - self.position.entry_price
        else:
            distance = self.position.entry_price - current_price
            
        # 2. Check for activation (2.5x ATR)
        if not self.position.atr_trail_active and distance >= (2.5 * atr):
            print(f"  [PositionManager] 🌬️ ATR TRAIL ACTIVATED: Distance ${distance:.2f} >= 2.5x ATR (${2.5*atr:.2f})")
            self.position.atr_trail_active = True
            
        # 3. Update stop if active (1.75x ATR trail)
        if self.position.atr_trail_active:
            if self.position.side == Side.LONG:
                new_stop = current_price - (1.75 * atr)
                if new_stop > self.position.trailing_stop_price:
                    self.position.trailing_stop_price = new_stop
            else:
                new_stop = current_price + (1.75 * atr)
                if self.position.trailing_stop_price == 0 or new_stop < self.position.trailing_stop_price:
                    self.position.trailing_stop_price = new_stop
            
            self._save_state()
    
    def check_trailing_stop(self, current_price: float) -> bool:
        """Check if trailing stop is hit."""
        if not self.position or self.position.trailing_stop_price == 0:
            return False
        
        if self.position.side == Side.LONG:
            return current_price <= self.position.trailing_stop_price
        else:
            return current_price >= self.position.trailing_stop_price
    
    def partial_close(self, price: float, close_pct: float = 0.5, reason: str = "PARTIAL") -> float:
        """
        Partially close position.
        
        Args:
            price: Current price
            close_pct: Percentage of position to close (0.5 = 50%)
            reason: Reason for partial close
            
        Returns:
            PnL from the closed portion
        """
        if not self.position or self.position.side == Side.FLAT:
            return 0.0
        
        close_qty = self.position.qty * close_pct
        remaining_qty = self.position.qty - close_qty
        
        # Calculate PnL for closed portion
        if self.position.side == Side.LONG:
            pnl = (price - self.position.entry_price) * close_qty
        else:
            pnl = (self.position.entry_price - price) * close_qty
        
        # Update balance
        self.balance += pnl
        self.daily_pnl += pnl
        
        # Record trade
        self.trade_history.append(TradeRecord(
            timestamp=datetime.now().isoformat(),
            action=f"PARTIAL_{reason}",
            side=self.position.side.value,
            qty=close_qty,
            price=price,
            pnl=pnl,
            balance_after=self.balance
        ))
        
        print(f"[PositionManager] Partial close {close_pct*100:.0f}% @ ${price:.2f} | PnL: ${pnl:.2f}")
        
        if remaining_qty < 0.00001:  # Effectively zero
            self.position = None
        else:
            self.position.qty = remaining_qty
        
        self._save_state()
        return pnl
    
    def get_profit_pct(self, current_price: float, leveraged: bool = False) -> float:
        """Get current profit percentage. If leveraged=True, returns ROE%."""
        if not self.position:
            return 0.0
        
        if self.position.side == Side.LONG:
            unleveraged_pnl = (current_price - self.position.entry_price) / self.position.entry_price
        else:
            unleveraged_pnl = (self.position.entry_price - current_price) / self.position.entry_price
            
        if leveraged:
            return unleveraged_pnl * self.leverage
        return unleveraged_pnl
    
    def get_status(self) -> dict:
        """Get current status summary."""
        return {
            "balance": self.balance,
            "position": self.position.to_dict() if self.position else None,
            "daily_pnl": self.daily_pnl,
            "is_halted": self.is_halted,
            "daily_fees": self.total_fees_paid,
            "trade_count": len(self.trade_history)
        }
    
    def export_history(self, filepath: str = "trade_history.json"):
        """Export trade history to file."""
        records = [asdict(t) for t in self.trade_history]
        with open(filepath, "w") as f:
            json.dump(records, f, indent=2)
        print(f"[PositionManager] Exported {len(records)} trades to {filepath}")


# Test harness
if __name__ == "__main__":
    pm = PositionManager(initial_capital=100.0)
    
    print("\n--- Position Manager Test ---")
    print(f"Status: {pm.get_status()}")
    
    # Test opening position
    pm.open_position(Side.LONG, 0.001, 96000.0)
    print(f"After open: {pm.get_status()}")
    
    # Test PnL update
    pm.update_unrealized_pnl(97000.0)
    print(f"Unrealized PnL at $97k: ${pm.position.unrealized_pnl:.2f}")
    
    # Test close
    pm.close_position(97000.0, "TEST")
    print(f"After close: {pm.get_status()}")
