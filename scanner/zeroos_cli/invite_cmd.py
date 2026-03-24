"""zeroos invite — generate referral code."""

import json
import os
from urllib.request import Request, urlopen

import click

from scanner.zeroos_cli.style import Z

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
    print()
    print(f'  {Z.logo()}')
    print()

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
        print(f'  {Z.fail("no invite code found.")}')
        print(f'  {Z.lime("$ zeroos init")}')
        print()
        raise SystemExit(1)

    invite_url = f"https://getzero.dev/waitlist?ref={referral}"

    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.bright(invite_url)}')
    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.dim("they install → both get 14d pro.")}')
    print(f'  {Z.dim("you earn 10% of their subscription.")}')
    print(f'  {Z.dim("forever. not 12 months. forever.")}')
    print()
    print(f'  {Z.rule()}')
    print()

    try:
        import subprocess
        subprocess.run(
            ["pbcopy"],
            input=invite_url.encode(),
            capture_output=True,
        )
        print(f'  {Z.success("copied to clipboard.")}')
    except Exception:
        print(f'  {Z.dim("copy the link above and share it.")}')

    print()
