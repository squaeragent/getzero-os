"""Agent Mode System — 6-dimension control for ZERO OS trading agents."""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

MODE_STATE_FILE = Path(__file__).parent / 'bus' / 'modes.json'

# Defaults
DEFAULT_MODES = {
    'strategy': 'momentum',
    'direction': 'both',
    'risk': 'normal',
    'state': 'active',
    'scope': 'broad',
    'conditions': [],
    'per_coin_overrides': {},
    'size_multiplier': 1.0,
    'updated_at': None,
    'history': []
}

STRATEGIES = ['momentum', 'mean_revert', 'breakout', 'sniper', 'scalp', 'grid']
DIRECTIONS = ['long_only', 'both', 'short_only', 'funding_harvest']
RISK_LEVELS = ['defense', 'normal', 'aggressive']
STATES = ['active', 'observe', 'sleep', 'exit_only', 'paper']
SCOPES = ['focused', 'broad', 'full']

RISK_PARAMS = {
    'defense': {'position_size': 0.05, 'max_positions': 1, 'stop': 0.015, 'max_hold_hours': 4, 'circuit_breaker': 0.03, 'immune_interval': 30},
    'normal': {'position_size': 0.12, 'max_positions': 3, 'stop': 0.04, 'max_hold_hours': 12, 'circuit_breaker': 0.10, 'immune_interval': 60},
    'aggressive': {'position_size': 0.25, 'max_positions': 5, 'stop': 0.06, 'max_hold_hours': 24, 'circuit_breaker': 0.15, 'immune_interval': 60}
}

SECTORS = {
    'large_caps': ['BTC', 'ETH', 'SOL'],
    'defi': ['AAVE', 'UNI', 'LINK', 'CRV'],
    'memes': ['DOGE', 'BONK', 'PEPE', 'SHIB', 'FARTCOIN'],
    'l1s': ['SOL', 'AVAX', 'NEAR', 'SUI', 'APT'],
}


class ModeManager:
    def __init__(self):
        self.modes = self._load()

    def _load(self):
        if MODE_STATE_FILE.exists():
            return json.loads(MODE_STATE_FILE.read_text())
        return dict(DEFAULT_MODES)

    def _save(self):
        self.modes['updated_at'] = datetime.now(timezone.utc).isoformat()
        MODE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MODE_STATE_FILE.write_text(json.dumps(self.modes, indent=2))

    def _log_change(self, dimension: str, old_value, new_value):
        entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'dimension': dimension,
            'from': old_value,
            'to': new_value
        }
        self.modes.setdefault('history', [])
        self.modes['history'].append(entry)
        # Keep last 50 entries
        self.modes['history'] = self.modes['history'][-50:]

    def set_strategy(self, strategy: str, coins: list[str] | None = None) -> dict:
        if strategy not in STRATEGIES:
            return {'error': f'unknown strategy: {strategy}. options: {STRATEGIES}'}
        if coins:
            for coin in coins:
                old = self.modes.get('per_coin_overrides', {}).get(coin, {}).get('strategy', self.modes['strategy'])
                self.modes.setdefault('per_coin_overrides', {})[coin] = {'strategy': strategy}
                self._log_change(f'strategy.{coin}', old, strategy)
        else:
            old = self.modes['strategy']
            self.modes['strategy'] = strategy
            self._log_change('strategy', old, strategy)
        self._save()
        return {'strategy': strategy, 'coins': coins or 'all', 'status': 'set'}

    def set_direction(self, direction: str) -> dict:
        if direction not in DIRECTIONS:
            return {'error': f'unknown direction: {direction}. options: {DIRECTIONS}'}
        old = self.modes['direction']
        self.modes['direction'] = direction
        self._log_change('direction', old, direction)
        self._save()
        return {'direction': direction, 'status': 'set'}

    def set_risk(self, risk: str) -> dict:
        if risk not in RISK_LEVELS:
            return {'error': f'unknown risk level: {risk}. options: {RISK_LEVELS}'}
        old = self.modes['risk']
        self.modes['risk'] = risk
        self._log_change('risk', old, risk)
        self._save()
        params = RISK_PARAMS[risk]
        return {'risk': risk, 'params': params, 'status': 'set'}

    def set_state(self, state: str, condition: str | None = None) -> dict:
        if state not in STATES:
            return {'error': f'unknown state: {state}. options: {STATES}'}
        old = self.modes['state']
        self.modes['state'] = state
        if condition and state in ('sleep', 'exit_only'):
            self.modes['wake_condition'] = condition
        self._log_change('state', old, state)
        self._save()
        result = {'state': state, 'status': 'set'}
        if condition:
            result['condition'] = condition
        return result

    def set_scope(self, scope) -> dict:
        if isinstance(scope, list):
            self.modes['scope'] = 'custom'
            self.modes['scope_coins'] = [c.upper() for c in scope]
            self._log_change('scope', self.modes.get('scope', 'broad'), f'custom: {scope}')
        elif scope in SECTORS:
            self.modes['scope'] = 'sector'
            self.modes['scope_coins'] = SECTORS[scope]
            self._log_change('scope', self.modes.get('scope', 'broad'), f'sector: {scope}')
        elif scope in SCOPES:
            old = self.modes.get('scope', 'broad')
            self.modes['scope'] = scope
            self.modes.pop('scope_coins', None)
            self._log_change('scope', old, scope)
        else:
            return {'error': f'unknown scope: {scope}. options: {SCOPES} or a list of coins or sector name'}
        self._save()
        return {'scope': self.modes['scope'], 'coins': self.modes.get('scope_coins'), 'status': 'set'}

    def add_condition(self, trigger: str, action: str) -> dict:
        condition = {'trigger': trigger, 'action': action, 'created_at': datetime.now(timezone.utc).isoformat()}
        self.modes.setdefault('conditions', []).append(condition)
        self._save()
        return {'condition': condition, 'total_conditions': len(self.modes['conditions']), 'status': 'added'}

    def remove_condition(self, trigger: str) -> dict:
        before = len(self.modes.get('conditions', []))
        self.modes['conditions'] = [c for c in self.modes.get('conditions', []) if c['trigger'] != trigger]
        removed = before - len(self.modes['conditions'])
        self._save()
        return {'trigger': trigger, 'removed': removed, 'status': 'removed'}

    def size_up(self) -> dict:
        self.modes['size_multiplier'] = round(self.modes.get('size_multiplier', 1.0) * 1.5, 2)
        self._log_change('size_multiplier', self.modes['size_multiplier'] / 1.5, self.modes['size_multiplier'])
        self._save()
        return {'size_multiplier': self.modes['size_multiplier'], 'status': 'sized up'}

    def size_down(self) -> dict:
        self.modes['size_multiplier'] = round(self.modes.get('size_multiplier', 1.0) * 0.5, 2)
        self._log_change('size_multiplier', self.modes['size_multiplier'] / 0.5, self.modes['size_multiplier'])
        self._save()
        return {'size_multiplier': self.modes['size_multiplier'], 'status': 'sized down'}

    def reset_size(self) -> dict:
        old = self.modes.get('size_multiplier', 1.0)
        self.modes['size_multiplier'] = 1.0
        self._log_change('size_multiplier', old, 1.0)
        self._save()
        return {'size_multiplier': 1.0, 'status': 'reset'}

    def get_modes(self) -> dict:
        return {
            'strategy': self.modes.get('strategy', 'momentum'),
            'direction': self.modes.get('direction', 'both'),
            'risk': self.modes.get('risk', 'normal'),
            'risk_params': RISK_PARAMS.get(self.modes.get('risk', 'normal'), {}),
            'state': self.modes.get('state', 'active'),
            'scope': self.modes.get('scope', 'broad'),
            'scope_coins': self.modes.get('scope_coins'),
            'conditions': self.modes.get('conditions', []),
            'per_coin_overrides': self.modes.get('per_coin_overrides', {}),
            'size_multiplier': self.modes.get('size_multiplier', 1.0),
            'wake_condition': self.modes.get('wake_condition'),
            'updated_at': self.modes.get('updated_at'),
            'history': self.modes.get('history', [])[-10:]
        }

    def get_active_params(self, coin: str | None = None) -> dict:
        '''Get the effective parameters for trading, considering overrides'''
        base_risk = RISK_PARAMS.get(self.modes.get('risk', 'normal'), RISK_PARAMS['normal'])
        strategy = self.modes.get('strategy', 'momentum')
        direction = self.modes.get('direction', 'both')

        # Check per-coin override
        if coin and coin in self.modes.get('per_coin_overrides', {}):
            override = self.modes['per_coin_overrides'][coin]
            strategy = override.get('strategy', strategy)

        # Apply size multiplier
        params = dict(base_risk)
        params['position_size'] = round(params['position_size'] * self.modes.get('size_multiplier', 1.0), 3)
        params['strategy'] = strategy
        params['direction'] = direction
        params['state'] = self.modes.get('state', 'active')

        return params
