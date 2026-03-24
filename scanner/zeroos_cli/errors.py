"""zero CLI error messages. helpful, not just descriptive."""

from scanner.zeroos_cli.style import Z

ERRORS = {
    'no_token': (
        f'  {Z.fail("activation required.")}\n'
        f'  {Z.dim("you need a token to start trading.")}\n'
        f'  {Z.dim("apply at")} {Z.lime("getzero.dev/waitlist")}\n'
        f'  {Z.dim("or enter your token:")}\n'
        f'  {Z.lime("$ zeroos init --token YOUR_TOKEN")}'
    ),
    'token_expired': (
        f'  {Z.fail("token expired.")}\n'
        f'  {Z.dim("your activation token has expired.")}\n'
        f'  {Z.dim("contact")} {Z.lime("t.me/zero_operators")} {Z.dim("for a new one.")}'
    ),
    'token_invalid': (
        f'  {Z.fail("token invalid.")}\n'
        f'  {Z.dim("this token is not recognized.")}\n'
        f'  {Z.dim("check for typos or apply at")} {Z.lime("getzero.dev/waitlist")}'
    ),
    'hl_connection': (
        f'  {Z.fail("couldn\'t connect to Hyperliquid.")}\n'
        f'  {Z.dim("check your internet connection.")}\n'
        f'  {Z.dim("HL status:")} {Z.lime("status.hyperliquid.xyz")}'
    ),
    'key_invalid': (
        f'  {Z.fail("invalid key format.")}\n'
        f'  {Z.dim("expected 64 hex characters (no 0x prefix).")}\n'
        f'  {Z.dim("find your key in your HL wallet export.")}'
    ),
    'already_running': (
        f'  {Z.warn("zero is already running.")}\n'
        f'  {Z.dim("your agent is active. check:")}\n'
        f'  {Z.lime("$ zeroos status")}'
    ),
    'not_running': (
        f'  {Z.warn("zero is not running.")}\n'
        f'  {Z.dim("start your agent:")}\n'
        f'  {Z.lime("$ zeroos start")}'
    ),
    'network_unreachable': (
        f'  {Z.warn("zero network unreachable.")}\n'
        f'  {Z.dim("your agent continues trading independently.")}\n'
        f'  {Z.dim("network features paused until connection restored.")}'
    ),
}
