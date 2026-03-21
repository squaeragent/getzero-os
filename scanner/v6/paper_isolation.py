"""Paper mode path isolation — redirects all bus/data paths to ~/.zeroos/state/.

Call apply_paper_isolation() at the start of any component's main() when PAPER_MODE=1.
This ensures paper mode state is completely isolated from the live agent.
"""

import os
from pathlib import Path


def is_paper_mode() -> bool:
    return os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes")


def apply_paper_isolation():
    """Redirect all config paths to paper-mode directories.
    
    Must be called BEFORE any component reads bus files.
    Patches scanner.v6.config module globals in-place.
    """
    if not is_paper_mode():
        return False

    import scanner.v6.config as cfg
    
    paper_bus = cfg.PAPER_BUS_DIR
    paper_data = cfg.PAPER_DATA_DIR
    
    paper_bus.mkdir(parents=True, exist_ok=True)
    paper_data.mkdir(parents=True, exist_ok=True)
    
    cfg.BUS_DIR = paper_bus
    cfg.DATA_DIR = paper_data
    cfg.POSITIONS_FILE = paper_bus / "positions.json"
    cfg.RISK_FILE = paper_bus / "risk.json"
    cfg.HEARTBEAT_FILE = paper_bus / "heartbeat.json"
    cfg.APPROVED_FILE = paper_bus / "approved.json"
    cfg.ENTRIES_FILE = paper_bus / "entries.json"
    cfg.EXITS_FILE = paper_bus / "exits.json"
    cfg.TRADES_FILE = paper_data / "trades.jsonl"
    cfg.EQUITY_HISTORY_FILE = paper_bus / "equity_history.jsonl"
    # Strategies file stays shared — paper mode reads the same signal data
    # cfg.STRATEGIES_FILE stays unchanged
    
    return True
