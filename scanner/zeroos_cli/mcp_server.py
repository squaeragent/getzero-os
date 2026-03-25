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
