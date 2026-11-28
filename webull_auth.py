"""Serialize SDK initialization and persist its token state across reader restarts."""
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet, InvalidToken
import psycopg
from webull.core.http.initializer.token.token_operation import TokenOperation
from webull.core.http.initializer.config.config_operation import ConfigOperation

from data_utils import DataError, utc

REAUTH = "REAUTH REQUIRED"
START = "Run python diagnose_webull.py --authorize on one configured service."
PENDING = "Approve the existing request in the Webull app."


def response_body(response):
    if response.status_code != 200:
        raise DataError("Webull authorization service returned an error. Check app credentials and access.")
    body = response.json()
    if not isinstance(body, dict):
        raise DataError("Webull authorization response unavailable.")
    return body


def reported_expiry(value):
    # Some SDK responses carry a duration without an absolute validity timestamp.
    if str(value).isdigit() and int(value) > 1_000_000_000:
        return utc(value)
    if isinstance(value, str) and "T" in value:
        try:
            return utc(value)
        except DataError:
            pass
    return None


class WebullAuthorization:
    def __init__(self, key, secret, endpoint, dsn, path):
        domain = f"Quant Desk Webull v1|us|{endpoint}|{key}".encode()
        self.scope = hashlib.sha256(domain).hexdigest()
        self.cipher = Fernet(base64.urlsafe_b64encode(hmac.digest(secret.encode(), domain, "sha256")))
        self.dsn = dsn
        self.path = Path(path or (Path.home() / ".quant-desk" / "webull.sqlite3"))
        self.expires = None

    def connect(self):
        if self.dsn:
            return psycopg.connect(self.dsn, connect_timeout=5, options="-c lock_timeout=30000")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        return sqlite3.connect(self.path, timeout=30)

    def initialize(self, api, factory, *, authorize=False):
        # A transaction spans lookup, provider validation and rotation. Other
        # containers can never observe partially written or uncommitted state.
        try:
            with closing(self.connect()) as db:
                if self.dsn:
                    db.execute("SELECT pg_advisory_xact_lock(438729104)")
                else:
                    db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS qd_webull_auth (scope TEXT PRIMARY KEY, body TEXT NOT NULL)")
                param = "%s" if self.dsn else "?"
                row = db.execute("SELECT body FROM qd_webull_auth WHERE scope=" + param, (self.scope,)).fetchone()
                content = self.cipher.decrypt(row[0].encode("ascii") if isinstance(row[0], str) else row[0]).decode() if row else ""
                if not content:
                    legacy = Path(os.getenv("WEBULL_OPENAPI_TOKEN_DIR") or "conf") / "token.txt"
                    if legacy.is_file():
                        content = legacy.read_text(encoding="utf8")
                with TemporaryDirectory(prefix="qd-webull-") as directory:
                    api.set_token_dir(directory)
                    cache = Path(directory) / "token.txt"
                    def persist():
                        if cache.is_file():
                            encrypted = self.cipher.encrypt(cache.read_bytes()).decode()
                            db.execute(f"INSERT INTO qd_webull_auth VALUES ({param},{param}) "
                                       "ON CONFLICT(scope) DO UPDATE SET body=excluded.body", (self.scope, encrypted))
                        db.commit()

                    config = response_body(ConfigOperation(api).get_config())
                    enabled = config.get("token_check_enabled")
                    if type(enabled) is not bool:
                        raise DataError("Webull did not report the account verification setting.")
                    if enabled:
                        ops = TokenOperation(api)
                        lines = content.splitlines()
                        token = lines[0] if len(lines) == 3 else None
                        state = response_body(ops.check_token(token)) if token else {}
                        if state and state.get("status") not in ("NORMAL", "PENDING", "EXPIRED", "INVALID"):
                            raise DataError("Webull returned an unknown authorization state.")
                        if state.get("status") not in ("NORMAL", "PENDING"):
                            if not authorize:
                                raise DataError(f"{REAUTH}. {START}")
                            state = response_body(ops.create_token(None))
                        expiry = reported_expiry(state.get("expires"))
                        if (state.get("status") == "NORMAL" and expiry and
                                0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 300):
                            try:
                                refreshed = ops.refresh_token(state.get("token") or token)
                            except Exception as exc:
                                status_code = getattr(exc, "get_http_status", lambda: None)()
                                if status_code not in (404, 405):
                                    raise
                            else:
                                if refreshed.status_code not in (404, 405):
                                    state = response_body(refreshed)
                        status = state.get("status")
                        token = state.get("token") or token
                        if not token or status not in ("NORMAL", "PENDING"):
                            raise DataError(f"{REAUTH}. {START}")
                        cache.write_text(f"{token}\n{state.get('expires') or 0}\n{status}\n", encoding="utf8")
                        if status == "PENDING":
                            persist()
                            raise DataError(f"{REAUTH}. {PENDING}")
                    try:
                        data = factory(api)  # SDK repeats its own account and token checks.
                    finally:
                        persist()  # Includes a PENDING state returned by the SDK.
                    self.expires = None
                    if enabled and cache.is_file():
                        fields = cache.read_text(encoding="utf8").splitlines()
                        if len(fields) == 3:
                            self.expires = reported_expiry(fields[1])
                    return data
        except (sqlite3.Error, psycopg.Error, OSError, InvalidToken, UnicodeError):
            raise DataError("Webull authorization store unavailable. Check shared storage, app identity and permissions.") from None
