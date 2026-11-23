"""Authorization recovery and product failures must not masquerade as each other."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from data_utils import DataError
from my_webull_adapter import WebullSDKAdapter
from settings import Settings
from webull_feed import WebullFeedStore


def response(code=200):
    return SimpleNamespace(status_code=code, json=lambda: {"ok": True})


def test_product_401_does_not_interrupt_other_reads_and_clears_on_retry():
    adapter = WebullSDKAdapter(Settings(), data=Mock())
    adapter._auth = SimpleNamespace(expires=None)
    method, limiter = Mock(return_value=response(401)), Mock()
    with pytest.raises(DataError, match="Stock footprint: HTTP 401"):
        adapter.request("Stock footprint", method, limiter)
    assert not getattr(adapter, "_auth_failed", False)
    adapter.request("Stock snapshot", Mock(return_value=response()), limiter)
    assert adapter.request_errors()[0]["Request"] == "Stock footprint"
    assert adapter.last_auth_error is None
    adapter.request("Stock footprint", Mock(return_value=response()), limiter)
    assert not adapter.request_errors()


def test_authorization_check_failure_blocks_until_validation_succeeds():
    adapter = WebullSDKAdapter(Settings(), data=Mock())
    adapter._auth = Mock(expires=None)
    adapter._auth.initialize.side_effect = DataError("REAUTH REQUIRED")
    adapter._api, adapter._factory = Mock(), Mock()
    adapter._renew_at = 0
    with pytest.raises(DataError, match="REAUTH REQUIRED"):
        adapter._check_authorization()
    assert adapter.last_auth_error and adapter.last_auth_error_at
    adapter._renew_at = 0
    adapter._auth.initialize.side_effect = None
    adapter._check_authorization()
    assert not adapter._auth_failed
    assert adapter.last_auth_error is None and adapter.last_auth_error_at is None
    assert adapter.last_auth_check


def test_endpoint_error_and_authorization_are_reported_independently():
    adapter = WebullSDKAdapter(Settings(), data=Mock())
    store = WebullFeedStore(Settings())
    store._adapter = adapter
    try:
        with pytest.raises(DataError, match="HTTP 403"):
            adapter.request("Stock footprint", Mock(return_value=response(403)), Mock())
        adapter.request("Stock snapshot", Mock(return_value=response()), Mock())
        assert store.auth_diagnostics()["Connection"] == "Authorized request observed"
        assert store.auth_diagnostics()["Last authentication error"] is None
        assert store.request_diagnostics()[0]["HTTP status"] == 403
    finally:
        store.close()


def test_sdk_exception_fields_cannot_expose_private_messages():
    from webull.core.exception.exceptions import ServerException
    adapter = WebullSDKAdapter(Settings(), data=Mock())
    method = Mock(side_effect=ServerException("UNAUTHORIZED", "fixture-secret-do-not-log", 401))
    with pytest.raises(DataError) as error:
        adapter.request("Futures footprint", method, Mock())
    assert "fixture-secret" not in str(error.value)
    assert "fixture-secret" not in str(adapter.request_errors())
