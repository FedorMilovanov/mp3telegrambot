from __future__ import annotations

from pathlib import Path

import pytest

import services.bgutil_http_provider as http_provider
import services.youtube_po_token_runtime as po_runtime


def _exact_ping() -> dict[str, object]:
    return {
        "server_uptime": 2.5,
        "version": po_runtime.BGUTIL_EXPECTED_VERSION,
        "owner": http_provider.HTTP_OWNER_POLICY,
        "provider_marker": http_provider.EXPECTED_PROVIDER_MARKER,
    }


def test_repo_ytdlp_policy_is_http_only_and_fail_closed() -> None:
    po_runtime._require_ytdlp_policy()
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")
    assert po_runtime.EXPECTED_BGUTIL_ROUTE in config
    assert '--extractor-args "youtubepot-bgutilscript:' not in config
    assert '--extractor-args "youtubepot-bgutilhttp:' in config


def test_policy_rejects_script_fallback_even_with_http_route(tmp_path: Path) -> None:
    config = tmp_path / "yt-dlp.conf"
    config.write_text(
        "\n".join(
            [
                "--no-plugin-dirs",
                "--plugin-dirs .runtime/bgutil-ytdlp-pot-provider",
                '--extractor-args "youtube:player_client=mweb"',
                '--extractor-args "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4417"',
                '--extractor-args "youtubepot-bgutilscript:server_home=.runtime/bgutil-ytdlp-pot-provider/server"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(po_runtime.YouTubePoTokenRuntimeError, match="script/server_home"):
        po_runtime._require_ytdlp_policy(config)


def test_exact_existing_loopback_provider_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_provider, "_require_runtime_files", lambda: None)
    monkeypatch.setattr(http_provider, "_probe_ping", _exact_ping)
    monkeypatch.setattr(
        http_provider,
        "_require_node",
        lambda: pytest.fail("reusing exact provider must not spawn Node"),
    )

    session = http_provider.start_bgutil_http_provider()
    assert session.owned is False
    assert session.process is None
    assert "owned=reused" in session.status_text
    session.close()


def test_foreign_service_on_provider_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _exact_ping()
    payload["owner"] = "foreign-service"
    monkeypatch.setattr(http_provider, "_require_runtime_files", lambda: None)
    monkeypatch.setattr(http_provider, "_probe_ping", lambda: payload)

    with pytest.raises(http_provider.BgutilHttpProviderError, match="foreign"):
        http_provider.start_bgutil_http_provider()


def test_wrong_provider_marker_is_rejected() -> None:
    payload = _exact_ping()
    payload["provider_marker"] = "1.3.1@wrong"
    with pytest.raises(http_provider.BgutilHttpProviderError, match="marker mismatch"):
        http_provider._require_exact_ping(payload)


def test_owned_session_cleanup_is_idempotent() -> None:
    calls: list[str] = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, *, timeout):
            calls.append(f"wait:{timeout}")
            return 0

    class FakeLog:
        def close(self):
            calls.append("close")

    session = http_provider.BgutilHttpProviderSession(
        process=FakeProcess(),  # type: ignore[arg-type]
        log_stream=FakeLog(),  # type: ignore[arg-type]
        owned=True,
    )
    session.close()
    session.close()

    assert calls == ["terminate", f"wait:{http_provider._STOP_TIMEOUT_SEC}", "close"]


def test_loopback_wrapper_preserves_exact_engine_and_never_binds_wildcard() -> None:
    source = Path("tools/bgutil_http_loopback.mjs").read_text(encoding="utf-8")
    assert 'const HOST = "127.0.0.1"' in source
    assert "server.listen(PORT, HOST" in source
    assert "0.0.0.0" not in source
    assert 'host: "::"' not in source
    assert "SessionManager" in source
    assert "provider_marker" in source
    assert "/get_pot" in source
    assert "body.challenge" in source
    assert "body.innertube_context" in source


def test_entrypoint_owns_provider_only_around_bot_loop() -> None:
    source = Path("bot_new.py").read_text(encoding="utf-8")
    start = source.index("provider_session = start_bgutil_http_provider()")
    run = source.index("return run_bot_process(_main_module)")
    close = source.index("provider_session.close()")
    bootstrap = source.index("require_runtime_ready()")

    assert bootstrap < start < run < close
