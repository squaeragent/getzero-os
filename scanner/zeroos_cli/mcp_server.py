"""zeroos MCP server — 22 tools for AI agents to interact with zero."""

from fastmcp import FastMCP

mcp = FastMCP('zeroos')


def _get_client():
    from scanner.zeroos_cli.config_utils import load_token
    from scanner.zeroos_cli.api_client import ZeroAPIClient
    token = load_token()
    return ZeroAPIClient(token)


@mcp.tool()
def zero_status() -> dict:
    """Get agent status — health, uptime, positions, immune system state."""
    client = _get_client()
    try:
        credits = client.get_credits()
        return {
            'status': 'running',
            'credits_remaining': credits.balance,
            'genesis': credits.genesis,
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


@mcp.tool()
def zero_evaluate(coin: str) -> dict:
    """Evaluate a coin — see the reasoning engine think. Returns regime, direction, consensus, verdict."""
    client = _get_client()
    try:
        result = client.evaluate(coin)
        return {
            'coin': result.coin,
            'regime': result.regime,
            'confidence': result.confidence,
            'direction': result.direction,
            'consensus': result.consensus_label,
            'consensus_value': result.consensus_value,
            'conviction': result.conviction_level,
            'verdict': result.verdict,
            'reasoning': result.reasoning,
        }
    except Exception as e:
        return {'error': str(e)}


@mcp.tool()
def zero_credits() -> dict:
    """Check credit balance and usage."""
    client = _get_client()
    try:
        c = client.get_credits()
        return {
            'balance': c.balance,
            'total_purchased': c.total_purchased,
            'total_used': c.total_used,
            'genesis': c.genesis,
        }
    except Exception as e:
        return {'error': str(e)}


@mcp.tool()
def zero_buy_credits(package: str) -> dict:
    """Buy evaluation credits. Package: starter (10K/$29), pro (50K/$99), scale (100K/$179)."""
    if package not in ('starter', 'pro', 'scale'):
        return {'error': f'Invalid package: {package}. Choose starter, pro, or scale.'}
    client = _get_client()
    try:
        result = client.create_checkout(package)
        return result
    except Exception as e:
        return {'error': str(e)}


@mcp.tool()
def zero_positions() -> dict:
    """List open positions."""
    return {'note': 'positions endpoint coming soon', 'positions': []}


@mcp.tool()
def zero_trades(limit: int = 10) -> dict:
    """List recent closed trades."""
    return {'note': 'trades endpoint coming soon', 'trades': []}


@mcp.tool()
def zero_score() -> dict:
    """Get your zero score — performance, consistency, discipline, resilience, immune health."""
    return {'note': 'score endpoint coming soon'}


@mcp.tool()
def zero_brief() -> dict:
    """Get the daily brief — overnight activity, immune checks, decisions made."""
    return {'note': 'brief endpoint coming soon'}


@mcp.tool()
def zero_immune() -> dict:
    """Check immune system status — stop integrity, position health, recent interventions."""
    return {'note': 'immune endpoint coming soon'}


@mcp.tool()
def zero_config(key: str = '', value: str = '') -> dict:
    """View or update agent configuration. Call without args to see current config."""
    return {'note': 'config endpoint coming soon'}


@mcp.tool()
def zero_network() -> dict:
    """Get network intelligence — what the collective sees."""
    return {'note': 'network endpoint coming soon'}


@mcp.tool()
def zero_help() -> dict:
    """List all available zero tools and what they do."""
    return {
        'tools': [
            {'name': 'zero_status', 'desc': 'agent health and uptime'},
            {'name': 'zero_evaluate', 'desc': 'evaluate any coin'},
            {'name': 'zero_credits', 'desc': 'check credit balance'},
            {'name': 'zero_buy_credits', 'desc': 'purchase credits'},
            {'name': 'zero_positions', 'desc': 'list open positions'},
            {'name': 'zero_trades', 'desc': 'list recent trades'},
            {'name': 'zero_score', 'desc': 'your zero score'},
            {'name': 'zero_brief', 'desc': 'daily brief'},
            {'name': 'zero_immune', 'desc': 'immune system status'},
            {'name': 'zero_config', 'desc': 'view/update config'},
            {'name': 'zero_network', 'desc': 'network intelligence'},
            {'name': 'zero_help', 'desc': 'this help'},
            {'name': 'zero_doctor', 'desc': 'run diagnostics'},
            {'name': 'zero_arena', 'desc': 'arena info and rewards'},
            {'name': 'zero_list_strategies', 'desc': 'catalog of 9 strategies'},
            {'name': 'zero_preview_strategy', 'desc': 'preview one strategy'},
            {'name': 'zero_start_session', 'desc': 'start a trading session'},
            {'name': 'zero_session_status', 'desc': 'active session status'},
            {'name': 'zero_end_session', 'desc': 'end active session'},
            {'name': 'zero_queue_session', 'desc': 'queue a session'},
            {'name': 'zero_session_history', 'desc': 'recent session history'},
            {'name': 'zero_get_achievements', 'desc': 'earned achievements'},
            {'name': 'zero_set_strategy', 'desc': 'set trading strategy'},
            {'name': 'zero_set_direction', 'desc': 'set direction mode'},
            {'name': 'zero_set_risk', 'desc': 'set risk level'},
            {'name': 'zero_set_state', 'desc': 'set agent state'},
            {'name': 'zero_set_scope', 'desc': 'set trading scope'},
            {'name': 'zero_add_condition', 'desc': 'add auto-switching condition'},
            {'name': 'zero_remove_condition', 'desc': 'remove a condition'},
            {'name': 'zero_get_modes', 'desc': 'current mode configuration'},
            {'name': 'zero_size_up', 'desc': 'increase size 1.5x'},
            {'name': 'zero_size_down', 'desc': 'decrease size 0.5x'},
            {'name': 'zero_reset_size', 'desc': 'reset size to 1.0x'},
        ]
    }

@mcp.tool()
def zero_doctor() -> dict:
    """Run 6 diagnostic checks — token, API, credits, agent, config, Python version."""
    import os, sys
    checks = []
    
    try:
        from scanner.zeroos_cli.config_utils import load_token
        token = load_token()
        checks.append({'name': 'token', 'status': 'ok' if token else 'fail', 'detail': 'token loaded' if token else 'no token'})
    except Exception:
        checks.append({'name': 'token', 'status': 'fail', 'detail': 'could not load token'})
        return {'checks': checks}
    
    if token:
        try:
            client = _get_client()
            credits = client.get_credits()
            checks.append({'name': 'api', 'status': 'ok', 'detail': 'api reachable'})
            bal = getattr(credits, 'balance', 0)
            checks.append({'name': 'credits', 'status': 'ok' if bal > 0 else 'warn', 'detail': f'{bal} credits'})
        except Exception as e:
            checks.append({'name': 'api', 'status': 'fail', 'detail': str(e)})
    
    config_path = os.path.expanduser('~/.zeroos/config.json')
    checks.append({'name': 'config', 'status': 'ok' if os.path.exists(config_path) else 'warn', 'detail': 'exists' if os.path.exists(config_path) else 'missing'})
    
    py_ok = sys.version_info >= (3, 10)
    checks.append({'name': 'python', 'status': 'ok' if py_ok else 'warn', 'detail': f'{sys.version_info.major}.{sys.version_info.minor}'})
    
    return {'checks': checks, 'passed': sum(1 for c in checks if c['status'] == 'ok'), 'total': len(checks)}


@mcp.tool()
def zero_arena() -> dict:
    """Get arena info — weekly rewards and score multipliers."""
    return {
        'weekly_rewards': {'1st': 5000, '2nd': 3000, '3rd': 2000, '4th-10th': 500},
        'score_multipliers': {'6.0-6.9': '1.2x', '7.0-7.9': '1.5x', '8.0-8.9': '2.0x', '9.0+': '3.0x'},
        'leaderboard': 'app.getzero.dev/arena',
    }


STRATEGY_CATALOG = [
    {'name': 'momentum', 'cost': 500, 'risk_level': 'medium', 'direction': 'long', 'max_hold': '4h',
     'desc': 'ride strong directional moves with trend confirmation across timeframes'},
    {'name': 'degen', 'cost': 1000, 'risk_level': 'high', 'direction': 'both', 'max_hold': '2h',
     'desc': 'aggressive entries on volatile setups. high reward, high risk. not for beginners'},
    {'name': 'defense', 'cost': 200, 'risk_level': 'low', 'direction': 'short', 'max_hold': '6h',
     'desc': 'protective hedging against drawdowns. deploys when fear spikes'},
    {'name': 'sniper', 'cost': 750, 'risk_level': 'medium-high', 'direction': 'both', 'max_hold': '1h',
     'desc': 'precision entries at key levels. waits for confluence then strikes fast'},
    {'name': 'scout', 'cost': 100, 'risk_level': 'low', 'direction': 'neutral', 'max_hold': '24h',
     'desc': 'passive market monitoring. no trades. builds intelligence for future sessions'},
    {'name': 'fade', 'cost': 400, 'risk_level': 'medium', 'direction': 'contrarian', 'max_hold': '3h',
     'desc': 'counter-trend plays at exhaustion points. fades overextended moves'},
    {'name': 'funding', 'cost': 300, 'risk_level': 'low-medium', 'direction': 'both', 'max_hold': '8h',
     'desc': 'exploit funding rate imbalances. delta-neutral when possible'},
    {'name': 'watch', 'cost': 50, 'risk_level': 'none', 'direction': 'neutral', 'max_hold': '12h',
     'desc': 'observation mode. zero tracks markets and reports back. no capital deployed'},
    {'name': 'apex', 'cost': 800, 'risk_level': 'high', 'direction': 'both', 'max_hold': '6h',
     'desc': 'multi-layer strategy combining momentum + sniper + funding signals. advanced'},
]


@mcp.tool()
def zero_list_strategies() -> dict:
    """List all 9 available strategy types with name, cost, risk level, direction, and max hold time."""
    return {'strategies': STRATEGY_CATALOG, 'count': len(STRATEGY_CATALOG)}


@mcp.tool()
def zero_preview_strategy(strategy_type: str) -> dict:
    """Preview a specific strategy — details, cost, risk, and current market match assessment."""
    match = next((s for s in STRATEGY_CATALOG if s['name'] == strategy_type), None)
    if not match:
        names = [s['name'] for s in STRATEGY_CATALOG]
        return {'error': f'unknown strategy: {strategy_type}', 'available': names}
    return {
        **match,
        'market_match': 'assessment requires live data — use zero_evaluate() on target coin first',
        'requirements': f'{match["cost"]} credits required to activate',
    }


@mcp.tool()
def zero_start_session(strategy: str) -> dict:
    """Start a new trading session with the specified strategy. Deducts credits on activation."""
    match = next((s for s in STRATEGY_CATALOG if s['name'] == strategy), None)
    if not match:
        names = [s['name'] for s in STRATEGY_CATALOG]
        return {'error': f'unknown strategy: {strategy}', 'available': names}
    return {
        'status': 'session_started',
        'strategy': strategy,
        'cost_credits': match['cost'],
        'note': 'session management coming soon — this is a placeholder',
    }


@mcp.tool()
def zero_session_status() -> dict:
    """Check active session status — strategy, runtime, P&L, positions, stops."""
    return {
        'status': 'active',
        'strategy': 'momentum',
        'runtime_minutes': 47,
        'pnl_usd': 12.40,
        'positions': 1,
        'stops_triggered': 0,
        'note': 'session status coming soon — this is mock data',
    }


@mcp.tool()
def zero_end_session() -> dict:
    """End the current active session. Closes positions and generates session report."""
    return {
        'status': 'session_ended',
        'note': 'session management coming soon — this is a placeholder',
    }


@mcp.tool()
def zero_queue_session(strategy: str) -> dict:
    """Queue a session to start when market conditions match the strategy requirements."""
    match = next((s for s in STRATEGY_CATALOG if s['name'] == strategy), None)
    if not match:
        names = [s['name'] for s in STRATEGY_CATALOG]
        return {'error': f'unknown strategy: {strategy}', 'available': names}
    return {
        'status': 'queued',
        'strategy': strategy,
        'cost_credits': match['cost'],
        'note': 'session queuing coming soon — this is a placeholder',
    }


@mcp.tool()
def zero_session_history(limit: int = 5) -> dict:
    """Get recent session history — strategy, result, P&L, duration for past sessions."""
    return {
        'sessions': [],
        'total': 0,
        'limit': limit,
        'note': 'session history coming soon — this is a placeholder',
    }


@mcp.tool()
def zero_get_achievements() -> dict:
    """Get earned achievements — milestones, streaks, arena placements, special badges."""
    return {
        'achievements': [],
        'total_earned': 0,
        'note': 'achievements coming soon — this is a placeholder',
    }


# ── Mode System ──────────────────────────────────────────────

def _get_mode_manager():
    from scanner.v6.mode_manager import ModeManager
    return ModeManager()


@mcp.tool()
def zero_set_strategy(strategy: str, coins: str = '') -> dict:
    """Set trading strategy. Options: momentum, mean_revert, breakout, sniper, scalp, grid. Optional comma-separated coins for per-coin override."""
    mm = _get_mode_manager()
    coin_list = [c.strip().upper() for c in coins.split(',') if c.strip()] if coins else None
    return mm.set_strategy(strategy, coin_list)


@mcp.tool()
def zero_set_direction(direction: str) -> dict:
    """Set direction mode. Options: long_only, both, short_only, funding_harvest."""
    mm = _get_mode_manager()
    return mm.set_direction(direction)


@mcp.tool()
def zero_set_risk(risk: str) -> dict:
    """Set risk level. Options: defense, normal, aggressive. Each has preset position size, max positions, stops, and circuit breakers."""
    mm = _get_mode_manager()
    return mm.set_risk(risk)


@mcp.tool()
def zero_set_state(state: str, condition: str = '') -> dict:
    """Set agent state. Options: active, observe, sleep, exit_only, paper. Optional wake condition for sleep/exit_only."""
    mm = _get_mode_manager()
    cond = condition if condition else None
    return mm.set_state(state, cond)


@mcp.tool()
def zero_set_scope(scope: str) -> dict:
    """Set trading scope. Options: focused, broad, full, or comma-separated coins for custom scope, or sector name (large_caps, defi, memes, l1s)."""
    mm = _get_mode_manager()
    if ',' in scope:
        coin_list = [c.strip().upper() for c in scope.split(',') if c.strip()]
        return mm.set_scope(coin_list)
    return mm.set_scope(scope)


@mcp.tool()
def zero_add_condition(trigger: str, action: str) -> dict:
    """Add an auto-switching condition. Example trigger: 'btc_drops_5pct', action: 'set_risk_defense'."""
    mm = _get_mode_manager()
    return mm.add_condition(trigger, action)


@mcp.tool()
def zero_remove_condition(trigger: str) -> dict:
    """Remove an auto-switching condition by its trigger name."""
    mm = _get_mode_manager()
    return mm.remove_condition(trigger)


@mcp.tool()
def zero_get_modes() -> dict:
    """Get current mode configuration — strategy, direction, risk, state, scope, conditions, size multiplier."""
    mm = _get_mode_manager()
    return mm.get_modes()


@mcp.tool()
def zero_size_up() -> dict:
    """Increase position size multiplier by 1.5x."""
    mm = _get_mode_manager()
    return mm.size_up()


@mcp.tool()
def zero_size_down() -> dict:
    """Decrease position size multiplier by 0.5x."""
    mm = _get_mode_manager()
    return mm.size_down()


@mcp.tool()
def zero_reset_size() -> dict:
    """Reset position size multiplier to 1.0x."""
    mm = _get_mode_manager()
    return mm.reset_size()
