"""zeroos proof — generate and verify proofs of operator achievements."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, success, info,
)


@click.command()
@click.option("--generate", "gen_type", help="Generate proof: run|score|performance|protection")
@click.option("--verify", "verify_id", help="Verify a proof by ID")
def proof(gen_type, verify_id):
    """Generate or verify proofs of achievement."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    from intelligence_expansions import generate_proof, verify_proof, list_proofs

    spacer()
    logo()
    spacer()

    if verify_id:
        result = verify_proof(verify_id)
        if result.get("valid"):
            success(f"{verify_id}: valid")
        else:
            fail(f"{verify_id}: {result.get('reason', 'invalid')}")
        spacer()
        return

    if gen_type:
        data = {}
        state_dir = Path.home() / ".zeroos" / "state"
        if gen_type == "run":
            hb = state_dir / "heartbeat.json"
            days = 0
            if hb.exists():
                from datetime import datetime, timezone
                hbd = json.loads(hb.read_text())
                started = hbd.get("started_at", "")
                if started:
                    try:
                        dt = datetime.fromisoformat(started)
                        days = int((datetime.now(timezone.utc) - dt).total_seconds() / 86400)
                    except Exception:
                        pass
            data = {"days": days}
        elif gen_type == "score":
            try:
                from scanner.v6.zero_score import score_from_db
                result = score_from_db()
                data = {"score": result.get("effective_score", 0), "components": result.get("components", {})}
            except Exception:
                data = {"score": 0}

        p = generate_proof(f"proof_of_{gen_type}", data)

        rule()
        spacer()
        section("PROOF GENERATED")
        dots("type", p.get("type", "?"))
        if p.get("tier"):
            dots("tier", p["tier"])
        dots("id", p.get("id", "?"))
        dots("verify", p.get("verify_url", "?"))
        spacer()
        return

    # List proofs
    proofs = list_proofs()

    rule()
    spacer()
    section(f"YOUR PROOFS ({len(proofs)})")
    spacer()

    if not proofs:
        console.print("  [dim]none yet.[/dim]")
        console.print("  [lime]$ zeroos proof --generate run[/lime]")
    for p in proofs[:10]:
        tier = f" ({p['tier']})" if p.get("tier") else ""
        info(f"{p['type']}{tier} — {p['id']}")

    spacer()
