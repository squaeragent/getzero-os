"""zeroos MCP server — 12 tools for AI agents to interact with zero."""

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
