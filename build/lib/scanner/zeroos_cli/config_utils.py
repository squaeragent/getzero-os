"""Token and config loading utilities."""

from pathlib import Path
import json


def load_token() -> str | None:
    token_path = Path.home() / '.zeroos' / 'token'
    if token_path.exists():
        return token_path.read_text().strip()
    # fallback to legacy auth_token
    legacy = Path.home() / '.zeroos' / 'auth_token'
    if legacy.exists():
        return legacy.read_text().strip()
    return None


def save_token(token: str):
    token_dir = Path.home() / '.zeroos'
    token_dir.mkdir(exist_ok=True)
    token_path = token_dir / 'token'
    token_path.write_text(token)
    token_path.chmod(0o600)
