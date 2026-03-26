"""zeroos mode — view and control agent modes."""

import json

import click

from scanner.zeroos_cli.console import console, spacer, rule, section, dots


@click.group(invoke_without_command=True)
@click.pass_context
def mode(ctx):
    """View or control agent modes."""
    if ctx.invoked_subcommand is None:
        _show_modes()


def _show_modes():
    from scanner.v6.mode_manager import ModeManager, RISK_PARAMS
    mm = ModeManager()
    m = mm.get_modes()

    spacer()
    console.print("  [header]◆ zero▮[/header] [mid]modes[/mid]")
    spacer()
    rule()
    spacer()

    section("STRATEGY")
    dots("strategy", m['strategy'])
    dots("direction", m['direction'])
    if m.get('per_coin_overrides'):
        for coin, ov in m['per_coin_overrides'].items():
            dots(f"  {coin}", ov.get('strategy', '—'))
    spacer()

    section("RISK")
    dots("risk level", m['risk'])
    params = m.get('risk_params', {})
    dots("position size", f"{params.get('position_size', 0):.0%}")
    dots("max positions", str(params.get('max_positions', 0)))
    dots("stop loss", f"{params.get('stop', 0):.1%}")
    dots("circuit breaker", f"{params.get('circuit_breaker', 0):.0%}")
    dots("size multiplier", f"{m.get('size_multiplier', 1.0)}x")
    spacer()

    section("STATE")
    state = m['state']
    state_color = {'active': 'success', 'observe': 'warning', 'sleep': 'dim', 'exit_only': 'error', 'paper': 'warning'}
    tag = state_color.get(state, 'mid')
    dots("state", f"[{tag}]{state}[/{tag}]")
    if m.get('wake_condition'):
        dots("wake condition", m['wake_condition'])
    spacer()

    section("SCOPE")
    dots("scope", m['scope'])
    if m.get('scope_coins'):
        dots("coins", ', '.join(m['scope_coins']))
    spacer()

    if m.get('conditions'):
        section("CONDITIONS")
        for c in m['conditions']:
            dots(c['trigger'], c['action'])
        spacer()

    if m.get('updated_at'):
        console.print(f"  [dim]updated: {m['updated_at']}[/dim]")
        spacer()

    rule()
    spacer()


@mode.command('set')
@click.argument('dimension')
@click.argument('value')
def mode_set(dimension, value):
    """Set a mode dimension. Usage: zeroos mode set <dimension> <value>"""
    from scanner.v6.mode_manager import ModeManager
    mm = ModeManager()

    handlers = {
        'strategy': lambda: mm.set_strategy(value),
        'direction': lambda: mm.set_direction(value),
        'risk': lambda: mm.set_risk(value),
        'state': lambda: mm.set_state(value),
        'scope': lambda: mm.set_scope(value.split(',') if ',' in value else value),
    }

    if dimension not in handlers:
        console.print(f"  [error]✗[/error] [mid]unknown dimension: {dimension}[/mid]")
        console.print(f"  [dim]options: {', '.join(handlers.keys())}[/dim]")
        raise SystemExit(1)

    result = handlers[dimension]()
    if 'error' in result:
        console.print(f"  [error]✗[/error] [mid]{result['error']}[/mid]")
        raise SystemExit(1)

    spacer()
    console.print(f"  [success]✓[/success] [mid]{dimension} → {value}[/mid]")
    spacer()


@mode.command('reset')
def mode_reset():
    """Reset all modes to defaults."""
    from scanner.v6.mode_manager import ModeManager, DEFAULT_MODES, MODE_STATE_FILE
    import json
    MODE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_STATE_FILE.write_text(json.dumps(dict(DEFAULT_MODES), indent=2))
    spacer()
    console.print("  [success]✓[/success] [mid]all modes reset to defaults[/mid]")
    spacer()


@mode.command('history')
def mode_history():
    """Show last 10 mode changes."""
    from scanner.v6.mode_manager import ModeManager
    mm = ModeManager()
    m = mm.get_modes()
    history = m.get('history', [])

    spacer()
    console.print("  [header]◆ zero▮[/header] [mid]mode history[/mid]")
    spacer()
    rule()
    spacer()

    if not history:
        console.print("  [dim]no mode changes recorded.[/dim]")
    else:
        for entry in reversed(history):
            ts = entry.get('timestamp', '?')[:19]
            dim = entry.get('dimension', '?')
            frm = entry.get('from', '?')
            to = entry.get('to', '?')
            console.print(f"  [dim]{ts}[/dim]  [bright]{dim}[/bright]  {frm} → [lime]{to}[/lime]")

    spacer()
    rule()
    spacer()
