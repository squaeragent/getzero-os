"""ExitIntelligence — exit signal detection."""

from zero_skills._client import _get_client


class ExitIntelligence:
    """Detect exit signals for open positions.
    
    Exit triggers: regime shift, consensus flip, time decay, stop hit.
    """

    def should_exit(self, coin: str, entry_direction: str, entry_price: float,
                    current_price: float, hours_held: float = 0) -> dict:
        """Check if position should be exited.
        
        Args:
            coin: Symbol
            entry_direction: LONG or SHORT
            entry_price: Entry price
            current_price: Current price
            hours_held: Hours position has been held
            
        Returns:
            dict with keys: exit (bool), reason, pnl_pct
        """
        client = _get_client()
        result = client.evaluate(coin)
        
        if entry_direction == "LONG":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100
        
        exit_signal = False
        reason = "hold"
        
        # Consensus flipped
        if result.direction != "NEUTRAL" and result.direction != entry_direction:
            exit_signal = True
            reason = f"consensus_flip_{result.direction}"
        
        # Time decay (>48h with low quality)
        if hours_held > 48 and getattr(result, "quality", 5) < 5:
            exit_signal = True
            reason = "time_decay"
        
        return {
            "exit": exit_signal,
            "reason": reason,
            "pnl_pct": round(pnl_pct, 2),
            "current_regime": result.regime,
            "current_direction": result.direction,
        }
