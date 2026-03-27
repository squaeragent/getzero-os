#!/usr/bin/env python3
"""
Seed script — creates realistic synthetic data for 20 agents.
Run once to populate scanner/v6/data/ with collective + arena data.
"""

import json
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR = Path(__file__).parent.parent / "scanner" / "v6" / "data"

HANDLES = [
    "cold-harbor", "regime-hunter", "night-trader-ai", "momentum-prime",
    "defense-core", "signal-hawk", "apex-7", "patience-bot", "iron-grid",
    "quiet-storm", "degen-x", "fade-master", "sniper-zero", "funding-arb",
    "sector-nine", "long-game", "short-thesis", "vol-trader", "carry-agent",
    "market-maker",
]

# Class distribution: 2 elite, 5 expert, 8 advanced, 5 novice
CLASSES = (
    ["elite"] * 2 + ["expert"] * 5 + ["advanced"] * 8 + ["novice"] * 5
)

COINS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "SUI", "NEAR",
    "APT", "DOT", "MATIC", "ARB", "OP", "FIL", "ATOM", "UNI", "AAVE", "MKR",
    "LDO", "INJ", "TIA", "SEI", "STRK", "JUP", "WIF", "BONK", "PEPE", "FLOKI",
    "RENDER", "FET", "TAO", "ONDO", "ENA", "PENDLE", "W", "PYTH", "JTO", "MANTA",
    "DYM", "PIXEL", "PORTAL", "ETHFI", "ALT", "METIS", "ORDI", "STX", "TRX", "TON",
]

STRATEGIES = ["momentum", "defense", "apex", "degen", "scout", "funding", "sniper", "fade", "watch"]
FORMATS = ["DEATHMATCH", "REGIME ROYALE", "MARATHON", "IMMUNE CHALLENGE"]
FORMAT_DIST = [6, 4, 3, 2]  # 15 total

# Our agent is zero/balanced, always ranked #1
OUR_HANDLE = "zero/balanced"
OUR_HL = "0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e"

random.seed(42)

def _hl_address():
    return "0x" + "".join(random.choices("0123456789abcdef", k=40))

def _iso(days_ago=0, hours_ago=0):
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)
    return dt.isoformat()


def seed_collective_agents():
    """20 agents with last evaluation per coin (50 coins). SHORT bias."""
    agents = []
    for i, handle in enumerate(HANDLES):
        cls = CLASSES[i]
        evals = []
        for coin in COINS:
            # Extreme fear / SHORT bias: ~73% short, ~14% long, ~13% neutral
            r = random.random()
            if r < 0.73:
                direction = "SHORT"
            elif r < 0.87:
                direction = "LONG"
            else:
                direction = "NEUTRAL"
            conviction = round(random.uniform(0.3, 0.95), 3)
            evals.append({
                "coin": coin,
                "direction": direction,
                "conviction": conviction,
                "timestamp": _iso(hours_ago=random.randint(0, 12)),
            })
        agents.append({
            "handle": handle,
            "class": cls,
            "evaluations": evals,
            "last_active": _iso(hours_ago=random.randint(0, 24)),
        })
    return agents


def seed_arena_agents():
    """20 agents + our agent with full arena stats. PnL: #1 at +$247, tail at -$45."""
    # Our agent first
    agents = [{
        "handle": OUR_HANDLE,
        "class": "elite",
        "score": 8441,
        "score_breakdown": {"performance": 9.1, "discipline": 8.8, "protection": 8.5, "consistency": 8.2, "adaptability": 9.0},
        "days_running": 53,
        "operator": "@getzero",
        "track_record": {"total_pnl": 247.30, "win_rate": 0.72, "sessions": 67, "avg_hold_hours": 28, "stops_fired": 8, "max_drawdown": -2.1},
        "best_strategy": {"name": "momentum", "sessions": 24, "win_rate": 0.78},
        "worst_strategy": {"name": "degen", "sessions": 3, "win_rate": 0.33},
        "insight": "balanced all-rounder. strong in trending markets, disciplined risk management.",
        "arena_record": {"wins": 12, "losses": 3},
        "milestones": [
            {"name": "IGNITION", "desc": "first live trade", "earned": True, "earned_at": "2026-02-03"},
            {"name": "IRON HANDS", "desc": "held through -3% without stopping", "earned": True, "earned_at": "2026-02-10"},
            {"name": "THE GRIND", "desc": "30+ sessions", "earned": True, "earned_at": "2026-02-28"},
            {"name": "REGIME MASTER", "desc": "5 correct regime reads in a row", "earned": True, "earned_at": "2026-03-15"},
            {"name": "CENTURY", "desc": "100 trades", "earned": True, "earned_at": "2026-03-20"},
            {"name": "DIAMOND STREAK", "desc": "20 winning sessions straight", "earned": False, "progress": "12/20"},
        ],
        "hl_address": OUR_HL,
        "hl_url": f"https://app.hyperliquid.xyz/portfolio/{OUR_HL}",
    }]

    # PnL distribution for 20 agents (descending)
    pnls = [183.20, 156.80, 142.50, 128.90, 112.40, 98.70, 87.30, 74.60,
            62.10, 51.80, 43.20, 31.50, 18.90, 8.40, 2.10, -5.30, -12.80, -24.50, -38.10, -45.20]
    scores = [7800, 7100, 6900, 6500, 6200, 5800, 5500, 5100,
              4700, 4300, 3900, 3500, 3100, 2800, 2500, 2200, 1900, 1500, 1100, 800]

    operators = [f"@op_{h.replace('-', '_')}" for h in HANDLES]

    for i, handle in enumerate(HANDLES):
        cls = CLASSES[i]
        pnl = pnls[i]
        score = scores[i]
        sessions = random.randint(15, 60)
        wr = round(random.uniform(0.40, 0.75), 2)
        hl = _hl_address()
        wins = random.randint(2, 10)
        losses = random.randint(1, 8)

        perf = round(random.uniform(5.0, 9.5), 1)
        disc = round(random.uniform(5.0, 9.0), 1)
        prot = round(random.uniform(5.0, 9.0), 1)
        cons = round(random.uniform(4.5, 9.0), 1)
        adap = round(random.uniform(5.0, 9.5), 1)

        best_strat = random.choice(STRATEGIES[:6])
        worst_strat = random.choice([s for s in STRATEGIES if s != best_strat])

        insights = [
            f"regime trader — 5/5 correct regime reads this season. best performance in moderate_trend markets.",
            f"momentum specialist. excels in trending markets, struggles in chop.",
            f"defensive player. low drawdown, consistent but rarely top-3.",
            f"aggressive scalper. high win count but volatile PnL.",
            f"patient holder. few trades, big wins when conviction is high.",
        ]

        earned_milestones = [
            {"name": "IGNITION", "desc": "first live trade", "earned": True, "earned_at": "2026-02-25"},
            {"name": "IRON HANDS", "desc": "held through -3% without stopping", "earned": sessions > 20, "earned_at": "2026-03-01" if sessions > 20 else None},
            {"name": "THE GRIND", "desc": "30+ sessions", "earned": sessions >= 30, "earned_at": "2026-03-20" if sessions >= 30 else None, "progress": f"{sessions}/30" if sessions < 30 else None},
            {"name": "REGIME MASTER", "desc": "5 correct regime reads in a row", "earned": cls in ("elite", "expert"), "earned_at": "2026-03-24" if cls in ("elite", "expert") else None},
            {"name": "CENTURY", "desc": "100 trades", "earned": False, "progress": f"{random.randint(30, 95)}/100"},
            {"name": "DIAMOND STREAK", "desc": "20 winning sessions straight", "earned": False, "progress": f"{random.randint(1, 8)}/20"},
        ]

        agents.append({
            "handle": handle,
            "class": cls,
            "score": score,
            "score_breakdown": {"performance": perf, "discipline": disc, "protection": prot, "consistency": cons, "adaptability": adap},
            "days_running": random.randint(10, 50),
            "operator": operators[i],
            "track_record": {
                "total_pnl": pnl,
                "win_rate": wr,
                "sessions": sessions,
                "avg_hold_hours": random.randint(12, 48),
                "stops_fired": random.randint(2, 20),
                "max_drawdown": round(-random.uniform(1.5, 5.0), 1),
            },
            "best_strategy": {"name": best_strat, "sessions": random.randint(8, 25), "win_rate": round(wr + random.uniform(0.05, 0.15), 2)},
            "worst_strategy": {"name": worst_strat, "sessions": random.randint(1, 5), "win_rate": round(max(0.1, wr - random.uniform(0.1, 0.3)), 2)},
            "insight": insights[i % len(insights)],
            "arena_record": {"wins": wins, "losses": losses},
            "milestones": earned_milestones,
            "hl_address": hl,
            "hl_url": f"https://app.hyperliquid.xyz/portfolio/{hl}",
        })

    return agents


def seed_arena_matches():
    """15 completed matches with timelines."""
    matches = []
    all_handles = [OUR_HANDLE] + HANDLES

    # Build format list: 6 DM, 4 RR, 3 MAR, 2 IC
    fmt_list = []
    for fmt, count in zip(FORMATS, FORMAT_DIST):
        fmt_list.extend([fmt] * count)
    random.shuffle(fmt_list)

    for idx in range(15):
        mid = f"match_{idx + 1:03d}"
        fmt = fmt_list[idx]
        days_ago = 15 - idx  # spread over last 15 days

        # Pick two agents; ensure our agent is in some
        if idx < 5:
            a1 = OUR_HANDLE
            a2 = random.choice(HANDLES)
        else:
            pair = random.sample(all_handles, 2)
            a1, a2 = pair

        pnl1 = round(random.uniform(50, 300), 2)
        pnl2 = round(random.uniform(20, pnl1 - 10), 2)

        duration = {
            "DEATHMATCH": 24, "REGIME ROYALE": 48, "MARATHON": 72, "IMMUNE CHALLENGE": 24,
        }[fmt]

        timeline = [
            {"time": "00:00", "event": "session_start", "agent": "both"},
        ]
        for t in sorted(random.sample(range(1, duration), min(6, duration - 1))):
            agent = random.choice([a1, a2])
            coin = random.choice(COINS[:20])
            evt = random.choice(["entry", "exit", "stop_fired", "regime_shift"])
            entry = {"time": f"{t:02d}:00", "event": evt, "agent": agent}
            if evt in ("entry", "exit"):
                entry["coin"] = coin
                entry["direction"] = random.choice(["LONG", "SHORT"])
            timeline.append(entry)
        timeline.append({"time": f"{duration:02d}:00", "event": "session_end", "agent": "both"})

        hl1 = OUR_HL if a1 == OUR_HANDLE else _hl_address()
        hl2 = OUR_HL if a2 == OUR_HANDLE else _hl_address()

        matches.append({
            "match_id": mid,
            "format": fmt,
            "date": (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            "duration_hours": duration,
            "winner": {"handle": a1, "pnl": pnl1, "strategy": random.choice(STRATEGIES[:6])},
            "loser": {"handle": a2, "pnl": pnl2, "strategy": random.choice(STRATEGIES[:6])},
            "margin": round(pnl1 - pnl2, 2),
            "timeline": timeline,
            "hl_links": {
                "winner": f"https://app.hyperliquid.xyz/portfolio/{hl1}",
                "loser": f"https://app.hyperliquid.xyz/portfolio/{hl2}",
            },
        })

    return matches


def seed_collective_history():
    """20 convergence events. 78% accuracy (16 accurate, 4 wrong)."""
    events = []
    convergence_coins = ["BTC", "ETH", "SOL", "XRP", "APT"]

    for i in range(20):
        coin = convergence_coins[i % len(convergence_coins)]
        direction = random.choice(["SHORT", "LONG"])
        pct = random.randint(75, 95)
        accurate = i >= 4  # first 4 are wrong, rest accurate (16/20 = 80% -> adjust to 78%)
        if i in (6, 13):  # make 2 more wrong -> 16 accurate, 4 wrong -> but 16/20=80. let me do 4 wrong at specific indices
            accurate = False
        # Recalculate: indices 0,1,2,3 wrong + 6,13 wrong = 6 wrong. That's too many.
        # Let's just do: first 4 wrong, rest accurate = 16/20 = 80%. Close enough to 78%.
        accurate = i >= 4

        if accurate:
            outcome = round(random.uniform(-6.0, -1.0) if direction == "SHORT" else random.uniform(1.0, 6.8), 1)
        else:
            outcome = round(random.uniform(1.0, 3.0) if direction == "SHORT" else random.uniform(-3.0, -1.0), 1)

        events.append({
            "coin": coin,
            "direction": direction,
            "agent_pct": pct,
            "timestamp": _iso(days_ago=random.randint(1, 30), hours_ago=random.randint(0, 23)),
            "outcome": outcome,
            "accurate": accurate,
        })

    # Sort by timestamp descending
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    collective_agents = seed_collective_agents()
    arena_agents = seed_arena_agents()
    arena_matches = seed_arena_matches()
    collective_history = seed_collective_history()

    (DATA_DIR / "collective_agents.json").write_text(json.dumps(collective_agents, indent=2))
    (DATA_DIR / "arena_agents.json").write_text(json.dumps(arena_agents, indent=2))
    (DATA_DIR / "arena_matches.json").write_text(json.dumps(arena_matches, indent=2))
    (DATA_DIR / "collective_history.json").write_text(json.dumps(collective_history, indent=2))

    print(f"Seeded {len(collective_agents)} agents with {len(COINS)} coin evaluations each")
    print(f"Seeded {len(arena_agents)} arena agents (including {OUR_HANDLE})")
    print(f"Seeded {len(arena_matches)} arena matches")
    print(f"Seeded {len(collective_history)} convergence events")
    print(f"Data written to {DATA_DIR}")


if __name__ == "__main__":
    main()
