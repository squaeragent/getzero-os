# zero-skills

Trading capabilities for AI agents. Regime detection, consensus engine, immune protection, collective intelligence.

## Install

```bash
pip install zero-skills
```

## Quick Start

```python
from zero_skills import RegimeDetector, ConsensusEngine, ImmuneProtocol, NetworkClient

# Classify market regime
regime = RegimeDetector().classify("SOL")
# → {"regime": "trending", "confidence": 0.82}

# Evaluate entry consensus
consensus = ConsensusEngine().evaluate("SOL")
# → {"direction": "LONG", "consensus": 0.73, "quality": 7, "verdict": "would_enter"}

# Protect positions
immune = ImmuneProtocol(max_loss_pct=3.0)
result = immune.run_cycle([
    {"coin": "SOL", "direction": "LONG", "entry_price": 140.0, "current_price": 142.5, "stop_price": 135.8}
])
# → {"healthy": True, "checks": 1, "failures": 0, "saves": 0}

# Report to collective
network = NetworkClient()
network.report_trade("SOL", "LONG", 140.0, 145.0, 3.57)
# → {"accepted": True, "credits_earned": 5}
```

## Skills

| Skill | What it does |
|---|---|
| `RegimeDetector` | Classify market regime (trending, ranging, volatile, chaotic) |
| `ConsensusEngine` | Multi-indicator consensus for entry signals (6 indicators) |
| `ConvictionSizer` | Position sizing based on conviction and quality |
| `ExitIntelligence` | Exit signal detection (regime shift, consensus flip, time decay) |
| `ImmuneProtocol` | Continuous position protection (stop verification, replacement) |
| `NetworkClient` | Collective intelligence sync (report trades, receive network weights) |

## Authentication

```bash
zeroos init --token YOUR_TOKEN
```

Or set the environment variable:

```bash
export ZEROOS_TOKEN=your_token
```

## API

Each evaluation costs 1 credit. Trades earn +5 credits.

- Docs: https://getzero.dev/docs/api
- Dashboard: https://app.getzero.dev

## License

MIT — Zero Intelligence Ltd
