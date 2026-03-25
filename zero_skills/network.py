"""NetworkClient — collective intelligence sync."""

from zero_skills._client import _get_client


class NetworkClient:
    """Connect to the zero collective intelligence network.
    
    Report trades and decisions. Receive network weights and signals.
    """

    def report_trade(self, coin: str, direction: str, entry_price: float,
                     exit_price: float, pnl_pct: float) -> dict:
        """Report a completed trade to the network.
        
        Earns +5 credits per trade reported.
        """
        client = _get_client()
        try:
            resp = client._post("/api/agents/trade", {
                "coin": coin,
                "direction": direction,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
            })
            return {"accepted": True, "credits_earned": 5}
        except Exception as e:
            return {"accepted": False, "error": str(e)}

    def report_decision(self, coin: str, action: str, regime: str) -> dict:
        """Report a decision (enter/reject/exit) to the network.
        
        Earns +50 per 100 decisions reported.
        """
        client = _get_client()
        try:
            client._post("/api/agents/decision", {
                "coin": coin,
                "action": action,
                "regime": regime,
            })
            return {"accepted": True}
        except Exception as e:
            return {"accepted": False, "error": str(e)}

    def get_intelligence(self) -> dict:
        """Get collective network intelligence."""
        client = _get_client()
        try:
            return client._get("/api/network/intelligence")
        except Exception as e:
            return {"error": str(e)}

    def sync(self) -> dict:
        """Full sync — get latest network state."""
        return self.get_intelligence()
