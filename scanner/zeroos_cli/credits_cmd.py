"""zeroos credits — view balance and purchase evaluation credits."""

import json as _json

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, success, action,
)


PACKAGES = {
    'starter': {'credits': '10,000', 'price': '$29'},
    'pro': {'credits': '50,000', 'price': '$99'},
    'scale': {'credits': '100,000', 'price': '$179'},
}


@click.group(invoke_without_command=True)
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON.')
@click.pass_context
def credits(ctx, as_json):
    """View credit balance and usage."""
    if ctx.invoked_subcommand is not None:
        return

    from scanner.zeroos_cli.config_utils import load_token
    from scanner.zeroos_cli.api_client import ZeroAPIClient

    token = load_token()
    api = ZeroAPIClient(token)

    try:
        result = api.get_credits()
    except Exception as e:
        fail(str(e))
        raise SystemExit(1)

    if as_json:
        print(_json.dumps({
            'balance': result.balance,
            'total_purchased': result.total_purchased,
            'total_used': result.total_used,
            'genesis': result.genesis,
            'estimated_days': result.estimated_days,
        }, indent=2))
        return

    spacer()
    logo()
    spacer()
    rule()
    spacer()
    section('CREDITS')

    genesis_label = '[lime]genesis[/lime]' if result.genesis else '[dim]standard[/dim]'
    dots('status', genesis_label)
    dots('balance', f'{result.balance:,}')
    dots('purchased', f'{result.total_purchased:,}')
    dots('used', f'{result.total_used:,}')

    # estimate: ~192 evals/day at full utilization
    if result.balance > 0:
        est_days = result.balance // 192
        dots('estimated days', f'~{est_days}')

    spacer()
    rule()
    spacer()
    section('PACKAGES')
    spacer()
    for name, pkg in PACKAGES.items():
        console.print(f'  [lime]{name:10s}[/lime] [bright]{pkg["credits"]:>7s} credits[/bright]  [dim]{pkg["price"]}[/dim]')
    spacer()
    action('zeroos credits buy <package>', 'purchase credits')
    spacer()
    rule()
    spacer()


@credits.command()
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON.')
def history(as_json):
    """View credit transaction history."""
    from scanner.zeroos_cli.config_utils import load_token
    from scanner.zeroos_cli.api_client import ZeroAPIClient

    token = load_token()
    if not token:
        fail('not authenticated.')
        action('zeroos init --token YOUR_TOKEN')
        raise SystemExit(1)

    client = ZeroAPIClient(token)
    try:
        data = client._request('GET', '/credits/history')

        if 'error' in data:
            fail(data['error'])
            raise SystemExit(1)

        transactions = data.get('transactions', [])

        if as_json:
            print(_json.dumps(data, indent=2))
            return

        spacer()
        console.print('  [header]◆ zero▮[/header] [mid]credit history[/mid]')
        spacer()

        if not transactions:
            console.print('  [dim]no transactions yet.[/dim]')
        else:
            for tx in transactions[:20]:
                amount = tx.get('amount', 0)
                sign = '+' if amount > 0 else ''
                color = 'green' if amount > 0 else 'red'
                tx_type = tx.get('type', '?')
                date = tx.get('created_at', '')[:10]
                console.print(f'  [{color}]{sign}{amount:>6}[/{color}]  {tx_type:16s}  {date}')

        spacer()
    except SystemExit:
        raise
    except Exception as e:
        fail(f'failed to fetch history: {e}')
        raise SystemExit(1)


@credits.command()
@click.argument('package', type=click.Choice(['starter', 'pro', 'scale']))
def buy(package):
    """Buy credits — opens Stripe checkout."""
    from scanner.zeroos_cli.config_utils import load_token
    from scanner.zeroos_cli.api_client import ZeroAPIClient

    token = load_token()
    if not token:
        fail('not authenticated. run:')
        action('zeroos init')
        raise SystemExit(1)

    api = ZeroAPIClient(token)

    try:
        result = api.create_checkout(package)
    except Exception as e:
        fail(str(e))
        raise SystemExit(1)

    if 'error' in result:
        fail(result['error'])
        raise SystemExit(1)

    url = result.get('url', '')
    if url:
        spacer()
        success(f'checkout ready for {package} package.')
        spacer()
        console.print(f'  [lime]{url}[/lime]')
        spacer()

        # try to open browser
        try:
            import webbrowser
            webbrowser.open(url)
            console.print('  [dim]opened in your browser.[/dim]')
        except Exception:
            console.print('  [dim]open the URL above to complete purchase.[/dim]')
        spacer()
    else:
        fail('could not create checkout session.')
        raise SystemExit(1)
