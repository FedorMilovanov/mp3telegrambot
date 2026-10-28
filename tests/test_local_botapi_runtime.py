from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

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


def test_global_termination_failure_returns_empty_result(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LOCAL_BOT_API_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOCAL_BOT_API_ALLOW_GLOBAL_KILL", "1")
    monkeypatch.setattr(runtime, "_windows_listener_pids", lambda _port: set())

    def fail_run(*_args, **_kwargs):
        raise RuntimeError("task manager unavailable")

    monkeypatch.setattr(runtime.subprocess, "run", fail_run)

    assert runtime._terminate_managed_server() == []


def test_windows_listener_probe_uses_resolved_netstat(monkeypatch):
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="  TCP    127.0.0.1:8081    0.0.0.0:0    LISTENING    4242\r\n",
        )
    )
    monkeypatch.setattr(runtime, "resolve_required_executable", lambda name: f"/resolved/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", run)

    assert runtime._windows_listener_pids(8081) == {4242}
    assert run.call_args.args[0] == ["/resolved/netstat", "-ano", "-p", "tcp"]


def test_windows_name_probe_uses_resolved_tasklist(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='"Telegram-Bot-API.exe","4242"\r\n'))
    monkeypatch.setattr(runtime, "resolve_required_executable", lambda name: f"/resolved/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", run)

    assert runtime._windows_name(4242) == "telegram-bot-api.exe"
    assert run.call_args.args[0] == [
        "/resolved/tasklist",
        "/FI",
        "PID eq 4242",
        "/FO",
        "CSV",
        "/NH",
    ]


def test_posix_name_probe_uses_resolved_ps(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="telegram-bot-api\n"))
    monkeypatch.setattr(runtime, "resolve_required_executable", lambda name: f"/resolved/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", run)

    assert runtime._posix_name(4242) == "telegram-bot-api"
    assert run.call_args.args[0] == ["/resolved/ps", "-p", "4242", "-o", "comm="]


def test_targeted_kill_uses_resolved_platform_executable(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(runtime, "resolve_required_executable", lambda name: f"/resolved/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", run)

    assert runtime._kill_pid(4242) is True
    assert run.call_args.args[0] == ["/resolved/taskkill", "/F", "/T", "/PID", "4242"]


def test_posix_targeted_kill_uses_resolved_executable(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(runtime, "resolve_required_executable", lambda name: f"/resolved/{name}")
    monkeypatch.setattr(runtime.subprocess, "run", run)

    assert runtime._kill_pid(4242) is True
    assert run.call_args.args[0] == ["/resolved/kill", "-TERM", "4242"]


def test_log_tail_redacts_token_hash_and_explicit_proxy_password(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_API_HASH", "private-api-hash")
    proxy = "http://user:proxy-secret@127.0.0.1:8080"
    log = tmp_path / "botapi-server.log"
    log.write_text(
        "GET /bot123456:secret-token/getMe\nhash=private-api-hash password=proxy-secret",
        encoding="utf-8",
    )

    tail = runtime._read_log_tail(str(log), proxy_url=proxy)

    assert "secret-token" not in tail
    assert "private-api-hash" not in tail
    assert "proxy-secret" not in tail
    assert "/bot***" in tail
