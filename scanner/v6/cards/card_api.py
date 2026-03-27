"""FastAPI router for card PNG endpoints."""

from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(prefix="/v6/cards", tags=["cards"])

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
