"""zeroos invite — generate referral code."""

import json
import os
from urllib.request import Request, urlopen

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, fail, success,
)

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
NETWORK_PATH = os.path.join(ZEROOS_DIR, "network.json")
ZERO_API = "https://getzero.dev"


def _get_referral_code() -> str | None:
    if os.path.exists(NETWORK_PATH):
        with open(NETWORK_PATH) as f:
            data = json.load(f)
            return data.get("referral_code")
    return None


@click.command()
@click.option("--new", is_flag=True, help="Generate a fresh invite link.")
def invite(new: bool):
    """Generate an invite link. Both operators get 14 days Pro free."""
    spacer()
    logo()
    spacer()

    referral = _get_referral_code()

    if not referral:
        try:
            req = Request(f"{ZERO_API}/api/invite", method="GET")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                referral = data.get("code")
        except Exception:
            pass

    if not referral:
        fail("no invite code found.")
        console.print("  [lime]$ zeroos init[/lime]")
        spacer()
        raise SystemExit(1)

    invite_url = f"https://getzero.dev/waitlist?ref={referral}"

    rule()
    spacer()
    console.print(f"  [bright]{invite_url}[/bright]")
    spacer()
    rule()
    spacer()
    console.print("  [dim]they install → both get 14d pro.[/dim]")
    console.print("  [dim]you earn 10% of their subscription.[/dim]")
    console.print("  [dim]forever. not 12 months. forever.[/dim]")
    spacer()
    rule()
    spacer()

    try:
        import subprocess
        subprocess.run(
            ["pbcopy"],
            input=invite_url.encode(),
            capture_output=True,
        )
        success("copied to clipboard.")
    except Exception:
        console.print("  [dim]copy the link above and share it.[/dim]")

    spacer()
