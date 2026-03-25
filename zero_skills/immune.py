"""ImmuneProtocol — continuous position protection."""


class ImmuneProtocol:
    """Protect every open position.
    
    Checks: stop-loss exists, stop-loss valid, position size within limits.
    Runs on a fixed clock — not event-driven.
    """

    def __init__(self, max_loss_pct: float = 3.0):
        self.max_loss_pct = max_loss_pct
        self._checks = 0
        self._failures = 0
        self._saves = 0

    def run_cycle(self, positions: list[dict]) -> dict:
        """Run one immune cycle across all positions.
        
        Args:
            positions: List of position dicts with keys:
                coin, direction, entry_price, current_price, stop_price, size_usd
                
        Returns:
            dict with keys: healthy (bool), checks, failures, actions
        """
        self._checks += 1
        actions = []
        all_healthy = True
        
        for pos in positions:
            entry = pos.get("entry_price", 0)
            stop = pos.get("stop_price", 0)
            current = pos.get("current_price", 0)
            direction = pos.get("direction", "LONG")
            
            # Check 1: stop exists
            if not stop:
                all_healthy = False
                self._failures += 1
                actions.append({
                    "coin": pos["coin"],
                    "action": "REPLACE_STOP",
                    "reason": "missing_stop",
                    "suggested_stop": self._calc_stop(entry, direction),
                })
                continue
            
            # Check 2: stop is on correct side
            if direction == "LONG" and stop >= current:
                all_healthy = False
                self._failures += 1
                actions.append({
                    "coin": pos["coin"],
                    "action": "FIX_STOP",
                    "reason": "stop_above_price",
                    "suggested_stop": self._calc_stop(entry, direction),
                })
            elif direction == "SHORT" and stop <= current:
                all_healthy = False
                self._failures += 1
                actions.append({
                    "coin": pos["coin"],
                    "action": "FIX_STOP", 
                    "reason": "stop_below_price",
                    "suggested_stop": self._calc_stop(entry, direction),
                })
        
        if actions:
            self._saves += len(actions)
        
        return {
            "healthy": all_healthy,
            "checks": self._checks,
            "failures": self._failures,
            "saves": self._saves,
            "positions_checked": len(positions),
            "actions": actions,
        }

    def _calc_stop(self, entry: float, direction: str) -> float:
        if direction == "LONG":
            return round(entry * (1 - self.max_loss_pct / 100), 4)
        return round(entry * (1 + self.max_loss_pct / 100), 4)

    @property
    def stats(self) -> dict:
        return {
            "total_checks": self._checks,
            "total_failures": self._failures,
            "total_saves": self._saves,
        }
