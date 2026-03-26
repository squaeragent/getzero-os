"""
collective.py — Collective Intelligence v1

Computes 3 signals from the simulator agent network:
1. Consensus Direction — LONG/SHORT/NEUTRAL conviction per coin
2. Rejection Consensus — what % of agents rejected each coin
3. Regime Agreement — regime classification agreement per coin

Data source: ~/.zeroos/sim/{handle}/state.json (primary)
             ~/.zeroos/sim/{handle}/session.json (fallback)
Output:      scanner/v6/bus/collective_signals.json
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SIM_DIR = Path.home() / ".zeroos" / "sim"
BUS_DIR = Path(__file__).resolve().parent / "bus"
OUTPUT = BUS_DIR / "collective_signals.json"

_quiet = False

# All coins the system tracks
ALL_COINS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
    "LINK", "DOT", "LTC", "UNI", "NEAR", "SUI", "OP", "SEI",
    "INJ", "TIA", "AAVE", "ONDO", "TRUMP", "ZEC",
]


def _log(msg: str):
    if _quiet:
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [collective] [{ts}] {msg}", flush=True)


def _load_agent_state(handle: str) -> dict | None:
    """Load agent state, trying state.json first then session.json."""
    agent_dir = SIM_DIR / handle

    # Primary: state.json (spec format)
    state_file = agent_dir / "state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: session.json (existing format)
    session_file = agent_dir / "session.json"
    if session_file.exists():
        try:
            return _convert_session(json.loads(session_file.read_text()))
        except (json.JSONDecodeError, OSError):
            pass

    return None


def _convert_session(session: dict) -> dict:
    """Convert session.json format to the state.json format we expect."""
    trades = session.get("trades", [])
    closes = [t for t in trades if t.get("action") == "close"]

    # Derive last_evaluations from closed trades
    # Group by coin, take the most recent close as the "verdict"
    coin_last: dict[str, dict] = {}
    for t in closes:
        coin = t["coin"]
        if coin not in coin_last or t["ts"] > coin_last[coin]["ts"]:
            coin_last[coin] = t

    evaluations = []
    for coin, t in coin_last.items():
        direction = t.get("direction", "NEUTRAL")
        pnl = t.get("pnl", 0)
        # Derive verdict: if agent closed profitably, they validated direction
        # If stopped out, verdict depends on whether they'd re-enter
        if pnl > 0:
            verdict = direction
        elif t.get("reason") == "stop_loss":
            verdict = "NEUTRAL"  # stopped out = uncertain
        else:
            verdict = direction  # take_profit or manual = conviction holds

        evaluations.append({
            "coin": coin,
            "verdict": verdict,
            "quality": "good" if pnl > 0 else "poor",
            "regime": _guess_regime_from_trade(t),
            "timestamp": t["ts"],
        })

    # Also account for open positions as active verdicts
    for pos in session.get("open_positions", []):
        # open_positions can be a list of strings (coin names) or dicts
        if isinstance(pos, str):
            coin = pos
            direction = "LONG"
        else:
            coin = pos.get("coin", pos.get("symbol", ""))
            direction = pos.get("direction", "LONG")
        if coin:
            evaluations.append({
                "coin": coin,
                "verdict": direction,
                "quality": "active",
                "regime": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return {
        "current_session": {"strategy": session.get("strategy", "unknown")},
        "last_evaluations": evaluations,
    }


def _guess_regime_from_trade(trade: dict) -> str | None:
    """Heuristic regime guess from trade outcome."""
    pnl = trade.get("pnl", 0)
    reason = trade.get("reason", "")
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("price", 0)

    if entry == 0:
        return None

    move_pct = abs(exit_p - entry) / entry * 100 if entry else 0

    if reason == "stop_loss" and move_pct < 0.3:
        return "stable"  # tight stop in flat market
    elif reason == "stop_loss" and move_pct > 1.5:
        return "chaotic"  # big adverse move
    elif pnl > 0 and move_pct > 1.0:
        return "trending"  # profitable trend capture
    elif pnl > 0 and move_pct < 0.5:
        return "reverting"  # small mean-reversion win
    return None


def _discover_agents() -> list[str]:
    """Find all sim agent handles."""
    if not SIM_DIR.exists():
        return []
    return sorted(
        d.name for d in SIM_DIR.iterdir()
        if d.is_dir() and d.name.startswith("zr_")
    )


def compute() -> dict:
    """Compute all 3 collective intelligence signals."""
    handles = _discover_agents()
    if not handles:
        _log("no sim agents found")
        return _empty_result()

    # Collect all evaluations per agent
    agent_evals: dict[str, list[dict]] = {}
    agent_count = 0

    for handle in handles:
        state = _load_agent_state(handle)
        if state is None:
            continue
        agent_count += 1
        evals = state.get("last_evaluations", [])
        if not evals:
            cs = state.get("current_session", {})
            evals = cs.get("last_evaluations", [])
        agent_evals[handle] = evals

    if agent_count == 0:
        _log("no agent state files found")
        return _empty_result()

    # --- SIGNAL 1: Consensus Direction ---
    consensus = _compute_consensus(agent_evals, agent_count)

    # --- SIGNAL 2: Rejection Consensus ---
    rejection = _compute_rejection(agent_evals, agent_count)

    # --- SIGNAL 3: Regime Agreement ---
    regime_agreement = _compute_regime_agreement(agent_evals)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_count": agent_count,
        "consensus": consensus,
        "rejection": rejection,
        "regime_agreement": regime_agreement,
    }

    # Write to bus
    try:
        BUS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(result, indent=2))
        _log(f"wrote collective_signals.json ({agent_count} agents, {len(consensus)} coins)")
    except OSError as e:
        _log(f"failed to write output: {e}")

    return result


def _compute_consensus(agent_evals: dict[str, list[dict]], agent_count: int) -> dict:
    """Signal 1: For each coin, count LONG/SHORT/NEUTRAL verdicts."""
    coin_votes: dict[str, dict[str, int]] = defaultdict(lambda: {"LONG": 0, "SHORT": 0, "NEUTRAL": 0})

    for handle, evals in agent_evals.items():
        # Get latest eval per coin for this agent
        latest: dict[str, dict] = {}
        for ev in evals:
            coin = ev.get("coin", "")
            if not coin:
                continue
            ts = ev.get("timestamp", "")
            if coin not in latest or ts > latest[coin].get("timestamp", ""):
                latest[coin] = ev

        for coin, ev in latest.items():
            verdict = ev.get("verdict", "NEUTRAL").upper()
            if verdict not in ("LONG", "SHORT", "NEUTRAL"):
                verdict = "NEUTRAL"
            coin_votes[coin][verdict] += 1

    consensus = {}
    for coin in sorted(coin_votes.keys()):
        votes = coin_votes[coin]
        total = sum(votes.values())
        if total == 0:
            continue

        long_pct = round(votes["LONG"] / total * 100)
        short_pct = round(votes["SHORT"] / total * 100)
        neutral_pct = round(votes["NEUTRAL"] / total * 100)

        if long_pct > 60:
            signal = "strong_long"
        elif short_pct > 60:
            signal = "strong_short"
        elif neutral_pct > 70:
            signal = "no_trade"
        else:
            signal = "mixed"

        consensus[coin] = {
            "long_pct": long_pct,
            "short_pct": short_pct,
            "neutral_pct": neutral_pct,
            "signal": signal,
        }

    return consensus


def _compute_rejection(agent_evals: dict[str, list[dict]], agent_count: int) -> dict:
    """Signal 2: For each coin, what % of agents rejected (never traded) it."""
    coin_traders: dict[str, int] = defaultdict(int)

    for handle, evals in agent_evals.items():
        traded_coins = set(ev.get("coin", "") for ev in evals if ev.get("coin"))
        for coin in traded_coins:
            coin_traders[coin] += 1

    # Consider all coins that any agent traded
    all_coins = set(coin_traders.keys()) | set(ALL_COINS)
    rejection = {}

    for coin in sorted(all_coins):
        traders = coin_traders.get(coin, 0)
        rate = round((1 - traders / agent_count) * 100) if agent_count > 0 else 100

        if rate > 90:
            signal = "avoid"
        elif rate > 70:
            signal = "low_interest"
        elif rate < 50:
            signal = "tradeable"
        else:
            signal = "mixed"

        rejection[coin] = {"rate": rate, "signal": signal}

    return rejection


def _compute_regime_agreement(agent_evals: dict[str, list[dict]]) -> dict:
    """Signal 3: For each coin, what regime each agent classified it as."""
    coin_regimes: dict[str, dict[str, int]] = defaultdict(
        lambda: {"trending": 0, "stable": 0, "chaotic": 0, "reverting": 0}
    )

    for handle, evals in agent_evals.items():
        # Latest regime per coin per agent
        latest: dict[str, dict] = {}
        for ev in evals:
            coin = ev.get("coin", "")
            regime = ev.get("regime")
            if not coin or not regime:
                continue
            ts = ev.get("timestamp", "")
            if coin not in latest or ts > latest[coin].get("timestamp", ""):
                latest[coin] = ev

        for coin, ev in latest.items():
            regime = ev.get("regime", "").lower()
            if regime in coin_regimes[coin]:
                coin_regimes[coin][regime] += 1

    regime_agreement = {}
    for coin in sorted(coin_regimes.keys()):
        counts = coin_regimes[coin]
        total = sum(counts.values())
        if total == 0:
            continue

        dominant = max(counts, key=counts.get)
        agreement_pct = round(counts[dominant] / total * 100)

        if agreement_pct > 70:
            confidence = "high"
        elif agreement_pct > 50:
            confidence = "medium"
        else:
            confidence = "low"

        regime_agreement[coin] = {
            "dominant": dominant,
            "agreement_pct": agreement_pct,
            "confidence": confidence,
            "trending_pct": round(counts["trending"] / total * 100),
            "stable_pct": round(counts["stable"] / total * 100),
            "chaotic_pct": round(counts["chaotic"] / total * 100),
            "reverting_pct": round(counts["reverting"] / total * 100),
        }

    return regime_agreement


def _empty_result() -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_count": 0,
        "consensus": {},
        "rejection": {},
        "regime_agreement": {},
    }


def main():
    global _quiet
    json_only = "--json" in sys.argv
    _quiet = json_only

    if not json_only:
        _log("computing collective intelligence signals...")

    result = compute()

    if json_only:
        print(json.dumps(result, indent=2))
        return

    # Pretty print
    ac = result["agent_count"]
    print(f"\n  Collective Intelligence — {ac} agents\n")

    consensus = result["consensus"]
    if consensus:
        print("  CONSENSUS DIRECTION")
        print("  " + "-" * 60)
        for coin, data in sorted(consensus.items(), key=lambda x: x[0]):
            bar = f"L:{data['long_pct']:>3}%  S:{data['short_pct']:>3}%  N:{data['neutral_pct']:>3}%"
            sig = data["signal"].upper().replace("_", " ")
            print(f"    {coin:<8} {bar}  [{sig}]")
        print()

    rejection = result["rejection"]
    if rejection:
        # Only show interesting ones
        notable = {k: v for k, v in rejection.items() if v["signal"] in ("avoid", "tradeable")}
        if notable:
            print("  REJECTION CONSENSUS")
            print("  " + "-" * 60)
            for coin, data in sorted(notable.items(), key=lambda x: -x[1]["rate"]):
                sig = data["signal"].upper()
                print(f"    {coin:<8} rejection: {data['rate']:>3}%  [{sig}]")
            print()

    regime = result["regime_agreement"]
    if regime:
        print("  REGIME AGREEMENT")
        print("  " + "-" * 60)
        for coin, data in sorted(regime.items(), key=lambda x: -x[1]["agreement_pct"]):
            conf = data["confidence"].upper()
            print(f"    {coin:<8} {data['dominant']:<10} agreement: {data['agreement_pct']:>3}%  [{conf}]")
        print()


if __name__ == "__main__":
    main()
