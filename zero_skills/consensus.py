"""ConsensusEngine — multi-indicator consensus for entry signals."""

from zero_skills._client import _get_client


class ConsensusEngine:
    """Evaluate multi-indicator consensus for trade entry.
    
    Uses 6 directional indicators: RSI, MACD, EMA, Bollinger, OBV, Funding.
    Consensus is weighted — not raw vote count.
    """

    def evaluate(self, coin: str) -> dict:
        """Evaluate consensus for a coin.
        
        Returns:
            dict with keys: direction (LONG/SHORT/NEUTRAL), consensus (0-1),
                           quality (0-10), verdict (would_enter/reject)
        """
        client = _get_client()
        result = client.evaluate(coin)
        return {
            "direction": result.direction,
            "consensus": result.consensus_value,
            "quality": getattr(result, "quality", 0),
            "verdict": result.verdict,
            "reasoning": result.reasoning,
        }
