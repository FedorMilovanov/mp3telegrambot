from __future__ import annotations

import pytest

from services import bgutil_http_runtime as http_runtime


@pytest.fixture(autouse=True)
def _reset_owned_process(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(http_runtime, "_OWNED_PROCESS", None)
    monkeypatch.setattr(http_runtime, "_CLEANUP_REGISTERED", False)


def test_reuses_version_matched_existing_http_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(
        http_runtime,
        "_ping",
        lambda *_args, **_kwargs: {"version": "1.3.1", "server_uptime": 12},
    )

    def unexpected_popen(*_args, **_kwargs):
        raise AssertionError("matching live provider must be reused")

    monkeypatch.setattr(http_runtime.subprocess, "Popen", unexpected_popen)
    result = http_runtime.ensure_bgutil_http_runtime(
        node_executable="node",
        server_home=tmp_path,
        expected_version="1.3.1",
    )
    assert result == http_runtime.DEFAULT_BASE_URL


def test_existing_wrong_version_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        http_runtime,
        "_ping",
        lambda *_args, **_kwargs: {"version": "9.9.9"},
    )
    with pytest.raises(http_runtime.BgutilHttpRuntimeError, match="неверной версией"):
        http_runtime.ensure_bgutil_http_runtime(
            node_executable="node",
            server_home=tmp_path,
            expected_version="1.3.1",
        )


def test_missing_compiled_http_server_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(http_runtime, "_ping", lambda *_args, **_kwargs: None)
    with pytest.raises(http_runtime.BgutilHttpRuntimeError, match="build/main.js"):
        http_runtime.ensure_bgutil_http_runtime(
            node_executable="node",
            server_home=tmp_path,
            expected_version="1.3.1",
        )


def test_spawns_pinned_main_once_and_waits_for_versioned_ping(monkeypatch, tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    main_js = build / "main.js"
    main_js.write_text("// compiled", encoding="utf-8")

    ping_results = iter([None, None, {"version": "1.3.1", "server_uptime": 0.2}])
    monkeypatch.setattr(
        http_runtime,
        "_ping",
        lambda *_args, **_kwargs: next(ping_results),
    )
    monkeypatch.setattr(http_runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(http_runtime, "_register_cleanup_once", lambda: None)

    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 12345
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    process = FakeProcess()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(http_runtime.subprocess, "Popen", fake_popen)

    result = http_runtime.ensure_bgutil_http_runtime(
        node_executable="C:/Node/node.exe",
        server_home=tmp_path,
        expected_version="1.3.1",
        startup_timeout=2,
    )

    assert result == http_runtime.DEFAULT_BASE_URL
    assert captured["command"] == [
        "C:/Node/node.exe",
        str(main_js.resolve()),
        "--port",
        "4416",
    ]
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())
    assert http_runtime._OWNED_PROCESS is process


def test_upstream_http_route_is_loopback_for_ytdlp_even_if_server_bind_is_broad():
    assert http_runtime.DEFAULT_BASE_URL == "http://127.0.0.1:4416"
