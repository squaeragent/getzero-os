"""Shared API client for zero-skills."""

import os

_client = None

def _get_client():
    global _client
    if _client is None:
        from scanner.zeroos_cli.api_client import ZeroAPIClient
        from scanner.zeroos_cli.config_utils import load_token
        token = load_token() or os.environ.get("ZEROOS_TOKEN", "")
        _client = ZeroAPIClient(token)
    return _client
