import pytest
from services.local_botapi_diagnostics import _classify, _proxy_label


def test_proxy_only_route_is_explained_as_missing_tun() -> None:
    reason = _classify("TimeoutError: timed out", False, "socks5://127.0.0.1:10808")
    assert "socks5://127.0.0.1:10808" in reason
    assert "системного TUN-маршрута" in reason


def test_credentials_are_reported_before_network_guess() -> None:
    reason = _classify("Error: invalid api-id and api-hash", False, "socks5://127.0.0.1:10808")
    assert "TELEGRAM_API_ID" in reason


def test_cloud_local_registration_conflict_mentions_logout() -> None:
    reason = _classify("409 Conflict: another Bot API server", True, "")
    assert "logOut" in reason


def test_proxy_label_never_exposes_credentials() -> None:
    assert _proxy_label("http://user:secret@127.0.0.1:8080") == "http://127.0.0.1:8080"


def test_malformed_proxy_label_uses_generic_safe_fallback(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG", logger="services.local_botapi_diagnostics")

    assert _proxy_label("http://user:proxy-secret@127.0.0.1:not-a-port") == "application proxy"
    assert "using generic label" in caplog.text
    assert "proxy-secret" not in caplog.text
