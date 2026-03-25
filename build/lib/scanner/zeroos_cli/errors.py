"""zero CLI error messages — Rich-powered, helpful, not just descriptive."""

from scanner.zeroos_cli.console import console, fail, warn


def show_error(key: str):
    """Print a named error message."""
    fn = _ERRORS.get(key)
    if fn:
        fn()
    else:
        fail(f"unknown error: {key}")


def _no_token():
    fail("activation required.")
    console.print("  [dim]you need a token to start trading.[/dim]")
    console.print("  [dim]apply at[/dim] [lime]getzero.dev/waitlist[/lime]")
    console.print("  [dim]or enter your token:[/dim]")
    console.print("  [lime]$ zeroos init --token YOUR_TOKEN[/lime]")


def _token_expired():
    fail("token expired.")
    console.print("  [dim]your activation token has expired.[/dim]")
    console.print("  [dim]contact[/dim] [lime]t.me/zero_operators[/lime] [dim]for a new one.[/dim]")


def _token_invalid():
    fail("token invalid.")
    console.print("  [dim]this token is not recognized.[/dim]")
    console.print("  [dim]check for typos or apply at[/dim] [lime]getzero.dev/waitlist[/lime]")


def _hl_connection():
    fail("couldn't connect to Hyperliquid.")
    console.print("  [dim]check your internet connection.[/dim]")
    console.print("  [dim]HL status:[/dim] [lime]status.hyperliquid.xyz[/lime]")


def _key_invalid():
    fail("invalid key format.")
    console.print("  [dim]expected 64 hex characters (no 0x prefix).[/dim]")
    console.print("  [dim]find your key in your HL wallet export.[/dim]")


def _already_running():
    warn("zero is already running.")
    console.print("  [dim]your agent is active. check:[/dim]")
    console.print("  [lime]$ zeroos status[/lime]")


def _not_running():
    warn("zero is not running.")
    console.print("  [dim]start your agent:[/dim]")
    console.print("  [lime]$ zeroos start[/lime]")


def _network_unreachable():
    warn("zero network unreachable.")
    console.print("  [dim]your agent continues trading independently.[/dim]")
    console.print("  [dim]network features paused until connection restored.[/dim]")


_ERRORS = {
    'no_token': _no_token,
    'token_expired': _token_expired,
    'token_invalid': _token_invalid,
    'hl_connection': _hl_connection,
    'key_invalid': _key_invalid,
    'already_running': _already_running,
    'not_running': _not_running,
    'network_unreachable': _network_unreachable,
}
