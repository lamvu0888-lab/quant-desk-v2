"""Validated Webull response handling. Never fill missing market values with zero."""
from __future__ import annotations
import math
from datetime import datetime, timezone

UTC = timezone.utc


class DataError(RuntimeError):
    pass


class BudgetPause(DataError):
    def __init__(self, seconds, message='Request budget cooling down'):
        self.seconds = max(.1, float(seconds))
        super().__init__(message)


def number(value, name='number', *, minimum=None):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError()
        result = float(value)
        if not math.isfinite(result) or (minimum is not None and result < minimum):
            raise ValueError()
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataError(f'Missing or invalid {name}') from exc


def utc(value):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError()
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, (float, int)) or (isinstance(value, str) and value.replace('.', '', 1).isdigit()):
            n = float(value)
            if not math.isfinite(n):
                raise ValueError()
            result = datetime.fromtimestamp(n / (1000 if n > 1e11 else 1), UTC)
        else:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise DataError('Missing or invalid timezone-aware market timestamp') from exc


def field(obj, *keys):
    return next((obj[k] for k in keys if isinstance(obj, dict) and obj.get(k) is not None), None)


def records(payload, symbol=None):
    """Unwrap lists, result/data wrappers and ticker-keyed Webull records."""
    if isinstance(payload, list):
        if symbol and any(isinstance(x, dict) and 'symbol' in x for x in payload):
            matches = [x for x in payload if isinstance(x, dict) and x.get('symbol') == symbol]
            if len(matches) != 1:
                raise DataError('Webull symbol missing or duplicated')
            selected = matches[0]
            for key in ('result', 'bars', 'data'):
                if isinstance(selected.get(key), (list, dict)):
                    return records(selected[key], symbol)
            return matches
        return payload
    if isinstance(payload, dict):
        if symbol and symbol in payload:
            return records(payload[symbol], symbol)
        if 'symbol' in payload and symbol and payload['symbol'] != symbol:
            raise DataError('Webull symbol mismatch')
        for key in ('result', 'data', 'bars', 'items', 'list', 'contracts', 'rows'):
            if isinstance(payload.get(key), (dict, list)):
                return records(payload[key], symbol)
        if 'symbol' in payload:
            return [payload]
    raise DataError('Unrecognized Webull response shape')


def reported_delay(payload, symbol=None):
    """Read only server-supplied delay metadata for this symbol, including wrappers."""
    if isinstance(payload, dict):
        if symbol and payload.get('symbol') not in (None, symbol):
            return None
        if payload.get('delay_minutes') is not None:
            return number(payload['delay_minutes'], 'delay_minutes', minimum=0)
        candidates = []
        if symbol and symbol in payload:
            candidates.append(payload[symbol])
        candidates.extend(payload[k] for k in ('result', 'data', 'items', 'list') if k in payload)
    elif isinstance(payload, list):
        candidates = [x for x in payload if isinstance(x, dict) and (symbol is None or x.get('symbol') in (None, symbol))]
    else:
        return None
    values = [v for x in candidates if (v := reported_delay(x, symbol)) is not None]
    return max(values) if values else None


def safe_error(exc):
    """SDK exceptions may embed signed requests; do not send their raw text to the UI."""
    code_getter = getattr(exc, 'get_error_code', None)
    code = code_getter() if callable(code_getter) else None
    if code in ('ERROR_INIT_TOKEN', 'ERROR_CHECK_TOKEN', 'ERROR_CREATE_TOKEN'):
        return 'Webull API token authorization is incomplete or expired. Approve it in the Webull OpenAPI console and reconnect.'
    if isinstance(exc, DataError):
        return str(exc)[:350]
    return f'{type(exc).__name__}: Webull connection failed. Check network, API credentials and OpenAPI permissions locally.'
