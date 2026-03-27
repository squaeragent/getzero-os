"""Backtest API endpoints — run backtests and retrieve results."""

import json
from pathlib import Path

from fastapi import APIRouter, Query

from scanner.v6.backtest.backtester import Backtester, BacktestResult
from scanner.v6.backtest.data_fetcher import TOP_COINS
from scanner.v6.strategy_loader import list_strategies

router = APIRouter(prefix="/v6/backtest", tags=["backtest"])

RESULTS_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(strategy: str, days: int) -> str:
    return f"{strategy}_{days}d"


def _save_result(result: BacktestResult):
    key = _cache_key(result.strategy, result.days)
    path = RESULTS_DIR / f"{key}.json"
    path.write_text(json.dumps(result.to_dict(), default=str))


def _load_result(strategy: str, days: int) -> dict | None:
    key = _cache_key(strategy, days)
    path = RESULTS_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


@router.get("/run")
async def run_backtest(
    strategy: str = "momentum",
    days: int = Query(default=90, ge=7, le=365),
    coins: str = Query(default="BTC,ETH,SOL"),
    equity: float = Query(default=100.0, ge=10),
):
    """Run backtest and return results JSON."""
    coin_list = [c.strip().upper() for c in coins.split(",") if c.strip()]
    bt = Backtester(starting_equity=equity)
    result = bt.run(strategy, coins=coin_list, days=days)
    _save_result(result)

    d = result.to_dict()
    # Trim trades list for API response (keep first 50)
    if len(d.get("trades", [])) > 50:
        d["trades"] = d["trades"][:50]
        d["trades_truncated"] = True
    return d


@router.get("/summary")
async def backtest_summary(
    days: int = Query(default=90, ge=7, le=365),
    coins: str = Query(default="BTC,ETH,SOL"),
):
    """Run all strategies, return comparison."""
    coin_list = [c.strip().upper() for c in coins.split(",") if c.strip()]
    strategies = list_strategies()
    bt = Backtester(starting_equity=100.0)

    results = []
    for name in strategies:
        try:
            result = bt.run(name, coins=coin_list, days=days)
            _save_result(result)
            results.append({
                "strategy": name,
                "pnl_pct": result.total_pnl_pct,
                "pnl_usd": result.total_pnl_usd,
                "trades": result.total_trades,
                "win_rate": result.win_rate,
                "max_drawdown": result.max_drawdown_pct,
                "sharpe": result.sharpe_ratio,
                "avg_hold_hours": result.avg_hold_hours,
                "rejection_rate": result.rejection_rate,
            })
        except Exception as exc:
            results.append({"strategy": name, "error": str(exc)})

    results.sort(key=lambda r: r.get("pnl_pct", -999), reverse=True)
    return {"days": days, "coins": coin_list, "strategies": results}


@router.get("/results/{strategy}")
async def get_results(strategy: str, days: int = Query(default=90)):
    """Get cached results for a strategy."""
    cached = _load_result(strategy, days)
    if cached:
        return cached
    return {"error": f"No cached results for {strategy} ({days}d). Run /v6/backtest/run?strategy={strategy}&days={days} first."}
