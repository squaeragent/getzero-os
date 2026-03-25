"""zeroos agent — multi-agent management for the zero network."""

import os
import json
import uuid

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, dots, success, fail, info,
)

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
NETWORK_PATH = os.path.join(ZEROOS_DIR, "network.json")
ZERO_API = "https://getzero.dev"

PRESETS = {
    "conservative": ("BTC/ETH only, few trades, high conviction", 2),
    "balanced": ("moderate risk, the default", 3),
    "degen": ("more trades, wider universe", 6),
    "funding": ("collect funding payments, delta-neutral", 4),
}


def _get_token() -> str | None:
    token_path = os.path.join(ZEROOS_DIR, "auth_token")
    if os.path.exists(token_path):
        with open(token_path) as f:
            return f.read().strip()
    return None


def _get_network_info() -> dict | None:
    if os.path.exists(NETWORK_PATH):
        with open(NETWORK_PATH) as f:
            return json.load(f)
    return None


@click.group()
def agent():
    """Manage agents on the zero network."""
    pass


@agent.command()
@click.option("--preset", type=click.Choice(list(PRESETS.keys())), required=True, help="Agent preset")
def add(preset: str):
    """Add a new agent to the zero network."""
    import urllib.request

    token = _get_token()
    if not token:
        fail("no auth token.")
        console.print("  [lime]$ zeroos auth login[/lime]")
        raise SystemExit(1)

    agent_id = str(uuid.uuid4())
    desc, max_pos = PRESETS[preset]

    spacer()
    logo()
    spacer()

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/challenge",
            data=json.dumps({"agent_id": agent_id}).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            challenge = json.loads(resp.read())

        nonce = challenge["nonce"]

        from scanner.zeroos_cli.keystore import load_key
        password = click.prompt(
            click.style("  encryption password", fg=(119, 119, 119)),
            hide_input=True, prompt_suffix="\n  > ",
        )
        key = load_key(password)

        from eth_account import Account
        from eth_account.messages import encode_defunct

        clean = key if key.startswith("0x") else "0x" + key
        acct = Account.from_key(clean)
        message = f"zero-agent-register:{agent_id}:{nonce}"
        signed = acct.sign_message(encode_defunct(text=message))

        reg_data = {
            "agent_id": agent_id,
            "agent_type": "zeroos_cli",
            "preset": preset,
            "mode": "paper",
            "nonce": nonce,
            "signature": signed.signature.hex(),
            "wallet_address": acct.address,
            "version": "0.1.0",
        }

        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/register",
            data=json.dumps(reg_data).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        if "error" in result:
            fail(result["error"])
            if result.get("upgrade"):
                console.print(f"  [dim]{result['upgrade']}[/dim]")
            raise SystemExit(1)

        success(f"agent/{preset} added. paper mode.")
        page_url = result.get("page_url", "")
        if page_url:
            dots("▸ agent page", page_url)
        spacer()

    except SystemExit:
        raise
    except Exception as e:
        fail(str(e))
        raise SystemExit(1)


@agent.command()
@click.argument("agent_id")
def remove(agent_id: str):
    """Deactivate an agent and revoke signal access."""
    import urllib.request

    token = _get_token()
    if not token:
        fail("no auth token.")
        console.print("  [lime]$ zeroos auth login[/lime]")
        raise SystemExit(1)

    spacer()
    logo()
    spacer()

    try:
        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/{agent_id}",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        if result.get("ok"):
            success("agent deactivated. signal access revoked.")
            console.print("  [dim]your on-chain positions remain open.[/dim]")
            console.print("  [dim]close them manually on hyperliquid if needed.[/dim]")
        else:
            fail(result.get("error", "unknown error"))

    except Exception as e:
        fail(str(e))
        console.print("  [dim]try the web dashboard: getzero.dev/app[/dim]")
        raise SystemExit(1)

    spacer()


@agent.command(name="list")
def list_agents():
    """List your registered agents."""
    spacer()
    logo()
    spacer()
    rule()
    spacer()

    agents_file = os.path.join(ZEROOS_DIR, "agents.json")
    agents = []
    if os.path.exists(agents_file):
        with open(agents_file) as f:
            agents = json.load(f).get("agents", [])

    net = _get_network_info()
    if net and not agents:
        dots("agent_id", net.get("agent_id", "?")[:8] + "...")
        dots("page", net.get("page_url", "not registered"))
        dots("tier", net.get("tier", "unknown"))
        spacer()
        return

    if not agents:
        console.print("  [dim]no agents registered.[/dim]")
        console.print("  [lime]$ zeroos agent add --preset balanced[/lime]")
        spacer()
        return

    for a in agents:
        status_val = a.get("status", "unknown")
        if status_val == "running":
            status_str = "[success]● running[/success]"
        elif status_val == "paused":
            status_str = "[warning]● paused[/warning]"
        else:
            status_str = f"[error]● {status_val}[/error]"

        preset = a.get("preset", "?")
        mode = a.get("mode", "paper")
        aid = a.get("id", "?")[:8]
        console.print(f"  [dim]{aid}[/dim]  [mid]agent/{preset}[/mid]  {status_str}  [dim]{mode}[/dim]")

    spacer()


@agent.command()
@click.argument("name")
def pause(name: str):
    """Pause an agent without deregistering."""
    agents_file = os.path.join(ZEROOS_DIR, "agents.json")
    if not os.path.exists(agents_file):
        fail("no agents found.")
        raise SystemExit(1)

    with open(agents_file) as f:
        data = json.load(f)

    found = False
    for a in data.get("agents", []):
        if a.get("preset") == name or a.get("id", "").startswith(name):
            a["status"] = "paused"
            found = True
            success(f"agent/{a.get('preset', name)} paused.")
            console.print("  [dim]positions remain open. immune system stays active.[/dim]")
            console.print("  [dim]new evaluations suspended.[/dim]")
            break

    if not found:
        fail(f"agent '{name}' not found.")
        console.print("  [lime]$ zeroos agent list[/lime]")
        raise SystemExit(1)

    with open(agents_file, "w") as f:
        json.dump(data, f, indent=2)


@agent.command()
@click.argument("name")
def resume(name: str):
    """Resume a paused agent."""
    agents_file = os.path.join(ZEROOS_DIR, "agents.json")
    if not os.path.exists(agents_file):
        fail("no agents found.")
        raise SystemExit(1)

    with open(agents_file) as f:
        data = json.load(f)

    found = False
    for a in data.get("agents", []):
        if a.get("preset") == name or a.get("id", "").startswith(name):
            a["status"] = "running"
            found = True
            success(f"agent/{a.get('preset', name)} resumed.")
            console.print("  [dim]evaluation cycle restarted.[/dim]")
            break

    if not found:
        fail(f"agent '{name}' not found.")
        console.print("  [lime]$ zeroos agent list[/lime]")
        raise SystemExit(1)

    with open(agents_file, "w") as f:
        json.dump(data, f, indent=2)
