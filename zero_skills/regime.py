"""RegimeDetector — classify market regime via zero API."""

from zero_skills._client import _evaluate


class RegimeDetector:
    """Detect current market regime for a coin.
    
    Regimes: trending, ranging, volatile, chaotic, mean_reverting,
             random_volatile, random_noisy, antipersistent_volatile
    """

    def classify(self, coin: str, prices: list | None = None) -> dict:
        """Classify the current market regime.
        
        Args:
            coin: Symbol (SOL, ETH, BTC, etc.)
            prices: Optional price data (unused — API handles data)
            
        Returns:
            dict with keys: regime, confidence, description
        """
        result = _evaluate(coin)
        return {
            "regime": result.regime,
            "confidence": result.confidence,
            "description": f"{coin} is in {result.regime} regime",
        }
