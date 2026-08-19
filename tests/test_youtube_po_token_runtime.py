from __future__ import annotations

from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import youtube_po_token_runtime as po


def test_require_youtube_po_token_runtime_reports_browserless_bgutil(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_home = tmp_path / "server"
    provider_home.mkdir()

    def installed_version(name: str) -> str:
        if name == po.BGUTIL_DISTRIBUTION:
            return "1.3.1"
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(po.metadata, "version", installed_version)
    monkeypatch.setattr(po, "_require_bgutil_module", lambda _version: None)
    monkeypatch.setattr(po, "_require_provider_build", lambda: provider_home)
    monkeypatch.setattr(po, "_require_node", lambda: "22.14.0")

    runtime = po.require_youtube_po_token_runtime()

    assert runtime.provider_version == "1.3.1"
    assert runtime.provider_commit == po.BGUTIL_EXPECTED_COMMIT
    assert runtime.node_version == "22.14.0"
    assert runtime.provider_home == provider_home
    assert runtime.status_text() == (
        f"bgutil 1.3.1@{po.BGUTIL_EXPECTED_COMMIT[:8]}; "
        "node=22.14.0; browserless=on"
    )


def test_missing_po_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(po.metadata, "version", missing)

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="не установлен"):
        po.require_youtube_po_token_runtime()


def test_locked_bgutil_provider_imports_against_current_ytdlp() -> None:
    provider_version = po._distribution_version(po.BGUTIL_DISTRIBUTION)
    assert provider_version == po.BGUTIL_EXPECTED_VERSION
    po._require_bgutil_module(provider_version)


def test_bgutil_probe_uses_isolated_python_process(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="1.3.1\n", stderr="")

    monkeypatch.setattr(po.subprocess, "run", fake_run)

    po._require_bgutil_module("1.3.1")

    command = captured["command"]
    assert command[0] == po.sys.executable
    assert command[1] == "-c"
    assert command[3:] == [po.BGUTIL_MODULE, "1.3.1"]
    assert captured["kwargs"]["check"] is False


def test_bgutil_module_version_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        po.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=3,
            stdout="0.0.0\n",
            stderr="",
        ),
    )

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="рассинхронизированную"):
        po._require_bgutil_module("1.3.1")


def test_bgutil_import_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        po.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ImportError: incompatible provider",
        ),
    )

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="не импортируется"):
        po._require_bgutil_module("1.3.1")


def test_missing_bgutil_build_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BGUTIL_PROVIDER_HOME", str(tmp_path / "missing"))

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="Start Bot.bat"):
        po._require_provider_build()


def test_bgutil_runtime_marker_must_match_exact_commit(monkeypatch, tmp_path):
    server = tmp_path / "provider" / "server"
    (server / "build").mkdir(parents=True)
    (server / "build" / "generate_once.js").write_text("// ok", encoding="utf-8")
    (server.parent / ".mp3bot-bgutil-version").write_text(
        "1.3.1@wrong-commit\n", encoding="utf-8"
    )
    monkeypatch.setenv("BGUTIL_PROVIDER_HOME", str(server))

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="pinned commit"):
        po._require_provider_build()


def test_runtime_rejects_reintroduced_wpc_provider_with_exact_recovery(monkeypatch):
    real_version = po.metadata.version

    def fake_version(name: str):
        if name == po.LEGACY_WPC_DISTRIBUTION:
            return "1.1.2"
        return real_version(name)

    monkeypatch.setattr(po.metadata, "version", fake_version)
    with pytest.raises(po.YouTubePoTokenRuntimeError) as caught:
        po._require_no_legacy_browser_provider()

    message = str(caught.value)
    assert "browser-based" in message
    assert (
        ".\\.venv\\Scripts\\python.exe -m pip uninstall -y "
        "yt-dlp-getpot-wpc nodriver"
    ) in message
    assert "Start Bot.bat" in message


def test_old_wpc_browser_stack_is_not_a_dependency() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("requirements-lock.txt").read_text(encoding="utf-8")

    assert "bgutil-ytdlp-pot-provider==1.3.1" in requirements
    assert "bgutil-ytdlp-pot-provider==1.3.1" in lock
    assert "yt-dlp-getpot-wpc" not in requirements
    assert "yt-dlp-getpot-wpc" not in lock
    assert "nodriver==" not in requirements
    assert "nodriver==" not in lock


def test_ytdlp_policy_uses_mweb_bgutil_without_manual_token_or_cookie_conflict() -> None:
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")
    lower = config.lower()

    assert "youtube:player_client=mweb" in config
    assert "youtubepot-bgutilscript:server_home=.runtime/bgutil-ytdlp-pot-provider/server" in config
    assert "po_token=" not in lower
    assert "--cookies" not in lower
    assert "--cookies-from-browser" not in lower
    assert "wpc" not in lower


def test_mweb_config_and_cookies_file_are_composed_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import services.ffmpeg as ff

    (tmp_path / "yt-dlp.conf").write_text(
        '--extractor-args "youtube:player_client=mweb"\n'
        '--extractor-args "youtubepot-bgutilscript:server_home=.runtime/bgutil-ytdlp-pot-provider/server"\n',
        encoding="utf-8",
    )
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ff, "COOKIES_FILE", cookies)
    monkeypatch.setattr(ff, "_supported_js_runtimes", lambda: [])
    monkeypatch.setattr(ff, "_proxy_for_ytdlp", lambda: "")

    args = ff._build_ytdlp_base_args()
    joined = " ".join(map(str, args))

    assert "--config-location yt-dlp.conf" in joined
    assert f"--cookies {cookies}" in joined


def test_start_launcher_provisions_bgutil_before_bot() -> None:
    launcher = Path("Start Bot.bat").read_text(encoding="utf-8")
    provision = launcher.index("tools\\ensure_bgutil_provider.py")
    start = launcher.index('"%VENV_PYTHON%" bot_new.py')

    assert provision < start


def test_factory_max_quality_selectors_remain_fail_closed() -> None:
    source = Path("services/shorts_factory_source.py").read_text(encoding="utf-8")

    assert '"bestaudio/best"' in source
    assert '"bestvideo+bestaudio/best"' in source
    assert '"--abort-on-unavailable-fragments"' in source
    assert '"18"' not in source


def test_bot_entrypoint_requires_po_runtime_before_runtime_bootstrap() -> None:
    entry = Path("bot_new.py").read_text(encoding="utf-8")
    po_index = entry.index("require_youtube_po_token_runtime()")
    bootstrap_index = entry.index("bootstrap_pre_main()")

    assert po_index < bootstrap_index
    assert "format 18/360p fallback не используется" in entry
