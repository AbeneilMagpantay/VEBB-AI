"""
VEBB-AI: Trade Logger Module
Comprehensive logging for analysis and optimization.
"""

import sys
import json
import csv
from datetime import datetime
from pathlib import Path


class ConsoleLogger:
    """Redirects stdout to both terminal and file."""
    def __init__(self, filename: Path, terminal):
        self.terminal = terminal
        self.log = open(filename, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()


class TradeLogger:
    """
    Logs all trading decisions, signals, and outcomes for analysis.
    
    Creates daily log files in /logs directory:
    - trades_YYYY-MM-DD.csv - All executed trades
    - signals_YYYY-MM-DD.csv - All signals (including skipped)
    - decisions_YYYY-MM-DD.json - Full decision context
    - console/bot_YYYY-MM-DD.log - Verbatim console output
    """
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Create subdirectories
        (self.log_dir / "trades").mkdir(exist_ok=True)
        (self.log_dir / "signals").mkdir(exist_ok=True)
        (self.log_dir / "decisions").mkdir(exist_ok=True)
        (self.log_dir / "console").mkdir(exist_ok=True)
        
        print(f"[Logger] Initialized. Logs saved to: {self.log_dir.absolute()}")

    def start_console_logging(self):
        """Redirect stdout to a daily console log file."""
        date_str = self._get_date_str()
        filepath = self.log_dir / "console" / f"bot_{date_str}.log"
        sys.stdout = ConsoleLogger(filepath, sys.stdout)
        print(f"[Logger] Console output is now being saved to {filepath.name}")
    
    def _get_date_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")
    
    def _get_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def info(self, message: str):
        """Log info message to console (and file via redirection)."""
        print(f"[{self._get_timestamp()}] INFO: {message}")

    def warning(self, message: str):
        """Log warning message to console."""
        print(f"[{self._get_timestamp()}] ⚠️ WARNING: {message}")

    def error(self, message: str):
        """Log error message to console."""
        print(f"[{self._get_timestamp()}] ❌ ERROR: {message}")
    
    def log_signal(
        self,
        timeframe: str,
        price: float,
        regime: str,
        delta: float,
        obi: float,
        intensity: float,
        sniper_result: dict,
        gemini_action: str,
        gemini_confidence: float,
        gemini_reasoning: str,
        executed: bool,
        skip_reason: str = ""
    ):
        """Log every signal (entry decision point)."""
        date_str = self._get_date_str()
        filepath = self.log_dir / "signals" / f"signals_{date_str}.csv"
        
        # Write header if file doesn't exist
        write_header = not filepath.exists()
        
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "timestamp", "timeframe", "price", "regime", "delta", "obi", "intensity",
                    "sniper_should_trade", "sniper_direction", "sniper_position",
                    "sniper_reason", "range_low", "range_high",
                    "gemini_action", "gemini_confidence", "gemini_reasoning",
                    "executed", "skip_reason"
                ])
            
            writer.writerow([
                self._get_timestamp(),
                timeframe,
                f"{price:.2f}",
                regime,
                f"{delta:.3f}",
                f"{obi:.3f}",
                f"{intensity:.1f}",
                sniper_result.get("should_trade", False),
                sniper_result.get("direction", ""),
                sniper_result.get("position_in_range", ""),
                sniper_result.get("reason", ""),
                f"{sniper_result.get('range_low', 0):.0f}",
                f"{sniper_result.get('range_high', 0):.0f}",
                gemini_action,
                f"{gemini_confidence:.2f}",
                gemini_reasoning[:200],  # Truncate long reasoning
                executed,
                skip_reason
            ])
    
    def log_trade(
        self,
        timeframe: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        pnl: float,
        pnl_pct: float,
        exit_reason: str,
        duration_seconds: int,
        entry_delta: float,
        exit_delta: float
    ):
        """Log executed trades with full details."""
        date_str = self._get_date_str()
        filepath = self.log_dir / "trades" / f"trades_{date_str}.csv"
        
        write_header = not filepath.exists()
        
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "timestamp", "timeframe", "side", "entry_price", "exit_price",
                    "qty", "pnl_usd", "pnl_pct", "exit_reason", "duration_sec",
                    "entry_delta", "exit_delta"
                ])
            
            writer.writerow([
                self._get_timestamp(),
                timeframe,
                side,
                f"{entry_price:.2f}",
                f"{exit_price:.2f}",
                f"{qty:.6f}",
                f"{pnl:.2f}",
                f"{pnl_pct:.4f}",
                exit_reason,
                duration_seconds,
                f"{entry_delta:.3f}",
                f"{exit_delta:.3f}"
            ])
            
    def log_trade_journal(self, timeframe: str, symbol: str, summary: str):
        """Log a human-readable post-trade analysis generated by Gemini."""
        date_str = self._get_date_str()
        
        # Ensure directory exists in case it was created after init
        journal_dir = self.log_dir / "trade_journals"
        journal_dir.mkdir(exist_ok=True)
        
        filepath = journal_dir / f"journal_{date_str}.txt"
        
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"[{self._get_timestamp()}] [{timeframe}] {symbol} Trade Analysis:\n")
            f.write(f"{summary}\n")
            f.write("-" * 80 + "\n\n")
    
    def log_decision(
        self,
        timeframe: str,
        context: dict
    ):
        """Log full decision context as JSON for deep analysis."""
        date_str = self._get_date_str()
        filepath = self.log_dir / "decisions" / f"decisions_{date_str}.jsonl"
        
        decision = {
            "timestamp": self._get_timestamp(),
            "timeframe": timeframe,
            **context
        }
        
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision) + "\n")
    
    def get_daily_summary(self, date_str: str = None) -> dict:
        """Get summary stats for a day."""
        if date_str is None:
            date_str = self._get_date_str()
        
        trades_file = self.log_dir / "trades" / f"trades_{date_str}.csv"
        
        if not trades_file.exists():
            return {"trades": 0, "total_pnl": 0, "win_rate": 0}
        
        trades = []
        with open(trades_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            trades = list(reader)
        
        if not trades:
            return {"trades": 0, "total_pnl": 0, "win_rate": 0}
        
        # Filter out header rows (can happen if bot restarted) and validate data
        valid_trades = []
        for t in trades:
            try:
                # Ensure pnl_usd is actually numeric
                float(t.get("pnl_usd", "0"))
                valid_trades.append(t)
            except (ValueError, TypeError):
                continue
                
        if not valid_trades:
            return {"trades": 0, "total_pnl": 0, "win_rate": 0}

        total_pnl = sum(float(t["pnl_usd"]) for t in valid_trades)
        wins = sum(1 for t in valid_trades if float(t["pnl_usd"]) > 0)
        win_rate = wins / len(valid_trades) if valid_trades else 0
        
        return {
            "date": date_str,
            "trades": len(valid_trades),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate * 100, 1),
            "wins": wins,
            "losses": len(valid_trades) - wins,
            "avg_pnl": round(total_pnl / len(valid_trades), 2) if valid_trades else 0
        }
    
    def print_summary(self):
        """Print today's summary to console."""
        summary = self.get_daily_summary()
        print(f"\n{'='*50}")
        print(f"  Daily Summary: {summary.get('date', 'Today')}")
        print(f"{'='*50}")
        print(f"  Trades: {summary['trades']}")
        print(f"  Total PnL: ${summary['total_pnl']:+.2f}")
        print(f"  Win Rate: {summary['win_rate']:.1f}%")
        print(f"  Wins/Losses: {summary.get('wins', 0)}/{summary.get('losses', 0)}")
        print(f"{'='*50}\n")


# Singleton instance
_logger = None

def get_logger() -> TradeLogger:
    """Get or create the global logger instance."""
    global _logger
    if _logger is None:
        _logger = TradeLogger()
    return _logger


# Test
if __name__ == "__main__":
    logger = get_logger()
    
    # Test signal log
    logger.log_signal(
        timeframe="15m",
        price=68000.0,
        regime="HIGH_VOL",
        delta=-2.5,
        sniper_result={
            "should_trade": True,
            "direction": "SHORT",
            "position_in_range": "TOP (85%)",
            "range_low": 67500,
            "range_high": 68200
        },
        gemini_action="GO_SHORT",
        gemini_confidence=0.85,
        gemini_reasoning="Strong bearish delta with price at range resistance",
        executed=True
    )
    
    # Test trade log
    logger.log_trade(
        timeframe="15m",
        side="SHORT",
        entry_price=68000.0,
        exit_price=67800.0,
        qty=0.001,
        pnl=0.20,
        pnl_pct=0.003,
        exit_reason="FLOW_REVERSAL_TP",
        duration_seconds=900,
        entry_delta=-2.5,
        exit_delta=1.2
    )
    
    logger.print_summary()
    print("Test logs created in /logs directory")
