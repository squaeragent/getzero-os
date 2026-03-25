"""Shared API client for zero-skills.

Works standalone (pip install zero-skills) or within zeroos.
Uses stdlib only — no external dependencies.
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

_client = None

API_BASE = os.environ.get("ZEROOS_API", "https://getzero.dev")


def _load_token() -> str:
    """Load token from env, config file, or zeroos config."""
    # 1. Environment variable
    token = os.environ.get("ZEROOS_TOKEN", "")
    if token:
        return token

    # 2. zero-skills config
    config_path = Path.home() / ".zeroos" / "token"
    if config_path.exists():
        return config_path.read_text().strip()

    # 3. Try zeroos CLI config
    yaml_path = Path.home() / ".zeroos" / "config.yaml"
    if yaml_path.exists():
        for line in yaml_path.read_text().splitlines():
            if "token:" in line:
                return line.split(":", 1)[1].strip().strip("'\"")

    return ""


class _SkillsClient:
    """Lightweight HTTP client for zero API (stdlib only)."""

    def __init__(self, token: str):
        self.token = token
        self.base = API_BASE.rstrip("/")

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode())
            except Exception:
                err_body = {"error": str(e)}
            err_body["status"] = e.code
            return err_body
        except Exception as e:
            return {"error": str(e)}

    def evaluate(self, coin: str) -> dict:
        return self._request("POST", "/api/evaluate", {"coin": coin})

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, data: dict) -> dict:
        return self._request("POST", path, data)


class _EvalResult:
    """Wrapper to give evaluate results attribute access."""
    def __init__(self, data: dict):
        self._data = data
        self.regime = data.get("regime", "unknown")
        self.confidence = data.get("confidence", data.get("regime_confidence", 0))
        self.direction = data.get("direction", "NEUTRAL")
        self.consensus_value = data.get("consensus_value", 0)
        self.consensus_label = data.get("consensus", "")
        self.conviction_level = data.get("conviction", "low")
        self.verdict = data.get("verdict", "skip")
        self.reasoning = data.get("reasoning", "")
        self.quality = data.get("quality", 0)

    def get(self, key, default=None):
        return self._data.get(key, default)


def _get_client():
    global _client
    if _client is None:
        token = _load_token()
        _client = _SkillsClient(token)
    return _client


def _evaluate(coin: str) -> _EvalResult:
    """Evaluate and return an _EvalResult with attribute access."""
    client = _get_client()
    data = client.evaluate(coin)
    return _EvalResult(data)
