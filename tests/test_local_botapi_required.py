from __future__ import annotations

import os
from pathlib import Path

import pytest

import services.local_botapi_required as required


class _RunningProcess:
    returncode = None

    def poll(self):
        return None


def _base_env(monkeypatch):
    monkeypatch.setenv("LOCAL_BOT_API_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("TELEGRAM_API_ID", "10001")
    monkeypatch.setenv("TELEGRAM_API_HASH", "api-hash")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5h://127.0.0.1:10808")
    monkeypatch.setenv("LOCAL_BOT_API_CLOUD_FALLBACK", "1")
    monkeypatch.setenv("CLOUD_MEDIA_AUTO_COMPRESS", "1")


def test_healthy_local_server_starts_without_logout_or_restart(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setattr(required.probe_runtime, "_probe_getme", lambda *_: (True, "@ready"))
    monkeypatch.setattr(
        required,
        "_cloud_logout",
        lambda *_: pytest.fail("healthy local server must not call cloud logOut"),
    )
    monkeypatch.setattr(
        required.process_runtime,
        "_terminate_managed_server",
        lambda: pytest.fail("healthy local server must not be restarted"),
    )

    required.require_local_bot_api()

    assert os.environ["MP3BOT_EFFECTIVE_BOT_API"] == "local"
    assert os.environ["LOCAL_BOT_API_URL"] == "http://127.0.0.1:8081"
    assert os.environ["LOCAL_BOT_API_CLOUD_FALLBACK"] == "0"
    assert os.environ["CLOUD_MEDIA_AUTO_COMPRESS"] == "0"
    assert os.environ["TELEGRAM_PROXY_URL"] == ""


def test_unhealthy_local_uses_one_logout_restart_and_real_getme(monkeypatch):
    _base_env(monkeypatch)
    calls = []
    monkeypatch.setattr(required.probe_runtime, "_probe_getme", lambda *_: (False, "offline"))
    monkeypatch.setattr(required, "_cloud_logout", lambda *_: calls.append("logout"))
    monkeypatch.setattr(
        required.process_runtime,
        "_terminate_managed_server",
        lambda: calls.append("stop") or [],
    )
    monkeypatch.setattr(required.probe_runtime, "_wait_until_port_closes", lambda *_: None)
    monkeypatch.setattr(
        required.process_runtime,
        "_start_server",
        lambda *_: (calls.append("start") or _RunningProcess(), "botapi-server.log"),
    )
    monkeypatch.setattr(
        required.probe_runtime,
        "_wait_for_getme",
        lambda *_args, **_kwargs: (True, "@recovered", 3),
    )

    required.require_local_bot_api()

    assert calls == ["logout", "stop", "start"]
    assert os.environ["MP3BOT_EFFECTIVE_BOT_API"] == "local"
    assert os.environ["LOCAL_BOT_API_CLOUD_FALLBACK"] == "0"
    assert os.environ["CLOUD_MEDIA_AUTO_COMPRESS"] == "0"


def test_failed_local_start_aborts_without_cloud_or_compression(monkeypatch):
    _base_env(monkeypatch)
    stops = []
    monkeypatch.setattr(required.probe_runtime, "_probe_getme", lambda *_: (False, "offline"))
    monkeypatch.setattr(required, "_cloud_logout", lambda *_: None)
    monkeypatch.setattr(
        required.process_runtime,
        "_terminate_managed_server",
        lambda: stops.append(True) or [],
    )
    monkeypatch.setattr(required.probe_runtime, "_wait_until_port_closes", lambda *_: None)
    monkeypatch.setattr(
        required.process_runtime,
        "_start_server",
        lambda *_: (_RunningProcess(), "botapi-server.log"),
    )
    monkeypatch.setattr(
        required.probe_runtime,
        "_wait_for_getme",
        lambda *_args, **_kwargs: (False, "timeout", 4),
    )
    monkeypatch.setattr(required.process_runtime, "_read_log_tail", lambda *_args, **_kwargs: "TDLib timeout")

    with pytest.raises(required.LocalBotApiRequiredError, match="локальный /getMe не поднялся"):
        required.require_local_bot_api()

    assert len(stops) == 2
    assert os.environ["LOCAL_BOT_API_URL"] == "http://127.0.0.1:8081"
    assert os.environ["LOCAL_BOT_API_CLOUD_FALLBACK"] == "0"
    assert os.environ["CLOUD_MEDIA_AUTO_COMPRESS"] == "0"
    assert os.environ.get("MP3BOT_EFFECTIVE_BOT_API") != "cloud"


def test_socks5h_proxy_is_normalized_for_httpx():
    assert required._normalise_proxy("socks5h://127.0.0.1:10808") == "socks5://127.0.0.1:10808"
    assert required._normalise_proxy("http://127.0.0.1:8080") == "http://127.0.0.1:8080"


def test_entrypoint_requires_local_before_importing_main_and_has_no_cloud_adapter():
    source = Path("bot_new.py").read_text(encoding="utf-8")
    local_import = source.index("from services.local_botapi_required import")
    local_call = source.index("require_local_bot_api()")
    main_import = source.index("from main import main")

    assert local_import < local_call < main_import
    assert "install_cloud_media_fallback" not in source
    assert "sys.exit(3)" in source
