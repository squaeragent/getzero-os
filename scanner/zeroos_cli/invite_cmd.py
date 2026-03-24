"""zeroos invite — Generate an invite link. 14 days Pro free for both."""

import json
import os
from urllib.request import Request, urlopen

import click

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
NETWORK_PATH = os.path.join(ZEROOS_DIR, "network.json")
ZERO_API = "https://getzero.dev"


def _get_referral_code() -> str | None:
    """Get referral code from local network.json."""
    if os.path.exists(NETWORK_PATH):
        with open(NETWORK_PATH) as f:
            data = json.load(f)
            return data.get("referral_code")
    return None


@click.command()
@click.option("--new", is_flag=True, help="Generate a fresh invite link.")
def invite(new: bool):
    """Generate an invite link. Both operators get 14 days Pro free."""
    click.echo()

    referral = _get_referral_code()

    if not referral:
        # Try to generate via API
        try:
            req = Request(
                f"{ZERO_API}/api/invite",
                method="GET",
            )
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                referral = data.get("code")
        except Exception:
            pass

    if not referral:
        click.echo("  ✗ no invite code found.")
        click.echo("  run: zeroos init")
        raise SystemExit(1)

    invite_url = f"https://getzero.dev/waitlist?ref={referral}"

    click.echo("  ■ ZERO INVITE")
    click.echo()
    click.echo(f"  {invite_url}")
    click.echo()
    click.echo("  ─────────────────────────────────")
    click.echo("  they install → both get 14d Pro.")
    click.echo("  you earn 10% of their subscription.")
    click.echo("  forever. not 12 months. forever.")
    click.echo("  ─────────────────────────────────")
    click.echo()

    try:
        import subprocess
        subprocess.run(
            ["pbcopy"],
            input=invite_url.encode(),
            capture_output=True,
        )
        click.echo("  copied to clipboard ✓")
    except Exception:
        click.echo("  copy the link above and share it.")

    click.echo()
