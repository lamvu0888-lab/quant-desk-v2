from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
import os
import threading

import pytest
from webull.core.client import ApiClient
from webull.data.data_client import DataClient
from data_utils import DataError
from webull_auth import WebullAuthorization


class Provider:
    def __init__(self):
        self.status = "PENDING"
        self.approvals = 0
        self.refreshes = 0
        self.expires = 1900000000
        self.verifications = 0
        self.token = "fixture-only-authorization"
        self.mu = threading.Lock()

    def request(self, req):
        with self.mu:
            name = type(req).__name__
            if name == "GetConfigRequest":
                data = {"token_check_enabled":True}
            else:
                # Inspect the SDK request body to distinguish creation from reuse.
                body = req.get_body_params()
                if name == "CreateTokenRequest" and not body.get("token"):
                    self.approvals += 1
                else:
                    assert body["token"] == self.token
                    self.verifications += 1
                if name == "RefreshTokenRequest":
                    self.expires = 1900000000
                    self.refreshes += 1
                data = {"token":self.token,"status":self.status,"expires":self.expires}
            return SimpleNamespace(status_code=200,json=lambda:data)


@pytest.mark.parametrize("backend", ["sqlite","postgres"])
def test_restart_concurrent_approval_revocation(tmp_path, monkeypatch, backend):
    dsn = os.getenv("QUANT_DESK_TEST_PG_DSN") if backend=="postgres" else ""
    if backend=="postgres" and not dsn:
        pytest.skip("Set QUANT_DESK_TEST_PG_DSN for a disposable PostgreSQL database")
    monkeypatch.chdir(tmp_path)
    provider=Provider()
    def connect(allow=False):
        store=WebullAuthorization(str(tmp_path),"fixture-only", "sandbox",dsn,tmp_path/"auth.db")
        api=ApiClient("key","secret","us",auto_retry=False)
        api.set_stream_logger(log_level=100)
        api.get_response=provider.request
        return store.initialize(api,DataClient,authorize=allow)
    with pytest.raises(DataError,match="REAUTH REQUIRED"):
        connect()
    def pending(_):
        with pytest.raises(DataError,match="existing request"):
            connect(True)
    with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(pending,range(4)))
    assert provider.approvals==1
    provider.status="NORMAL"
    with ThreadPoolExecutor(max_workers=4) as pool:
        clients=list(pool.map(lambda _:connect(),range(4)))
    assert all(isinstance(c,DataClient) for c in clients) and provider.approvals==1
    assert provider.verifications>=4
    if not dsn:
        assert provider.token.encode() not in (tmp_path/"auth.db").read_bytes()
    provider.expires=int(datetime.now(timezone.utc).timestamp())+120
    connect()
    assert provider.refreshes==1 and provider.approvals==1
    provider.status="EXPIRED"
    with pytest.raises(DataError,match="REAUTH REQUIRED") as err:
        connect()
    assert provider.approvals==1 and provider.token not in str(err.value)
