"""zeroos feedback — report bugs or request features."""

import click
import json
import urllib.request
from datetime import datetime, timezone

from scanner.zeroos_cli.style import Z


@click.command()
@click.argument("message", nargs=-1, required=True)
@click.option("--type", "fb_type", type=click.Choice(["bug", "feature", "question"]), default="bug")
def feedback(message: tuple, fb_type: str):
    """Send feedback to the zero team."""
    text = " ".join(message)
    if not text.strip():
        print(f'  {Z.dim("usage: zeroos feedback \'your message here\'")}')
        return

    from scanner.zeroos_cli import __version__

    payload = {
        "type": fb_type,
        "message": text,
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        import os
        url = os.environ.get("ZEROOS_API_URL", "https://getzero.dev")
        req = urllib.request.Request(
            f"{url}/api/feedback",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f'  {Z.success("feedback sent. thank you.")}')
                return
    except Exception:
        pass

    from pathlib import Path
    fb_dir = Path.home() / ".zeroos" / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (fb_dir / f"{ts}.json").write_text(json.dumps(payload, indent=2))
    print(f'  {Z.success("saved locally (network unavailable). will sync on next connection.")}')
