"""FastAPI router for card PNG endpoints."""

import json
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(prefix="/v6/cards", tags=["cards"])

RESULTS_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_results"

_renderer = None


def _get_renderer():
    global _renderer
    if _renderer is None:
        from scanner.v6.cards.renderer import CardRenderer
        _renderer = CardRenderer()
    return _renderer


@router.get("/eval")
async def card_eval(coin: str, operator_id: str = Query("op_default")):
    """Render evaluation card for a coin. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    result = api.evaluate(operator_id, coin)
    png = await _get_renderer().render_async("eval_card", result)
    return Response(content=png, media_type="image/png")


@router.get("/heat")
async def card_heat(operator_id: str = Query("op_default")):
    """Render heat map card. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    data = api.get_heat(operator_id)
    png = await _get_renderer().render_async("heat_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/brief")
async def card_brief(operator_id: str = Query("op_default")):
    """Render brief card. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    result = api.get_brief(operator_id)
    png = await _get_renderer().render_async("brief_card", result)
    return Response(content=png, media_type="image/png")


@router.get("/approaching")
async def card_approaching(operator_id: str = Query("op_default")):
    """Render approaching card. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    data = api.get_approaching(operator_id)
    png = await _get_renderer().render_async("approaching_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/result")
async def card_result(session_id: str = None, operator_id: str = Query("op_default")):
    """Render result card. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    result = api.session_result(operator_id, session_id or "latest")
    png = await _get_renderer().render_async("result_card", result)
    return Response(content=png, media_type="image/png")


@router.get("/equity")
async def card_equity(session_id: str = Query("latest"), operator_id: str = Query("op_default")):
    """Render equity curve card. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    data = api.session_result(operator_id, session_id)
    png = await _get_renderer().render_async("equity_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/radar")
async def card_radar(coin: str, operator_id: str = Query("op_default")):
    """Render radar/spider card for a coin. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    data = api.evaluate(operator_id, coin)
    png = await _get_renderer().render_async("radar_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/gauge")
async def card_gauge(value: int = Query(50)):
    """Render Fear & Greed gauge card. Returns PNG."""
    data = {"value": value, "label": "Fear & Greed"}
    png = await _get_renderer().render_async("gauge_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/funnel")
async def card_funnel(session_id: str = Query("latest"), operator_id: str = Query("op_default")):
    """Render rejection funnel card. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    data = api.session_result(operator_id, session_id)
    png = await _get_renderer().render_async("funnel_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/regime")
async def card_regime(operator_id: str = Query("op_default")):
    """Render regime card — global market state. Returns PNG."""
    from scanner.v6.api import ZeroAPI
    from scanner.v6.regime import RegimeState
    api = ZeroAPI()
    heat_data = api.get_heat(operator_id)
    brief_data = api.get_brief(operator_id)
    regime = RegimeState.from_heat(heat_data, brief_data)
    png = await _get_renderer().render_async("regime_card", regime.to_dict())
    return Response(content=png, media_type="image/png")


@router.get("/mode")
async def card_mode(mode: str = Query("comfort")):
    """Render drive mode comparison card. Returns PNG."""
    from scanner.v6.strategy_loader import load_strategy, VALID_MODES
    if mode not in VALID_MODES:
        mode = "comfort"
    cfg = load_strategy("momentum")
    modes_dict = {}
    for m in sorted(VALID_MODES):
        mc = cfg.get_mode_config(m)
        modes_dict[m] = mc.to_dict()
    data = {"active_mode": mode, "modes": modes_dict}
    png = await _get_renderer().render_async("mode_card", data)
    return Response(content=png, media_type="image/png")


# ── Backtest card endpoints ────────────────────────────────────────

def _load_backtest(strategy: str, days: int) -> dict | None:
    """Load cached backtest result, or run backtest if not cached."""
    key = f"{strategy}_{days}d"
    path = RESULTS_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    # Not cached — run backtest
    from scanner.v6.backtest.backtester import Backtester
    bt = Backtester(starting_equity=100.0)
    result = bt.run(strategy, days=days)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), default=str))
    return result.to_dict()


@router.get("/backtest/summary")
async def card_backtest_summary(days: int = Query(default=90, ge=7, le=365)):
    """Render backtest summary card comparing all strategies. Returns PNG."""
    from scanner.v6.strategy_loader import list_strategies
    strategies = list_strategies()
    rows = []
    for name in strategies:
        try:
            r = _load_backtest(name, days)
            rows.append({
                "name": name,
                "strategy": name,
                "total_pnl_pct": r.get("total_pnl_pct", 0),
                "total_trades": r.get("total_trades", 0),
                "win_rate": r.get("win_rate", 0),
                "max_drawdown_pct": r.get("max_drawdown_pct", 0),
                "sharpe_ratio": r.get("sharpe_ratio", 0),
            })
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("total_pnl_pct", -999), reverse=True)
    # Derive date range from first successful result
    first = _load_backtest(rows[0]["name"], days) if rows else {}
    data = {
        "strategies": rows,
        "days": days,
        "start_date": first.get("start_date", "---"),
        "end_date": first.get("end_date", "---"),
    }
    png = await _get_renderer().render_async("backtest_summary_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/backtest/equity")
async def card_backtest_equity(
    strategy: str = Query(default="momentum"),
    days: int = Query(default=30, ge=7, le=365),
):
    """Render backtest equity curve card. Returns PNG."""
    r = _load_backtest(strategy, days)
    data = {
        "strategy": strategy,
        "equity_curve": r.get("equity_curve", []),
        "total_pnl_pct": r.get("total_pnl_pct", 0),
        "max_drawdown_pct": r.get("max_drawdown_pct", 0),
        "total_trades": r.get("total_trades", 0),
        "win_rate": r.get("win_rate", 0),
        "days": days,
    }
    png = await _get_renderer().render_async("backtest_equity_card", data)
    return Response(content=png, media_type="image/png")


@router.get("/backtest/compare")
async def card_backtest_compare(
    a: str = Query(default="momentum"),
    b: str = Query(default="degen"),
    days: int = Query(default=30, ge=7, le=365),
):
    """Render backtest comparison card — two strategies head-to-head. Returns PNG."""
    ra = _load_backtest(a, days)
    rb = _load_backtest(b, days)
    data = {
        "a": {
            "strategy": a,
            "equity_curve": ra.get("equity_curve", []),
            "total_pnl_pct": ra.get("total_pnl_pct", 0),
            "win_rate": ra.get("win_rate", 0),
            "max_drawdown_pct": ra.get("max_drawdown_pct", 0),
            "sharpe_ratio": ra.get("sharpe_ratio", 0),
        },
        "b": {
            "strategy": b,
            "equity_curve": rb.get("equity_curve", []),
            "total_pnl_pct": rb.get("total_pnl_pct", 0),
            "win_rate": rb.get("win_rate", 0),
            "max_drawdown_pct": rb.get("max_drawdown_pct", 0),
            "sharpe_ratio": rb.get("sharpe_ratio", 0),
        },
        "days": days,
    }
    png = await _get_renderer().render_async("backtest_compare_card", data)
    return Response(content=png, media_type="image/png")
