"""zeroos arena — view your arena rank and leaderboard."""

import click

from scanner.zeroos_cli.console import console, spacer


@click.command()
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON.')
def arena(as_json):
    """View your arena rank and leaderboard."""
    if as_json:
        import json
        click.echo(json.dumps({'note': 'arena data coming soon. visit app.getzero.dev/arena'}))
        return

    spacer()
    console.print('  [header]◆ zero▮[/header] [mid]arena[/mid]')
    spacer()
    console.print('  [dim]arena leaderboard: app.getzero.dev/arena[/dim]')
    console.print('  [dim]your score: zeroos score[/dim]')
    spacer()

    # Weekly reward tiers
    console.print('  [mid]weekly rewards:[/mid]')
    console.print('  [green]#1[/green]  5,000 credits')
    console.print('  [green]#2[/green]  3,000 credits')
    console.print('  [green]#3[/green]  2,000 credits')
    console.print('  [dim]#4-10[/dim]  500 credits each')
    spacer()

    # Score multipliers
    console.print('  [mid]score multipliers:[/mid]')
    console.print('  6.0-6.9  1.2x')
    console.print('  7.0-7.9  1.5x')
    console.print('  8.0-8.9  2.0x')
    console.print('  9.0+     3.0x')
    spacer()
