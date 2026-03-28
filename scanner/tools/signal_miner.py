#!/usr/bin/env python3
"""
Signal Miner — Harvest signals from NVArena API.

Phases:
  1. MINE:     Open signal packs for target coins (1-5 credits each)
  2. FILTER:   Keep only signals above Sharpe/WR thresholds
  3. ASSEMBLE: Run tournament assembly for filtered signals (3 credits each)
  4. COMPARE:  Compare assembled Sharpe against current strategies
  5. REPORT:   Output upgrade recommendations

Usage:
  python3 signal_miner.py mine --coins BTC,ETH,SOL --packs 3 --type common
  python3 signal_miner.py mine --coins ALL --packs 2 --type rare
  python3 signal_miner.py filter --min-sharpe 1.5 --min-wr 50
  python3 signal_miner.py assemble --coins BTC,SOL
  python3 signal_miner.py portfolio-check
  python3 signal_miner.py report
  python3 signal_miner.py full --coins BTC,ETH,SOL --packs 5 --budget 200

Cost: common=1cr, rare=2cr, trump=5cr per pack (10 signals each)
      assemble=3cr per coin, portfolio=0cr
"""

import json
import os
import sys
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://arena.nvprotocol.com"
MINE_DIR = Path(__file__).parent.parent / "data" / "signal_mine"
SIGNALS_DIR = MINE_DIR / "signals"
ASSEMBLED_DIR = MINE_DIR / "assembled"
REPORTS_DIR = MINE_DIR / "reports"

# Thresholds for keeping signals
MIN_SHARPE = 1.0
MIN_WIN_RATE = 40.0
MIN_TRADES = 5
MAX_DRAWDOWN = 25.0

# Budget safety
MAX_CREDITS_PER_RUN = 500

def get_api_key():
    key = os.environ.get("ENVY_API_KEY")
    if not key:
        # Try loading from .env
        env_file = Path.home() / "getzero-os" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ENVY_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key


def api_get(path, api_key=None):
    """GET request to NVArena API."""
    import urllib.request
    headers = {}
    if api_key:
        headers["X-API-KEY"] = api_key
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ct = resp.headers.get("Content-Type", "")
            body = resp.read().decode()
            if "yaml" in ct:
                return {"_yaml": body}
            return json.loads(body)
    except Exception as e:
        return {"error": str(e)}


def api_post_yaml(path, yaml_body, api_key=None):
    """POST YAML to NVArena API."""
    import urllib.request
    headers = {"Content-Type": "text/yaml"}
    if api_key:
        headers["X-API-KEY"] = api_key
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, data=yaml_body.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ct = resp.headers.get("Content-Type", "")
            body = resp.read().decode()
            if "yaml" in ct:
                return {"_yaml": body}
            return json.loads(body)
    except Exception as e:
        return {"error": str(e)}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def ensure_dirs():
    for d in [SIGNALS_DIR, ASSEMBLED_DIR, REPORTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def get_credits(api_key):
    data = api_get("/api/claw/subscription/status", api_key)
    return data.get("credits", 0)


def get_all_coins(api_key):
    data = api_get("/api/claw/coins", api_key)
    return [c["symbol"] for c in data.get("coins", [])]


# ─── PHASE 1: MINE ────────────────────────────────────────────────────────────

def mine_packs(coins, num_packs=3, pack_type="common", api_key=None, budget=None):
    """Open signal packs for given coins. Returns total signals mined."""
    credit_cost = {"common": 1, "rare": 2, "trump": 5}[pack_type]
    total_cost = len(coins) * num_packs * credit_cost

    credits = get_credits(api_key)
    log(f"Credits available: {credits:,.0f}")
    log(f"Estimated cost: {total_cost} credits ({len(coins)} coins × {num_packs} packs × {credit_cost} cr)")

    if budget and total_cost > budget:
        log(f"⚠ Over budget ({budget} cr). Reducing packs.")
        num_packs = max(1, budget // (len(coins) * credit_cost))
        total_cost = len(coins) * num_packs * credit_cost
        log(f"Adjusted: {num_packs} packs per coin, {total_cost} credits")

    if total_cost > MAX_CREDITS_PER_RUN:
        log(f"⚠ Exceeds safety limit ({MAX_CREDITS_PER_RUN} cr). Use --budget to override.")
        return 0

    total_signals = 0
    total_spent = 0

    for coin in coins:
        coin_signals = []
        for pack_num in range(num_packs):
            log(f"  Opening {pack_type} pack #{pack_num+1} for {coin}...")
            data = api_get(f"/api/claw/paid/signals/pack/{pack_type}?coin={coin}", api_key)

            if "error" in data:
                log(f"    ERROR: {data['error']}")
                time.sleep(2)
                continue

            yaml_text = data.get("_yaml", "")
            if not yaml_text:
                log(f"    No YAML response")
                continue

            # Parse signals from YAML
            try:
                parsed = yaml.safe_load(yaml_text)
                signals = parsed.get("signals", [])
                for sig in signals:
                    sig["_coin"] = coin
                    sig["_pack_type"] = pack_type
                    sig["_mined_at"] = datetime.now(timezone.utc).isoformat()
                coin_signals.extend(signals)
                total_spent += credit_cost
                log(f"    Got {len(signals)} signals (spent {total_spent} cr so far)")
            except Exception as e:
                log(f"    Parse error: {e}")
                # Save raw YAML anyway
                raw_file = SIGNALS_DIR / f"{coin}_{pack_type}_raw_{pack_num}.yaml"
                raw_file.write_text(yaml_text)

            time.sleep(0.5)  # Rate limit respect

        if coin_signals:
            # Save all signals for this coin
            out_file = SIGNALS_DIR / f"{coin}_{pack_type}_{len(coin_signals)}sigs.json"
            with open(out_file, "w") as f:
                json.dump({"coin": coin, "pack_type": pack_type,
                          "count": len(coin_signals), "signals": coin_signals,
                          "mined_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
            total_signals += len(coin_signals)
            log(f"  {coin}: {len(coin_signals)} signals saved → {out_file.name}")

    log(f"\nMining complete: {total_signals} signals from {len(coins)} coins, {total_spent} credits spent")
    return total_signals


# ─── PHASE 2: FILTER ──────────────────────────────────────────────────────────

def filter_signals(min_sharpe=MIN_SHARPE, min_wr=MIN_WIN_RATE, min_trades=MIN_TRADES, max_dd=MAX_DRAWDOWN):
    """Filter mined signals by quality thresholds. Returns filtered dict by coin."""
    all_signals = []

    for f in SIGNALS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            for sig in data.get("signals", []):
                sig["_source_file"] = f.name
                all_signals.append(sig)
        except Exception as e:
            log(f"  Skip {f.name}: {e}")

    log(f"Total mined signals: {len(all_signals)}")

    passed = []
    for sig in all_signals:
        sharpe = sig.get("sharpe", 0) or 0
        wr = sig.get("win_rate", 0) or 0
        trades = sig.get("trade_count", 0) or 0
        dd = sig.get("max_drawdown", 100) or 100

        if sharpe >= min_sharpe and wr >= min_wr and trades >= min_trades and dd <= max_dd:
            passed.append(sig)

    log(f"Passed filter (Sharpe≥{min_sharpe}, WR≥{min_wr}%, trades≥{min_trades}, DD≤{max_dd}%): {len(passed)}")

    # Group by coin
    by_coin = {}
    for sig in passed:
        coin = sig.get("_coin", sig.get("coin", "UNKNOWN"))
        by_coin.setdefault(coin, []).append(sig)

    # Sort each coin's signals by Sharpe descending
    for coin in by_coin:
        by_coin[coin].sort(key=lambda s: s.get("sharpe", 0), reverse=True)

    # Save filtered
    out_file = MINE_DIR / "filtered_signals.json"
    with open(out_file, "w") as f:
        json.dump({"filtered_at": datetime.now(timezone.utc).isoformat(),
                   "thresholds": {"min_sharpe": min_sharpe, "min_wr": min_wr,
                                 "min_trades": min_trades, "max_dd": max_dd},
                   "total_passed": len(passed),
                   "by_coin": {c: len(s) for c, s in by_coin.items()},
                   "signals": passed}, f, indent=2)

    for coin, sigs in sorted(by_coin.items()):
        best = sigs[0]
        log(f"  {coin}: {len(sigs)} signals passed (best: {best['name'][:40]} Sharpe={best.get('sharpe',0):.2f} WR={best.get('win_rate',0):.1f}%)")

    return by_coin


# ─── PHASE 3: ASSEMBLE ────────────────────────────────────────────────────────

def assemble_strategies(coins=None, api_key=None):
    """Run tournament assembly for filtered signals. 3 credits per coin."""
    filtered_file = MINE_DIR / "filtered_signals.json"
    if not filtered_file.exists():
        log("No filtered signals. Run: signal_miner.py filter")
        return

    data = json.loads(filtered_file.read_text())
    all_signals = data.get("signals", [])

    # Group by coin
    by_coin = {}
    for sig in all_signals:
        coin = sig.get("_coin", sig.get("coin", "UNKNOWN"))
        by_coin.setdefault(coin, []).append(sig)

    if coins:
        by_coin = {c: s for c, s in by_coin.items() if c in coins}

    if not by_coin:
        log("No signals to assemble.")
        return

    credits = get_credits(api_key)
    cost = len(by_coin) * 3
    log(f"Assembling {len(by_coin)} coins × 3 cr = {cost} credits (have {credits:,.0f})")

    results = {}
    for coin, sigs in sorted(by_coin.items()):
        # Build YAML for assembly
        yaml_signals = []
        for sig in sigs[:15]:  # Max 15 signals per assembly
            yaml_signals.append({
                "name": sig["name"],
                "signal_type": sig.get("signal_type", "LONG"),
                "expression": sig["expression"],
                "exit_expression": sig.get("exit_expression", ""),
                "max_hold_hours": sig.get("max_hold_hours", 48),
            })

        yaml_body = yaml.dump({"coin": coin, "signals": yaml_signals}, default_flow_style=False)

        log(f"  Assembling {coin} ({len(yaml_signals)} signals)...")
        resp = api_post_yaml("/api/claw/paid/strategy/assemble?mode=normal&max_signals=10", yaml_body, api_key)

        if "error" in resp:
            log(f"    ERROR: {resp['error']}")
            time.sleep(2)
            continue

        yaml_text = resp.get("_yaml", "")
        if yaml_text:
            # Save assembled strategy
            out_file = ASSEMBLED_DIR / f"{coin}_assembled.yaml"
            out_file.write_text(yaml_text)

            # Parse for metrics
            try:
                parsed = yaml.safe_load(yaml_text)
                strategy = parsed.get("strategy", parsed)
                sharpe = strategy.get("sharpe", strategy.get("total_sharpe", "?"))
                ret = strategy.get("total_return", "?")
                trades = strategy.get("trade_count", "?")
                log(f"    {coin}: assembled Sharpe={sharpe}, return={ret}%, trades={trades}")
                results[coin] = {"sharpe": sharpe, "return": ret, "trades": trades, "file": str(out_file)}
            except Exception as e:
                log(f"    Parse error: {e}")
                results[coin] = {"raw": yaml_text[:200]}
        else:
            log(f"    No YAML response for {coin}")

        time.sleep(1)  # Rate limit

    # Save results
    out_file = MINE_DIR / "assembly_results.json"
    with open(out_file, "w") as f:
        json.dump({"assembled_at": datetime.now(timezone.utc).isoformat(),
                   "results": results}, f, indent=2)

    log(f"\nAssembly complete: {len(results)} coins")
    return results


# ─── PHASE 4: PORTFOLIO CHECK ─────────────────────────────────────────────────

def portfolio_check(api_key=None):
    """Run portfolio optimizer as second opinion. Free endpoint."""
    # Get our current active coins from strategies.json
    strategies_file = Path(__file__).parent.parent / "v6" / "bus" / "strategies.json"
    if strategies_file.exists():
        strat_data = json.loads(strategies_file.read_text())
        current_coins = strat_data.get("active_coins", [])
    else:
        current_coins = ["BTC", "ETH", "SOL"]

    log(f"Current active coins: {', '.join(current_coins)}")

    existing = ",".join(current_coins[:5])  # API max
    resp = api_get(f"/api/claw/paid/portfolio/optimize?existing={existing}&count=8&mode=normal", api_key)

    if "error" in resp:
        log(f"ERROR: {resp['error']}")
        return

    yaml_text = resp.get("_yaml", "")
    if yaml_text:
        out_file = MINE_DIR / "portfolio_opinion.yaml"
        out_file.write_text(yaml_text)
        log(f"Portfolio optimizer response saved → {out_file.name}")

        # Parse recommendations
        try:
            parsed = yaml.safe_load(yaml_text)
            portfolio = parsed.get("portfolio", {})
            suggestions = portfolio.get("coins", portfolio.get("suggested", []))
            if isinstance(suggestions, list):
                log("Optimizer suggestions:")
                for s in suggestions:
                    if isinstance(s, dict):
                        log(f"  {s.get('coin', '?'):8s} alloc={s.get('allocation', '?')}% sharpe={s.get('sharpe', '?')} corr={s.get('correlation', '?')}")
                    else:
                        log(f"  {s}")
        except Exception as e:
            log(f"Parse warning: {e}")
            print(yaml_text[:500])
    else:
        log("No response")
        print(json.dumps(resp, indent=2)[:500])


# ─── PHASE 5: REPORT ──────────────────────────────────────────────────────────

def generate_report():
    """Compare mined signals against current strategies."""
    # Load current strategies
    strategies_file = Path(__file__).parent.parent / "v6" / "bus" / "strategies.json"
    current = {}
    if strategies_file.exists():
        strat_data = json.loads(strategies_file.read_text())
        for coin, data in strat_data.get("coins", {}).items():
            best_sharpe = data.get("best_sharpe", 0)
            num_signals = len(data.get("signals", []))
            current[coin] = {"sharpe": best_sharpe, "signals": num_signals}

    # Load assembled results
    assembly_file = MINE_DIR / "assembly_results.json"
    assembled = {}
    if assembly_file.exists():
        assembled = json.loads(assembly_file.read_text()).get("results", {})

    # Load filtered signals
    filtered_file = MINE_DIR / "filtered_signals.json"
    filtered_count = 0
    if filtered_file.exists():
        filtered_count = json.loads(filtered_file.read_text()).get("total_passed", 0)

    # Generate report
    report = []
    report.append("=" * 60)
    report.append("SIGNAL MINING REPORT")
    report.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    report.append("=" * 60)
    report.append("")

    # Count total mined
    total_mined = sum(1 for f in SIGNALS_DIR.glob("*.json")
                      for _ in json.loads(f.read_text()).get("signals", []))
    report.append(f"Total signals mined:    {total_mined}")
    report.append(f"Passed quality filter:  {filtered_count}")
    report.append(f"Coins assembled:        {len(assembled)}")
    report.append("")

    report.append("COMPARISON: Current vs Mined")
    report.append("-" * 60)
    report.append(f"{'Coin':8s} {'Current Sharpe':>15s} {'Mined Sharpe':>15s} {'Upgrade?':>10s}")
    report.append("-" * 60)

    upgrades = []
    for coin in sorted(set(list(current.keys()) + list(assembled.keys()))):
        cur_sharpe = current.get(coin, {}).get("sharpe", 0)
        mined_sharpe = assembled.get(coin, {}).get("sharpe", 0)
        try:
            mined_sharpe = float(mined_sharpe)
        except (TypeError, ValueError):
            mined_sharpe = 0

        upgrade = "YES ✓" if mined_sharpe > cur_sharpe * 1.1 else "no"
        if mined_sharpe > cur_sharpe * 1.1:
            upgrades.append(coin)
        report.append(f"{coin:8s} {cur_sharpe:>15.3f} {mined_sharpe:>15.3f} {upgrade:>10s}")

    report.append("")
    if upgrades:
        report.append(f"RECOMMENDED UPGRADES: {', '.join(upgrades)}")
        report.append("Copy assembled strategies from scanner/data/signal_mine/assembled/")
        report.append("to scanner/v6/bus/ — run AFTER measurement period ends (April 4)")
    else:
        report.append("No upgrades found. Current strategies are optimal or mined data insufficient.")

    report_text = "\n".join(report)
    print(report_text)

    # Save report
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_file = REPORTS_DIR / f"mining_report_{ts}.txt"
    report_file.write_text(report_text)
    log(f"\nReport saved → {report_file}")


# ─── FULL PIPELINE ─────────────────────────────────────────────────────────────

def full_pipeline(coins, num_packs=5, pack_type="common", budget=200, api_key=None):
    """Run all phases: mine → filter → assemble → portfolio → report."""
    log("=" * 60)
    log("SIGNAL MINING PIPELINE — FULL RUN")
    log("=" * 60)

    log("\n[1/5] MINING SIGNAL PACKS")
    mine_packs(coins, num_packs, pack_type, api_key, budget)

    log("\n[2/5] FILTERING BY QUALITY")
    by_coin = filter_signals()

    if not by_coin:
        log("No signals passed filter. Stopping.")
        return

    log("\n[3/5] ASSEMBLING STRATEGIES")
    assemble_strategies(list(by_coin.keys()), api_key)

    log("\n[4/5] PORTFOLIO OPTIMIZER CHECK")
    portfolio_check(api_key)

    log("\n[5/5] GENERATING REPORT")
    generate_report()

    credits = get_credits(api_key)
    log(f"\nCredits remaining: {credits:,.0f}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ensure_dirs()
    api_key = get_api_key()
    if not api_key:
        print("FATAL: ENVY_API_KEY not found. Set in env or ~/getzero-os/.env")
        sys.exit(1)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    # Parse --key value args
    opts = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            opts[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1

    if cmd == "mine":
        coins_str = opts.get("coins", "BTC,ETH,SOL")
        if coins_str == "ALL":
            coins = get_all_coins(api_key)
        else:
            coins = [c.strip() for c in coins_str.split(",")]
        num_packs = int(opts.get("packs", "3"))
        pack_type = opts.get("type", "common")
        budget = int(opts.get("budget", str(MAX_CREDITS_PER_RUN)))
        mine_packs(coins, num_packs, pack_type, api_key, budget)

    elif cmd == "filter":
        min_sharpe = float(opts.get("min-sharpe", str(MIN_SHARPE)))
        min_wr = float(opts.get("min-wr", str(MIN_WIN_RATE)))
        filter_signals(min_sharpe, min_wr)

    elif cmd == "assemble":
        coins = None
        if "coins" in opts:
            coins = [c.strip() for c in opts["coins"].split(",")]
        assemble_strategies(coins, api_key)

    elif cmd == "portfolio-check":
        portfolio_check(api_key)

    elif cmd == "report":
        generate_report()

    elif cmd == "full":
        coins_str = opts.get("coins", "BTC,ETH,SOL,SEI,APT,OP,NEAR,HYPE")
        if coins_str == "ALL":
            coins = get_all_coins(api_key)
        else:
            coins = [c.strip() for c in coins_str.split(",")]
        num_packs = int(opts.get("packs", "5"))
        pack_type = opts.get("type", "common")
        budget = int(opts.get("budget", "200"))
        full_pipeline(coins, num_packs, pack_type, budget, api_key)

    elif cmd == "status":
        credits = get_credits(api_key)
        log(f"Credits: {credits:,.0f}")
        # Count mined signals
        total = sum(len(json.loads(f.read_text()).get("signals", []))
                   for f in SIGNALS_DIR.glob("*.json"))
        log(f"Mined signals: {total}")
        filtered_file = MINE_DIR / "filtered_signals.json"
        if filtered_file.exists():
            d = json.loads(filtered_file.read_text())
            log(f"Filtered signals: {d.get('total_passed', 0)}")
        assembled = list(ASSEMBLED_DIR.glob("*.yaml"))
        log(f"Assembled strategies: {len(assembled)}")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
