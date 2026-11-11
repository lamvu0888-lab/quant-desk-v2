"""Shared GitHub Contents storage for Render worker and Streamlit UI.

Requires a fine-grained GitHub token with Contents read/write for this repository.
Never store secrets in tracked JSON. Fails closed on invalid remote configuration.
"""
from __future__ import annotations
import base64
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote
import requests

DEFAULT = {'symbols': ['SPY', 'SPX'], 'interval_seconds': 30, 'enabled': True}
CONFIG_PATH = 'monitor_data/discord_watchlist.json'
LOG_PATH = 'monitor_data/signal_history.json'


def normalize_symbols(values):
    if not isinstance(values, list):
        raise ValueError('Tickers must be a list')
    out = []
    for value in values:
        symbol = str(value).strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}', symbol):
            raise ValueError(f'Invalid ticker: {value}')
        if symbol not in out:
            out.append(symbol)
    if len(out) > 12:
        raise ValueError('Maximum 12 tickers to protect API quotas')
    return out


class SharedStore:
    def __init__(self, repo=None, token=None, branch=None):
        self.repo = (repo if repo is not None else os.getenv('MONITOR_GITHUB_REPO', '')).strip()
        self.token = (token if token is not None else os.getenv('MONITOR_GITHUB_TOKEN', '')).strip()
        self.branch = (branch if branch is not None else os.getenv('MONITOR_GITHUB_BRANCH', 'main')).strip()
        if self.repo and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repo):
            raise ValueError('MONITOR_GITHUB_REPO must be owner/repository')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', self.branch) or '..' in self.branch:
            raise ValueError('Invalid branch')

    @property
    def configured(self):
        return bool(self.repo and self.token)

    def _url(self, path):
        return f'https://api.github.com/repos/{self.repo}/contents/{quote(path, safe="/")}'

    def _headers(self):
        return {'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'}

    def read(self, path, default=None):
        if not self.configured:
            return default, None
        response = requests.get(self._url(path), headers=self._headers(), params={'ref': self.branch}, timeout=12)
        if response.status_code == 404:
            return default, None
        response.raise_for_status()
        body = response.json()
        if body.get('encoding') != 'base64':
            raise ValueError('Unexpected GitHub Contents response')
        return json.loads(base64.b64decode(body['content'])), body['sha']

    def write(self, path, data, sha=None, message='Update Quant Desk monitoring settings'):
        if not self.configured:
            raise RuntimeError('Set MONITOR_GITHUB_REPO and MONITOR_GITHUB_TOKEN on both services')
        payload = {'message': message, 'branch': self.branch,
                   'content': base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()}
        if sha:
            payload['sha'] = sha
        response = requests.put(self._url(path), headers=self._headers(), json=payload, timeout=15)
        if response.status_code in (409, 422):
            raise RuntimeError('Shared file changed. Refresh and retry to avoid overwriting another update.')
        response.raise_for_status()
        return response.json()

    def watchlist(self):
        value, sha = self.read(CONFIG_PATH, dict(DEFAULT))
        if not isinstance(value, dict):
            raise ValueError('Watchlist must be a JSON object')
        result = {**DEFAULT, **value}
        result['symbols'] = normalize_symbols(result['symbols'])
        result['interval_seconds'] = max(30, int(result['interval_seconds']))
        result['enabled'] = result['enabled'] is True
        return result, sha

    def history(self):
        value, sha = self.read(LOG_PATH, [])
        if not isinstance(value, list):
            raise ValueError('Signal history must be a JSON list')
        return value, sha

    def append_event(self, event):
        # GitHub writes are serialized by SHA. Retry conflicts without dropping events.
        for attempt in range(3):
            history, sha = self.history()
            if any(item.get('id') == event['id'] for item in history):
                return
            try:
                self.write(LOG_PATH, (history + [event])[-500:], sha, 'Record Quant Desk signal')
                return
            except RuntimeError:
                if attempt == 2:
                    raise
