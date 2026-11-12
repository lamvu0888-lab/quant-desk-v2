"""One configuration for the dashboard, diagnostics and scanner."""
from dataclasses import dataclass, replace
import os
from pathlib import Path
from dotenv import load_dotenv
from data_utils import DataError

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    endpoint: str = 'api.webull.com'
    quote_seconds: int = 5
    candle_seconds: int = 60
    chain_seconds: int = 120
    discovery_seconds: int = 900
    max_requests: int = 45
    history_count: int = 1000
    quote_max_age: int = 15
    option_max_age: int = 120
    dte_days: int = 7
    strike_width: float = .02
    max_pages: int = 20
    max_contracts: int = 500
    root_symbol: str = ''
    category: str = 'AUTO'
    risk_free_rate: float = .04
    dividend_yield: float = 0.0

    @classmethod
    def load(cls, lookup=None):
        if lookup is None:
            load_dotenv(ROOT / '.env', override=True)
            lookup = os.environ.get
        pairs = {
            'endpoint': ('WEBULL_ENDPOINT', str), 'quote_seconds': ('QUOTE_REFRESH_SECONDS', int),
            'candle_seconds': ('CANDLE_REFRESH_SECONDS', int), 'chain_seconds': ('CHAIN_REFRESH_SECONDS', int),
            'discovery_seconds': ('CHAIN_DISCOVERY_SECONDS', int), 'max_requests': ('MAX_REQUESTS_PER_MINUTE', int),
            'history_count': ('HISTORY_1M_BARS', int), 'quote_max_age': ('QUOTE_MAX_AGE_SECONDS', int),
            'option_max_age': ('OPTION_MAX_AGE_SECONDS', int), 'dte_days': ('CHAIN_DTE_DAYS', int),
            'strike_width': ('CHAIN_STRIKE_WIDTH_PCT', float), 'max_pages': ('CHAIN_MAX_PAGES', int),
            'max_contracts': ('CHAIN_MAX_CONTRACTS', int), 'root_symbol': ('CHAIN_ROOT_SYMBOL', str),
            'category': ('WEBULL_CATEGORY', str), 'risk_free_rate': ('RISK_FREE_RATE', float),
            'dividend_yield': ('DIVIDEND_YIELD', float),
        }
        try:
            result = cls(**{key: cast(value) for key, (name, cast) in pairs.items() if (value := lookup(name))})
        except (ValueError, TypeError) as exc:
            raise DataError('Invalid numeric setting in .env') from exc
        # V1.8 shipped QUOTE_MAX_AGE_SECONDS=90. Accept that existing .env
        # value during upgrades, while keeping V1.9's stricter forecast gate.
        result = replace(result, quote_max_age=min(result.quote_max_age, 30))
        if result.endpoint not in ('api.webull.com', 'api.sandbox.webull.com'):
            raise DataError('WEBULL_ENDPOINT must be api.webull.com or api.sandbox.webull.com')
        bounds = {'quote_seconds': (5, 60), 'candle_seconds': (30, 300), 'chain_seconds': (60, 1800),
                  'discovery_seconds': (300, 3600), 'max_requests': (15, 120), 'history_count': (500, 1200),
                  'quote_max_age': (5, 90), 'option_max_age': (15, 300), 'dte_days': (1, 14),
                  'strike_width': (.005, .1), 'max_pages': (1, 100), 'max_contracts': (20, 1000),
                  'risk_free_rate': (-.1, 1), 'dividend_yield': (0, 1)}
        for key, (lo, hi) in bounds.items():
            if not lo <= getattr(result, key) <= hi:
                raise DataError(f'{key} must be between {lo} and {hi}')
        if result.category not in ('AUTO', 'US_STOCK', 'US_ETF'):
            raise DataError('WEBULL_CATEGORY must be AUTO, US_STOCK or US_ETF')
        return result
