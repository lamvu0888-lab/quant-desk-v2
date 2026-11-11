"""Bounded dashboard mirror of the worker journal on the existing GitHub data branch."""
from __future__ import annotations

import base64
import hashlib
import json
import zlib

from data_utils import utc

SNAPSHOT_PATH = "monitor_data/v25_research_snapshot.json"
MAX_RAW_BYTES = 12_000_000
MAX_PACKED_BYTES = 640_000
LIMITS = {"decision": 28, "candidate": 190, "hedge": 44}


def publish_snapshot(journal, shared, symbols, now):
    data = {symbol: {kind: journal.records(symbol, kind, scope="Tradytics:0", limit=limit)
                     for kind, limit in LIMITS.items()} for symbol in symbols}
    if not any(rows for symbol in data.values() for rows in symbol.values()):
        return False
    raw = json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    packed = zlib.compress(raw, level=6)
    if len(raw) > MAX_RAW_BYTES or len(packed) > MAX_PACKED_BYTES:
        # Bound dashboard transfer without pruning the worker's source journal.
        data = {symbol: {kind: rows[:max(8, LIMITS[kind] // 3)] for kind, rows in group.items()}
                for symbol, group in data.items()}
        raw = json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
        packed = zlib.compress(raw, level=6)
    if len(raw) > MAX_RAW_BYTES or len(packed) > MAX_PACKED_BYTES:
        raise ValueError("Research snapshot exceeds the transfer budget.")
    digest = hashlib.sha256(raw).hexdigest()
    previous, sha = shared.read(SNAPSHOT_PATH, {})
    if isinstance(previous, dict) and previous.get("digest") == digest:
        return False
    shared.write(SNAPSHOT_PATH, {"schema": 1, "as_of": utc(now).isoformat(), "digest": digest,
                                 "data": base64.b64encode(packed).decode()},
                 sha, "Refresh Quant Desk research snapshot")
    return True


def shared_snapshot(shared):
    payload, _ = shared.read(SNAPSHOT_PATH, {})
    if not payload:
        return {}, None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("Unsupported research snapshot schema.")
    packed = base64.b64decode(payload["data"], validate=True)
    if len(packed) > MAX_PACKED_BYTES:
        raise ValueError("Research snapshot exceeds the transfer budget.")
    stream = zlib.decompressobj()
    raw = stream.decompress(packed, MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES or not stream.eof or hashlib.sha256(raw).hexdigest() != payload["digest"]:
        raise ValueError("Research snapshot could not be verified.")
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Research snapshot has an invalid format.")
    return decoded, payload["as_of"]
