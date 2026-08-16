from __future__ import annotations

import services.local_botapi_runtime as runtime


def _base_env(monkeypatch):
    monkeypatch.setenv("LOCAL_BOT_API_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("BOT_TOKEN", "123456:secret-token")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5h://127.0.0.1:10808")
    monkeypatch.setenv("LOCAL_BOT_API_CLOUD_FALLBACK", "1")


def test_proxy_args_accept_only_supported_http_proxy() -> None:
    proxy = "http://user:pass@127.0.0.1:8080"
    assert runtime._proxy_args(proxy) == [f"--proxy={proxy}"]
    assert runtime._proxy_args("socks5://127.0.0.1:10808") == []


def test_targeted_termination_ignores_unrelated_listener(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LOCAL_BOT_API_DATA_DIR", str(tmp_path / "data"))
    data_dir = runtime._writable_data_dir()
    runtime._write_pid(runtime._pid_path(data_dir), 101)
    monkeypatch.setattr(runtime, "_windows_listener_pids", lambda _port: {101, 202})
    monkeypatch.setattr(runtime, "_is_botapi", lambda pid: pid == 101)
    killed = []
    monkeypatch.setattr(runtime, "_kill_pid", lambda pid: killed.append(pid) or True)

    result = runtime._terminate_managed_server()

    assert result == [101]
    assert killed == [101]
    assert not runtime._pid_path(data_dir).exists()


def test_log_tail_redacts_token_hash_and_explicit_proxy_password(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_API_HASH", "private-api-hash")
    proxy = "http://user:proxy-secret@127.0.0.1:8080"
    log = tmp_path / "botapi-server.log"
    log.write_text(
        "GET /bot123456:secret-token/getMe\n"
        "hash=private-api-hash password=proxy-secret",
        encoding="utf-8",
    )

    tail = runtime._read_log_tail(str(log), proxy_url=proxy)

    assert "secret-token" not in tail
    assert "private-api-hash" not in tail
    assert "proxy-secret" not in tail
    assert "/bot***" in tail
