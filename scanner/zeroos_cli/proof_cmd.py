"""zeroos proof — Generate and verify proofs of operator achievements."""
import sys, json, click
from pathlib import Path

@click.command()
@click.option("--generate", "gen_type", help="Generate proof: run|score|performance|protection")
@click.option("--verify", "verify_id", help="Verify a proof by ID")
def proof(gen_type, verify_id):
    """Generate or verify proofs of achievement."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path: sys.path.insert(0, v6)
    from intelligence_expansions import generate_proof, verify_proof, list_proofs

    if verify_id:
        result = verify_proof(verify_id)
        icon = "✓" if result.get("valid") else "✗"
        click.echo(f"\n  {icon} {verify_id}: {'valid' if result.get('valid') else result.get('reason','invalid')}\n")
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
                    except: pass
            data = {"days": days}
        elif gen_type == "score":
            try:
                from scanner.v6.zero_score import score_from_db
                result = score_from_db()
                data = {"score": result.get("effective_score", 0), "components": result.get("components", {})}
            except: data = {"score": 0}
        p = generate_proof(f"proof_of_{gen_type}", data)
        click.echo(f"\n  PROOF GENERATED")
        click.echo(f"  ────────────────────────────────────────")
        click.echo(f"  type: {p.get('type','?')}")
        if p.get("tier"): click.echo(f"  tier: {p['tier']}")
        click.echo(f"  id: {p.get('id','?')}")
        click.echo(f"  verify: {p.get('verify_url','?')}\n")
        return

    # List proofs
    proofs = list_proofs()
    click.echo(f"\n  YOUR PROOFS ({len(proofs)})")
    click.echo(f"  ────────────────────────────────────────")
    if not proofs:
        click.echo(f"  none yet. generate with: zeroos proof --generate run")
    for p in proofs[:10]:
        tier = f" ({p['tier']})" if p.get("tier") else ""
        click.echo(f"  ▸ {p['type']}{tier} — {p['id']}")
    click.echo()
