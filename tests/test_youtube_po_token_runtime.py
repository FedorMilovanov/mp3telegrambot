from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import youtube_po_token_runtime as po


def test_require_youtube_po_token_runtime_reports_browserless_bgutil(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    server = tmp_path / "server"
    server.mkdir()

    monkeypatch.setattr(po.metadata, "version", lambda _name: po.BGUTIL_VERSION)
    monkeypatch.setattr(po, "_require_bgutil_module", lambda _version: None)
    monkeypatch.setattr(po, "_require_node", lambda: node)
    monkeypatch.setattr(po, "_require_built_runtime", lambda: server)

    runtime = po.require_youtube_po_token_runtime()

    assert runtime.provider_version == "1.3.1"
    assert runtime.node_path == node
    assert runtime.server_home == server
    assert "bgutil 1.3.1" in runtime.status_text()
    assert "browser=none" in runtime.status_text()
    assert "WPC" not in runtime.status_text()


def test_missing_po_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(po.metadata, "version", missing)

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="не установлен"):
        po.require_youtube_po_token_runtime()


def test_locked_bgutil_provider_imports_against_current_ytdlp() -> None:
    provider_version = po._distribution_version(po.BGUTIL_DISTRIBUTION)
    assert provider_version == po.BGUTIL_VERSION
    po._require_bgutil_module(provider_version)


def test_bgutil_probe_uses_isolated_python_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="1.3.1\n", stderr="")

    monkeypatch.setattr(po.subprocess, "run", fake_run)
    po._require_bgutil_module(po.BGUTIL_VERSION)

    command = captured["command"]
    assert command[0] == po.sys.executable
    assert command[1] == "-c"
    assert command[3:] == [po.BGUTIL_MODULE, po.BGUTIL_VERSION]
    assert captured["kwargs"]["check"] is False


def test_bgutil_module_version_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        po.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=3,
            stdout="0.0.0\n",
            stderr="",
        ),
    )

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="version mismatch"):
        po._require_bgutil_module(po.BGUTIL_VERSION)


def test_built_runtime_is_pinned_to_exact_upstream_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "bgutil"
    server = home / "server"
    build = server / "build"
    build.mkdir(parents=True)
    (build / "main.js").write_text("// main", encoding="utf-8")
    (build / "generate_once.js").write_text("// once", encoding="utf-8")
    marker = home / ".mp3bot-runtime.json"
    marker.write_text(
        json.dumps({"version": po.BGUTIL_VERSION, "commit": po.BGUTIL_COMMIT}),
        encoding="utf-8",
    )

    monkeypatch.setattr(po, "BGUTIL_HOME", home)
    monkeypatch.setattr(po, "BGUTIL_SERVER", server)
    monkeypatch.setattr(po, "BGUTIL_MARKER", marker)

    assert po._require_built_runtime() == server


def test_wrong_runtime_commit_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "bgutil"
    server = home / "server"
    build = server / "build"
    build.mkdir(parents=True)
    (build / "main.js").write_text("// main", encoding="utf-8")
    (build / "generate_once.js").write_text("// once", encoding="utf-8")
    marker = home / ".mp3bot-runtime.json"
    marker.write_text(
        json.dumps({"version": po.BGUTIL_VERSION, "commit": "wrong"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(po, "BGUTIL_HOME", home)
    monkeypatch.setattr(po, "BGUTIL_SERVER", server)
    monkeypatch.setattr(po, "BGUTIL_MARKER", marker)

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="does not match"):
        po._require_built_runtime()


def test_ytdlp_policy_uses_mweb_bgutil_without_manual_token_or_cookie_conflict() -> None:
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")
    lower = config.lower()

    assert "youtube:player_client=mweb" in config
    assert "youtubepot-bgutilscript:server_home=" in config
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
        '--extractor-args "youtubepot-bgutilscript:server_home=.runtime/bgutil/server"\n',
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


def test_po_provider_dependencies_are_pinned_and_browser_stack_removed() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("requirements-lock.txt").read_text(encoding="utf-8")

    assert "bgutil-ytdlp-pot-provider==1.3.1" in requirements
    assert "bgutil-ytdlp-pot-provider==1.3.1" in lock
    assert "yt-dlp-getpot-wpc" not in requirements
    assert "yt-dlp-getpot-wpc" not in lock
    assert "nodriver" not in requirements
    assert "nodriver" not in lock


def test_bootstrap_pins_matching_bgutil_tag_and_commit() -> None:
    import tools.bootstrap_bgutil_provider as bootstrap

    assert bootstrap.BGUTIL_VERSION == po.BGUTIL_VERSION
    assert bootstrap.BGUTIL_COMMIT == po.BGUTIL_COMMIT
    source = Path("tools/bootstrap_bgutil_provider.py").read_text(encoding="utf-8")
    assert '"npm", "ci"' not in source  # executable is resolved, not shell-interpolated
    assert '"ci", "--no-audit", "--no-fund"' in source
    assert '"exec", "--", "tsc"' in source


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
