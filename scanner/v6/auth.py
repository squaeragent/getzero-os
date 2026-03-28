"""
ZERO Engine — Auth middleware, token resolution, rate limiting.

Bearer token auth for protected routes. Public routes pass through.
Rate limiting per operator with sliding window.
"""

from __future__ import annotations

import json
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ── Token Storage ───────────────────────────────────────────────────────────

TOKENS_FILE = Path(__file__).parent / "data" / "tokens.json"


def _load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        return {}
    try:
        return json.loads(TOKENS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def resolve_token(token: str) -> dict | None:
    """Look up operator by bearer token."""
    tokens = _load_tokens()
    return tokens.get(token)


def generate_token() -> str:
    """Generate a zr_ prefixed bearer token."""
    return "zr_" + secrets.token_hex(16)


def register_token(operator_id: str, plan: str = "free") -> str:
    """Generate and store a token for an operator. Returns the token."""
    tokens = _load_tokens()

    # Check if operator already has a token
    for tok, info in tokens.items():
        if info.get("operator_id") == operator_id:
            return tok

    token = generate_token()
    tokens[token] = {
        "operator_id": operator_id,
        "plan": plan,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _save_tokens(tokens)
    return token


# ── Rate Limiting ───────────────────────────────────────────────────────────

RATE_LIMITS = {
    "free": 30,
    "pro": 120,
    "scale": 1200,
    "api": 6000,
}

# In-memory sliding window
_rate_counters: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(operator_id: str, plan: str) -> tuple[bool, dict]:
    """Check if operator is within rate limit. Returns (allowed, headers)."""
    limit = RATE_LIMITS.get(plan, 30)
    now = time.time()
    window_start = now - 3600

    # Clean old entries
    _rate_counters[operator_id] = [
        t for t in _rate_counters[operator_id] if t > window_start
    ]

    remaining = limit - len(_rate_counters[operator_id])
    reset_time = int(now + 3600)

    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Reset": str(reset_time),
    }

    if remaining <= 0:
        return False, headers

    _rate_counters[operator_id].append(now)
    return True, headers


# ── Public Route Prefixes ───────────────────────────────────────────────────

PUBLIC_PREFIXES = (
    "/health",
    "/v6/engine/health",
    "/v6/engine/stats",
    "/v6/strategies",
    "/v6/strategy/",
    "/v6/evaluate/",
    "/v6/collective",
    "/v6/arena/public",
    "/v6/agent/public/",
    "/v6/genesis",
    "/v6/cache/",
    "/v6/dashboard",
    "/v6/cards/",
    "/v6/backtest/",
    "/mcp",
    "/docs",
    "/openapi.json",
    "/schemas",
    "/decide",
    "/regime",
    "/signals",
    "/positions",
    "/performance",
    "/world",
    "/landscape",
)


# ── Auth Middleware ──────────────────────────────────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path

        # Root path
        if path == "/" or path == "/health":
            return await call_next(request)

        # Public routes pass through
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # OPTIONS requests pass through (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Check auth header
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "missing token"},
            )

        token = auth.replace("Bearer ", "", 1)
        operator = resolve_token(token)
        if not operator:
            return JSONResponse(
                status_code=401,
                content={"error": "invalid token"},
            )

        # Rate limit check
        op_id = operator["operator_id"]
        plan = operator["plan"]
        allowed, rate_headers = check_rate_limit(op_id, plan)

        if not allowed:
            retry_after = int(rate_headers["X-RateLimit-Reset"]) - int(time.time())
            resp = JSONResponse(
                status_code=429,
                content={
                    "error": "rate limit exceeded",
                    "limit": RATE_LIMITS.get(plan, 30),
                    "retry_after": max(0, retry_after),
                    "upgrade": "getzero.dev/pricing",
                },
            )
            for k, v in rate_headers.items():
                resp.headers[k] = v
            return resp

        # Set operator context on request state
        request.state.operator_id = op_id
        request.state.plan = plan

        # Call route handler
        response = await call_next(request)

        # Add rate limit headers to response
        for k, v in rate_headers.items():
            response.headers[k] = v

        return response


# ── MCP Tool Tier Gating ────────────────────────────────────────────────────

TOOL_TIERS = {
    # Public (no token)
    "zero_evaluate": "public",
    "zero_list_strategies": "public",
    "zero_preview_strategy": "public",
    "zero_get_engine_health": "public",
    # Free
    "zero_get_heat": "free",
    "zero_get_approaching": "free",
    "zero_get_regime": "free",
    "zero_get_brief": "free",
    "zero_start_session": "free",
    "zero_session_status": "free",
    "zero_end_session": "free",
    "zero_session_history": "free",
    "zero_get_score": "free",
    "zero_get_achievements": "free",
    "zero_get_streak": "free",
    "zero_get_reputation": "free",
    "zero_get_arena": "free",
    "zero_get_rivalry": "free",
    "zero_get_profile": "free",
    "zero_get_insights": "free",
    # Pro
    "zero_get_pulse": "pro",
    "zero_queue_session": "pro",
    "zero_set_mode": "pro",
    "zero_auto_select": "pro",
    # Scale
    "zero_session_result": "scale",
    "zero_get_chain": "scale",
    "zero_get_credits": "scale",
    "zero_get_energy": "scale",
}

TIER_ORDER = {"public": 0, "free": 1, "pro": 2, "scale": 3}

FREE_STRATEGIES = {"momentum", "defense", "watch"}
FREE_MAX_CONCURRENT = 1
PRO_MAX_CONCURRENT = 3


def check_tool_tier(tool_name: str, plan: str) -> dict | None:
    """Check if operator's plan allows a tool. Returns error dict or None."""
    required_tier = TOOL_TIERS.get(tool_name, "free")
    if required_tier == "public":
        return None

    plan_level = TIER_ORDER.get(plan, 1)
    required_level = TIER_ORDER.get(required_tier, 1)

    if plan_level < required_level:
        return {
            "error": "forbidden",
            "reason": f"requires {required_tier} plan",
            "your_plan": plan,
            "upgrade": "getzero.dev/pricing",
        }
    return None
