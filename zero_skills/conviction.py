"""ConvictionSizer — position sizing based on conviction level."""


class ConvictionSizer:
    """Size positions based on conviction and risk parameters.
    
    Conviction levels: low (0.5x), medium (1.0x), high (1.5x), max (2.0x)
    """

    def __init__(self, base_size_pct: float = 2.0, max_size_pct: float = 5.0):
        self.base_size_pct = base_size_pct
        self.max_size_pct = max_size_pct

    def size(self, equity: float, conviction: str, quality: int = 5) -> dict:
        """Calculate position size.
        
        Args:
            equity: Current account equity in USD
            conviction: Level string (low/medium/high/max)
            quality: Quality score 0-10
            
        Returns:
            dict with keys: size_usd, size_pct, leverage_suggestion
        """
        multipliers = {"low": 0.5, "medium": 1.0, "high": 1.5, "max": 2.0}
        mult = multipliers.get(conviction, 1.0)
        quality_mult = max(0.5, quality / 10)
        
        size_pct = min(self.base_size_pct * mult * quality_mult, self.max_size_pct)
        size_usd = equity * (size_pct / 100)
        
        return {
            "size_usd": round(size_usd, 2),
            "size_pct": round(size_pct, 2),
            "conviction": conviction,
            "quality_factor": round(quality_mult, 2),
            "leverage_suggestion": min(5, max(1, round(quality / 2))),
        }
