from __future__ import annotations

import os
from pathlib import Path

import services.local_botapi_runtime as runtime


def _base_env(monkeypatch):
    monkeypatch.setenv("LOCAL_BOT_API_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("BOT_TOKEN", "123456:secret-token")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5h://127.0.0.1:10808")
    monkeypatch.setenv("LOCAL_BOT_API_CLOUD_FALLBACK", "1")


def test_autostart_zero_does_not_touch_processes(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LOCAL_BOT_API_AUTOSTART", "0")
    calls = []
    monkeypatch.setattr(runtime.legacy, "_probe_getme", lambda *_: (False, "timeout"))
    monkeypatch.setattr(runtime, "_terminate_managed_server", lambda: calls.append("kill"))

    runtime.prepare_local_bot_api()

    assert calls == []
    assert os.environ["MP3BOT_EFFECTIVE_BOT_API"] == "cloud"
    assert os.environ["LOCAL_BOT_API_URL"] == ""


def test_proxy_args_accept_only_supported_http_proxy(monkeypatch):
    runtime._ACTIVE_PROXY_URL = "http://user:pass@127.0.0.1:8080"
    assert runtime._proxy_args() == [
        "--proxy=http://user:pass@127.0.0.1:8080"
    ]

    runtime._ACTIVE_PROXY_URL = "socks5://127.0.0.1:10808"
    assert runtime._proxy_args() == []


def test_targeted_termination_ignores_unrelated_listener(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LOCAL_BOT_API_DATA_DIR", str(tmp_path / "data"))
    data_dir = runtime._writable_data_dir()
    runtime._write_pid(runtime._pid_path(data_dir), 101)
    monkeypatch.setattr(runtime.os, "name", "nt")
    monkeypatch.setattr(runtime, "_windows_listener_pids", lambda _port: {101, 202})
    monkeypatch.setattr(runtime, "_is_botapi", lambda pid: pid == 101)
    killed = []
    monkeypatch.setattr(runtime, "_kill_pid", lambda pid: killed.append(pid) or True)

    result = runtime._terminate_managed_server()

    assert result == [101]
    assert killed == [101]
    assert not runtime._pid_path(data_dir).exists()


def test_log_tail_redacts_token_hash_and_proxy_password(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_API_HASH", "private-api-hash")
    runtime._ACTIVE_PROXY_URL = "http://user:proxy-secret@127.0.0.1:8080"
    log = tmp_path / "botapi-server.log"
    log.write_text(
        "GET /bot123456:secret-token/getMe\n"
        "hash=private-api-hash password=proxy-secret",
        encoding="utf-8",
    )

    tail = runtime._read_log_tail(str(log))

    assert "secret-token" not in tail
    assert "private-api-hash" not in tail
    assert "proxy-secret" not in tail
    assert "/bot***" in tail


def test_runtime_restores_http_proxy_after_legacy_bootstrap(monkeypatch):
    _base_env(monkeypatch)
    proxy = "http://user:pass@127.0.0.1:8080"
    monkeypatch.setenv("LOCAL_BOT_API_PROXY_URL", proxy)
    monkeypatch.setenv("LOCAL_BOT_API_AUTOSTART", "1")
    seen = {}

    def fake_prepare():
        seen["terminate"] = runtime.legacy._terminate_stale_server
        seen["start"] = runtime.legacy._start_local_server
        seen["tail"] = runtime.legacy._read_log_tail
        os.environ["LOCAL_BOT_API_PROXY_URL"] = ""

    original_terminate = runtime.legacy._terminate_stale_server
    original_start = runtime.legacy._start_local_server
    original_tail = runtime.legacy._read_log_tail
    monkeypatch.setattr(runtime.legacy, "prepare_local_bot_api", fake_prepare)

    runtime.prepare_local_bot_api()

    assert seen["terminate"] is runtime._terminate_managed_server
    assert seen["start"] is runtime._start_server
    assert seen["tail"] is runtime._read_log_tail
    assert runtime.legacy._terminate_stale_server is original_terminate
    assert runtime.legacy._start_local_server is original_start
    assert runtime.legacy._read_log_tail is original_tail
    assert os.environ["LOCAL_BOT_API_PROXY_URL"] == proxy
