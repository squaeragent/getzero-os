"""zeroos doctor — run diagnostic checks on your zero agent."""

import os
import sys

import click

from scanner.zeroos_cli.console import console, spacer


@click.command()
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON.')
def doctor(as_json):
    """Run 6 diagnostic checks on your zero agent."""
    from scanner.zeroos_cli.config_utils import load_token
    from scanner.zeroos_cli.api_client import ZeroAPIClient

    checks = []

    # 1. Token exists
    token = load_token()
    checks.append({
        'name': 'token',
        'status': 'ok' if token else 'fail',
        'detail': 'token loaded' if token else 'no token found. run: zeroos init --token YOUR_TOKEN',
    })

    if not token:
        _render(checks, as_json)
        return

    client = ZeroAPIClient(token)

    # 2. API reachable
    try:
        credits = client.get_credits()
        checks.append({'name': 'api', 'status': 'ok', 'detail': 'api reachable'})
    except Exception as e:
        checks.append({'name': 'api', 'status': 'fail', 'detail': f'api unreachable: {e}'})
        _render(checks, as_json)
        return

    # 3. Credits available
    bal = credits.balance
    checks.append({
        'name': 'credits',
        'status': 'ok' if bal > 0 else 'warn',
        'detail': f'{bal:,} credits' if bal > 0 else '0 credits. run: zeroos credits buy',
    })

    # 4. Agent exists — try evaluate as a proxy
    try:
        client.evaluate('BTC')
        checks.append({'name': 'agent', 'status': 'ok', 'detail': 'agent responding'})
    except Exception:
        checks.append({'name': 'agent', 'status': 'warn', 'detail': 'agent not responding (may need credits)'})

    # 5. Config file
    config_path = os.path.expanduser('~/.zeroos/config.json')
    checks.append({
        'name': 'config',
        'status': 'ok' if os.path.exists(config_path) else 'warn',
        'detail': 'config loaded' if os.path.exists(config_path) else 'no config file. run: zeroos init',
    })

    # 6. Python version
    py_ok = sys.version_info >= (3, 10)
    py_ver = f'python {sys.version_info.major}.{sys.version_info.minor}'
    checks.append({
        'name': 'python',
        'status': 'ok' if py_ok else 'warn',
        'detail': py_ver if py_ok else f'{py_ver} (3.10+ recommended)',
    })

    _render(checks, as_json)


def _render(checks, as_json):
    if as_json:
        import json
        click.echo(json.dumps({'checks': checks}, indent=2))
        return

    spacer()
    console.print('  [header]◆ zero▮[/header] [mid]doctor[/mid]')
    spacer()

    for c in checks:
        icon = '✓' if c['status'] == 'ok' else '!' if c['status'] == 'warn' else '✗'
        color = 'green' if c['status'] == 'ok' else 'yellow' if c['status'] == 'warn' else 'red'
        console.print(f'  [{color}]{icon}[/{color}]  {c["name"]:12s} {c["detail"]}')

    spacer()
    passed = sum(1 for c in checks if c['status'] == 'ok')
    total = len(checks)
    if passed == total:
        console.print(f'  [green]all {total} checks passed.[/green]')
    else:
        console.print(f'  [yellow]{passed}/{total} checks passed.[/yellow]')
    spacer()
