"""Private Schwab OAuth storage shared by the market-data readers."""
import base64
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from cryptography.fernet import Fernet, InvalidToken
import psycopg
import requests

from tradytics_feed import FeedError

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
TOKEN_PATH = Path(__file__).resolve().parent / "monitor_data/runtime/schwab-oauth.sqlite3"
AUTHORIZE = "Run AUTHORIZE_SCHWAB_SPX.cmd on your computer to authorize market data."


class SchwabTokens:
    def __init__(self, client_id, secret, dsn="", path=None):
        self.client_id, self._secret = client_id, secret
        self._dsn, self._path = dsn, Path(path or TOKEN_PATH)
        self._key = hashlib.sha256(client_id.encode()).hexdigest()
        derived = hmac.digest(secret.encode(), b"Quant Desk Schwab OAuth v1\0" + client_id.encode(), "sha256")
        self._cipher = Fernet(base64.urlsafe_b64encode(derived))
        self._cache = {}
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config):
        key, secret = config("SCHWAB_CLIENT_ID"), config("SCHWAB_CLIENT_SECRET")
        if not key or not secret:
            return None
        return cls(key, secret, config("SCHWAB_TOKEN_DSN"), config("SCHWAB_TOKEN_PATH"))

    @contextmanager
    def _db(self, *, initialize=False):
        try:
            if self._dsn:
                db = psycopg.connect(self._dsn, connect_timeout=5, options="-c lock_timeout=15000")
            else:
                if initialize:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    os.close(os.open(self._path, os.O_CREAT | os.O_WRONLY, 0o600))
                if not self._path.is_file():
                    raise FeedError("AUTH REQUIRED", AUTHORIZE, 30)
                db = sqlite3.connect(self._path.resolve().as_uri() + "?mode=rw", uri=True, timeout=15)
            with closing(db):
                if not self._dsn:
                    db.execute("BEGIN IMMEDIATE")
                if initialize:
                    db.execute("CREATE TABLE IF NOT EXISTS qd_schwab_oauth (app TEXT PRIMARY KEY, ciphertext TEXT NOT NULL)")
                yield db
                db.commit()
        except psycopg.errors.UndefinedTable:
            raise FeedError("AUTH REQUIRED", AUTHORIZE, 30) from None
        except (sqlite3.Error, psycopg.Error, OSError) as exc:
            if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
                raise FeedError("AUTH REQUIRED", AUTHORIZE, 30) from None
            raise FeedError("TOKEN STORE", "Schwab token store unavailable. Check its private connection and permissions.") from None

    def _query(self, db, sql, *args):
        return db.execute(sql.replace("?", "%s") if self._dsn else sql, args)

    def _save(self, db, token):
        ciphertext = self._cipher.encrypt(json.dumps(token).encode()).decode()
        self._query(db, """INSERT INTO qd_schwab_oauth (app,ciphertext) VALUES (?,?)
            ON CONFLICT(app) DO UPDATE SET ciphertext=excluded.ciphertext""", self._key, ciphertext)

    def _exchange(self, form, previous_refresh=None):
        try:
            response = requests.post(TOKEN_URL, auth=(self.client_id, self._secret), data=form,
                                     headers={"Accept": "application/json"}, timeout=(4, 8), allow_redirects=False)
        except requests.RequestException:
            raise FeedError("AUTH UNAVAILABLE", "Schwab token service could not be reached.") from None
        if response.status_code in (400, 401, 403):
            raise FeedError("AUTH REQUIRED", "Schwab rejected the grant or app credentials. " + AUTHORIZE, 60)
        if response.status_code != 200:
            raise FeedError("AUTH UNAVAILABLE", f"Schwab token service returned HTTP {response.status_code}.", 60)
        try:
            body = response.json()
            access, refresh = body["access_token"], body.get("refresh_token") or previous_refresh
            lifetime = int(body["expires_in"])
            if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
                    or body.get("token_type", "").lower() != "bearer" or lifetime <= 0):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise FeedError("AUTH RESPONSE", "Schwab returned an incomplete OAuth token.") from None
        return {"access": access, "refresh": refresh, "expires": time.time() + lifetime}

    def prepare(self):
        with self._db(initialize=True):
            pass

    def authorize(self, code, callback):
        token = self._exchange({"grant_type": "authorization_code", "code": code, "redirect_uri": callback})
        with self._lock:
            with self._db(initialize=True) as db:
                self._save(db, token)
            self._cache = token

    def access_token(self, rejected=None):
        def usable(token):
            return token.get("access") and token["access"] != rejected and token["expires"] > time.time() + 60

        with self._lock:
            if usable(self._cache):
                return self._cache["access"]
            # Keep refresh and rotation in one transaction across Streamlit and worker processes.
            with self._db() as db:
                lock = " FOR UPDATE" if self._dsn else ""
                row = self._query(db, "SELECT ciphertext FROM qd_schwab_oauth WHERE app=?" + lock, self._key).fetchone()
                if not row:
                    raise FeedError("AUTH REQUIRED", AUTHORIZE, 30)
                try:
                    token = json.loads(self._cipher.decrypt(row[0].encode("ascii") if isinstance(row[0], str) else row[0]))
                except (InvalidToken, ValueError, TypeError):
                    raise FeedError("AUTH REQUIRED", "Stored authorization does not match the app secret. " + AUTHORIZE) from None
                if not usable(token):
                    refresh = token["refresh"]
                    token = self._exchange({"grant_type": "refresh_token", "refresh_token": refresh}, refresh)
                    self._save(db, token)
            self._cache = token
            return token["access"]
