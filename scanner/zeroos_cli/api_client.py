"""HTTP client for the zero API — stdlib only."""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass

API_BASE = 'https://getzero.dev/api'


@dataclass
class EvalResult:
    coin: str
    regime: str
    confidence: str  # high/medium/low
    direction: str
    consensus_label: str
    consensus_value: float
    conviction_level: str
    verdict: str
    reasoning: str
    entry_price: float
    stop_price: float
    position_size_pct: float
    credits_after: int = -1


@dataclass
class CreditsResult:
    balance: int
    total_purchased: int
    total_used: int
    genesis: bool
    estimated_days: int = 0


class ZeroAPIClient:
    def __init__(self, token: str = None):
        self.token = token

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        url = f'{API_BASE}{path}'
        body = json.dumps(data).encode() if data else None
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode()
            try:
                return json.loads(resp_body)
            except Exception:
                return {'error': str(e), 'status': e.code}

    def evaluate(self, coin: str) -> EvalResult:
        data = self._request('POST', '/evaluate', {'coin': coin.upper()})
        if 'error' in data:
            raise Exception(data['error'])
        return EvalResult(
            coin=data.get('coin', coin),
            regime=data.get('regime', 'unknown'),
            confidence=data.get('regime_confidence', 'unknown'),
            direction=data.get('direction', 'neutral'),
            consensus_label=data.get('consensus', 'unknown'),
            consensus_value=data.get('consensus_value', 0),
            conviction_level=data.get('conviction', 'low'),
            verdict=data.get('verdict', 'skip'),
            reasoning=data.get('reasoning', ''),
            entry_price=data.get('entry_price', 0),
            stop_price=data.get('stop_price', 0),
            position_size_pct=data.get('position_size_pct', 0),
        )

    def get_credits(self) -> CreditsResult:
        data = self._request('GET', '/credits')
        if 'error' in data:
            return CreditsResult(balance=0, total_purchased=0, total_used=0, genesis=False)
        return CreditsResult(
            balance=data.get('balance', 0),
            total_purchased=data.get('total_purchased', 0),
            total_used=data.get('total_used', 0),
            genesis=data.get('genesis', False),
        )

    def create_checkout(self, package: str) -> dict:
        return self._request('POST', '/credits/purchase', {'package': package})

    def validate_token(self) -> dict:
        return self._request('POST', '/auth/validate-token')
