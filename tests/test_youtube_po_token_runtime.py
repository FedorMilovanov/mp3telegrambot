from __future__ import annotations

from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import youtube_po_token_runtime as po


def test_require_youtube_po_token_runtime_reports_exact_source_bgutil(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_root = tmp_path / "provider"
    provider_home = provider_root / "server"
    plugin_root = provider_root / "plugin"
    provider_home.mkdir(parents=True)
    plugin_root.mkdir()

    monkeypatch.setattr(po, "_require_provider_build", lambda: provider_home)

    def require_module(actual_plugin_root: Path, expected_version: str) -> str:
        assert actual_plugin_root == plugin_root
        assert expected_version == po.BGUTIL_EXPECTED_VERSION
        return "1.3.1"

    monkeypatch.setattr(po, "_require_bgutil_module", require_module)
    monkeypatch.setattr(po, "_require_node", lambda: "22.14.0")

    runtime = po.require_youtube_po_token_runtime()

    assert runtime.provider_version == "1.3.1"
    assert runtime.provider_commit == po.BGUTIL_EXPECTED_COMMIT
    assert runtime.node_version == "22.14.0"
    assert runtime.provider_home == provider_home
    assert runtime.plugin_root == plugin_root
    assert runtime.status_text() == (
        f"bgutil 1.3.1@{po.BGUTIL_EXPECTED_COMMIT[:8]}; "
        "node=22.14.0; browserless=on; source-only=on"
    )


def test_missing_exact_source_provider_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BGUTIL_PROVIDER_HOME", str(tmp_path / "missing" / "server"))

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="exact-source runtime"):
        po._require_provider_build()


def test_bgutil_probe_uses_exact_source_plugin_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    plugin_root = tmp_path / "plugin"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=f"1.3.1\n{plugin_root / 'yt_dlp_plugins' / 'extractor' / 'getpot_bgutil.py'}\n",
            stderr="",
        )

    monkeypatch.setattr(po.subprocess, "run", fake_run)

    assert po._require_bgutil_module(plugin_root, "1.3.1") == "1.3.1"

    command = captured["command"]
    assert command[0] == po.sys.executable
    assert command[1] == "-c"
    assert command[3:] == [str(plugin_root.resolve()), po.BGUTIL_MODULE, "1.3.1"]
    assert captured["kwargs"]["check"] is False


def test_bgutil_source_module_version_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        po.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=3,
            stdout="0.0.0\n/tmp/provider/plugin/yt_dlp_plugins/extractor/getpot_bgutil.py\n",
            stderr="",
        ),
    )

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="рассинхронизированную"):
        po._require_bgutil_module(tmp_path / "plugin", "1.3.1")


def test_bgutil_probe_rejects_site_packages_shadow(monkeypatch, tmp_path):
    monkeypatch.setattr(
        po.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=4,
            stdout="1.3.1\n/site-packages/yt_dlp_plugins/extractor/getpot_bgutil.py\n",
            stderr="",
        ),
    )

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="site-packages"):
        po._require_bgutil_module(tmp_path / "plugin", "1.3.1")


def test_bgutil_import_failure_fails_closed(monkeypatch, tmp_path):
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
        po._require_bgutil_module(tmp_path / "plugin", "1.3.1")


def test_bgutil_runtime_marker_must_match_exact_commit(monkeypatch, tmp_path):
    provider = tmp_path / "provider"
    server = provider / "server"
    (server / "build").mkdir(parents=True)
    (server / "build" / "generate_once.js").write_text("// ok", encoding="utf-8")
    plugin = provider / "plugin" / "yt_dlp_plugins" / "extractor"
    plugin.mkdir(parents=True)
    (plugin / "getpot_bgutil.py").write_text("# ok", encoding="utf-8")
    (provider / ".mp3bot-bgutil-version").write_text(
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


def test_python_lock_has_no_po_provider_wheel() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("requirements-lock.txt").read_text(encoding="utf-8")

    assert "bgutil-ytdlp-pot-provider==" not in requirements
    assert "bgutil-ytdlp-pot-provider==" not in lock
    assert "yt-dlp-getpot-wpc" not in requirements
    assert "yt-dlp-getpot-wpc" not in lock
    assert "nodriver==" not in requirements
    assert "nodriver==" not in lock


def test_ytdlp_policy_restricts_plugins_to_exact_source_mweb_route() -> None:
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")
    lower = config.lower()

    assert "--no-plugin-dirs" in config
    assert "--plugin-dirs .runtime/bgutil-ytdlp-pot-provider" in config
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
        "--no-plugin-dirs\n"
        "--plugin-dirs .runtime/bgutil-ytdlp-pot-provider\n"
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
