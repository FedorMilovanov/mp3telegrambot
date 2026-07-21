from __future__ import annotations

import os
import time
from pathlib import Path

import services.local_botapi_bootstrap as bootstrap


class _RunningProcess:
    returncode = None

    def poll(self):
        return None


def _base_env(monkeypatch):
    monkeypatch.setenv("LOCAL_BOT_API_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("BOT_TOKEN", "123:test-token")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5h://127.0.0.1:10808")
    monkeypatch.setenv("LOCAL_BOT_API_CLOUD_FALLBACK", "1")
    monkeypatch.setenv("LOCAL_BOT_API_SMART_BOOTSTRAP", "1")


def test_healthy_local_server_is_not_restarted(monkeypatch):
    _base_env(monkeypatch)
    restarted = []
    monkeypatch.setattr(bootstrap, "_probe_getme", lambda *_: (True, "@healthy"))
    monkeypatch.setattr(bootstrap, "_terminate_stale_server", lambda: restarted.append(True))

    bootstrap.prepare_local_bot_api()

    assert os.environ["LOCAL_BOT_API_URL"] == "http://127.0.0.1:8081"
    assert os.environ["MP3BOT_EFFECTIVE_BOT_API"] == "local"
    assert restarted == []


def test_negative_route_hint_still_attempts_real_server(monkeypatch):
    _base_env(monkeypatch)
    calls = []
    probe_results = iter([(False, "initial timeout")])
    monkeypatch.setattr(bootstrap, "_probe_getme", lambda *_: next(probe_results))
    monkeypatch.setattr(bootstrap, "_system_telegram_route_available", lambda *_: False)
    monkeypatch.setattr(bootstrap, "_terminate_stale_server", lambda: calls.append("stop"))
    monkeypatch.setattr(bootstrap, "_wait_until_port_closes", lambda *_: None)
    monkeypatch.setattr(
        bootstrap,
        "_start_local_server",
        lambda *_: (_RunningProcess(), r"C:\Temp\botapi-server.log"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_wait_for_getme",
        lambda *_args, **_kwargs: (True, "@recovered", 2),
    )

    bootstrap.prepare_local_bot_api()

    assert calls == ["stop"]
    assert os.environ["LOCAL_BOT_API_URL"] == "http://127.0.0.1:8081"
    assert os.environ["MP3BOT_EFFECTIVE_BOT_API"] == "local"


def test_real_getme_failure_selects_cloud_and_enables_media_fallback(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LOCAL_BOT_API_PROXY_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setattr(bootstrap, "_probe_getme", lambda *_: (False, "timeout"))
    monkeypatch.setattr(bootstrap, "_system_telegram_route_available", lambda *_: False)
    monkeypatch.setattr(bootstrap, "_terminate_stale_server", lambda: None)
    monkeypatch.setattr(bootstrap, "_wait_until_port_closes", lambda *_: None)
    monkeypatch.setattr(
        bootstrap,
        "_start_local_server",
        lambda *_: (_RunningProcess(), r"C:\Temp\missing.log"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_wait_for_getme",
        lambda *_args, **_kwargs: (False, "timed out", 4),
    )
    monkeypatch.setattr(bootstrap, "_read_log_tail", lambda *_: "failed to connect: timeout")

    bootstrap.prepare_local_bot_api()

    assert os.environ["LOCAL_BOT_API_URL"] == ""
    assert os.environ["LOCAL_BOT_API_WAIT_LOCAL"] == "0"
    assert os.environ["MP3BOT_EFFECTIVE_BOT_API"] == "cloud"
    assert os.environ["CLOUD_MEDIA_AUTO_COMPRESS"] == "1"
    assert os.environ["LOCAL_BOT_API_PROXY_URL"] == ""
    assert os.environ["TELEGRAM_PROXY_URL"].startswith("socks5h://")


def test_stale_process_is_restarted_once_and_local_mode_is_preserved(monkeypatch):
    _base_env(monkeypatch)
    calls = []
    monkeypatch.setattr(bootstrap, "_probe_getme", lambda *_: (False, "stale"))
    monkeypatch.setattr(bootstrap, "_system_telegram_route_available", lambda *_: True)
    monkeypatch.setattr(bootstrap, "_terminate_stale_server", lambda: calls.append("stop"))
    monkeypatch.setattr(bootstrap, "_wait_until_port_closes", lambda *_: None)
    monkeypatch.setattr(
        bootstrap,
        "_start_local_server",
        lambda *_: (_RunningProcess(), r"C:\Temp\botapi-server.log"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_wait_for_getme",
        lambda *_args, **_kwargs: (True, "@recovered", 3),
    )

    bootstrap.prepare_local_bot_api()

    assert calls == ["stop"]
    assert os.environ["LOCAL_BOT_API_URL"] == "http://127.0.0.1:8081"
    assert os.environ["LOCAL_BOT_API_WAIT_LOCAL"] == "1"


def test_wait_loop_uses_one_real_deadline_and_short_probes():
    timeouts = []

    def failed_probe(_url: str, timeout: float):
        timeouts.append(timeout)
        return False, "timeout"

    started = time.monotonic()
    ok, detail, attempts = bootstrap._wait_for_getme(
        "http://127.0.0.1:8081/botTOKEN/getMe",
        _RunningProcess(),
        started + 0.35,
        probe=failed_probe,
    )
    elapsed = time.monotonic() - started

    assert ok is False
    assert detail == "timeout"
    assert attempts >= 1
    assert elapsed < 0.8
    assert all(0 < timeout <= 1.5 for timeout in timeouts)


def test_entrypoint_runs_runtime_before_importing_main_and_installs_fallback_after():
    source = Path("bot_new.py").read_text(encoding="utf-8")
    runtime_pos = source.index("from services.local_botapi_runtime import")
    bootstrap_pos = source.index("prepare_local_bot_api()")
    main_import_pos = source.index("from main import main")
    fallback_pos = source.index("install_cloud_media_fallback()")

    assert runtime_pos < bootstrap_pos < main_import_pos < fallback_pos
    assert "PID/портом этого проекта" in source
