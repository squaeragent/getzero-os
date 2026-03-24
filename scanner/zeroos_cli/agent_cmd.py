"""zeroos agent — Multi-agent management for the zero network."""

import os
import json
import uuid

import click

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
        click.echo("  ✗ no auth token. run: zeroos auth login")
        raise SystemExit(1)

    agent_id = str(uuid.uuid4())
    desc, max_pos = PRESETS[preset]

    click.echo(f"  ▸ registering agent/{preset} ............. ", nl=False)

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        # Challenge
        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/challenge",
            data=json.dumps({"agent_id": agent_id}).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            challenge = json.loads(resp.read())

        nonce = challenge["nonce"]

        # For add, use the stored keystore — get the key
        from scanner.zeroos_cli.keystore import load_key
        password = click.prompt("  encryption password", hide_input=True)
        key = load_key(password)

        from eth_account import Account
        from eth_account.messages import encode_defunct

        clean = key if key.startswith("0x") else "0x" + key
        acct = Account.from_key(clean)
        message = f"zero-agent-register:{agent_id}:{nonce}"
        signed = acct.sign_message(encode_defunct(text=message))

        # Register
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
            click.echo("failed")
            click.echo(f"  ✗ {result['error']}")
            if result.get("upgrade"):
                click.echo(f"  ℹ {result['upgrade']}")
            raise SystemExit(1)

        click.echo("done")
        page_url = result.get("page_url", "")
        click.echo(f"  ▸ your agent page: {page_url}")
        click.echo()
        click.echo(f"  agent/{preset} added. paper mode.")
        click.echo()

    except SystemExit:
        raise
    except Exception as e:
        click.echo("failed")
        click.echo(f"  ✗ {e}")
        raise SystemExit(1)


@agent.command()
@click.argument("agent_id")
def remove(agent_id: str):
    """Deactivate an agent and revoke signal access."""
    import urllib.request

    token = _get_token()
    if not token:
        click.echo("  ✗ no auth token. run: zeroos auth login")
        raise SystemExit(1)

    click.echo(f"  ▸ deactivating agent {agent_id[:8]}... ", nl=False)

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
            click.echo("done")
            click.echo("  ▸ agent deactivated. signal access revoked.")
            click.echo("  ℹ your on-chain positions remain open.")
            click.echo("  ℹ close them manually on hyperliquid if needed.")
        else:
            click.echo("failed")
            click.echo(f"  ✗ {result.get('error', 'unknown error')}")

    except Exception as e:
        click.echo("failed")
        click.echo(f"  ✗ {e}")
        click.echo("  ℹ try the web dashboard: getzero.dev/app")
        raise SystemExit(1)


@agent.command(name="list")
def list_agents():
    """List your registered agents."""
    agents_file = os.path.join(ZEROOS_DIR, "agents.json")
    agents = []
    if os.path.exists(agents_file):
        with open(agents_file) as f:
            agents = json.load(f).get("agents", [])

    net = _get_network_info()
    if net and not agents:
        # Legacy single-agent format
        click.echo(f"  agent_id: {net.get('agent_id', '?')[:8]}...")
        click.echo(f"  page: {net.get('page_url', 'not registered')}")
        click.echo(f"  tier: {net.get('tier', 'unknown')}")
        return

    if not agents:
        click.echo("  no agents registered.")
        click.echo("  run: zeroos agent add --preset balanced")
        return

    click.echo()
    for a in agents:
        status_color = "green" if a.get("status") == "running" else "yellow" if a.get("status") == "paused" else "red"
        status = click.style(a.get("status", "unknown").upper(), fg=status_color)
        preset = a.get("preset", "?")
        mode = a.get("mode", "paper")
        agent_id = a.get("id", "?")[:8]
        click.echo(f"  {agent_id}  agent/{preset}  {status}  {mode}")
    click.echo()


@agent.command()
@click.argument("name")
def pause(name: str):
    """Pause an agent without deregistering."""
    agents_file = os.path.join(ZEROOS_DIR, "agents.json")
    if not os.path.exists(agents_file):
        click.echo("  ✗ no agents found.")
        raise SystemExit(1)

    with open(agents_file) as f:
        data = json.load(f)

    found = False
    for a in data.get("agents", []):
        if a.get("preset") == name or a.get("id", "").startswith(name):
            a["status"] = "paused"
            found = True
            click.echo(f"  ▸ agent/{a.get('preset', name)} paused.")
            click.echo("  positions remain open. immune system stays active.")
            click.echo("  new evaluations suspended.")
            break

    if not found:
        click.echo(f"  ✗ agent '{name}' not found. run: zeroos agent list")
        raise SystemExit(1)

    with open(agents_file, "w") as f:
        json.dump(data, f, indent=2)


@agent.command()
@click.argument("name")
def resume(name: str):
    """Resume a paused agent."""
    agents_file = os.path.join(ZEROOS_DIR, "agents.json")
    if not os.path.exists(agents_file):
        click.echo("  ✗ no agents found.")
        raise SystemExit(1)

    with open(agents_file) as f:
        data = json.load(f)

    found = False
    for a in data.get("agents", []):
        if a.get("preset") == name or a.get("id", "").startswith(name):
            a["status"] = "running"
            found = True
            click.echo(f"  ▸ agent/{a.get('preset', name)} resumed.")
            click.echo("  evaluation cycle restarted.")
            break

    if not found:
        click.echo(f"  ✗ agent '{name}' not found. run: zeroos agent list")
        raise SystemExit(1)

    with open(agents_file, "w") as f:
        json.dump(data, f, indent=2)
