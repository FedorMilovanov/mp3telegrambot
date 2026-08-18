from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import ensure_bgutil_provider as setup


def test_current_runtime_skips_network_and_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    server = provider / "server"
    generated = server / "build" / "generate_once.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("// ready\n", encoding="utf-8")
    marker = provider / ".mp3bot-bgutil-version"
    marker.write_text(setup.BGUTIL_VERSION + "\n", encoding="utf-8")

    monkeypatch.setattr(setup, "PROVIDER_ROOT", provider)
    monkeypatch.setattr(setup, "SERVER_ROOT", server)
    monkeypatch.setattr(setup, "GENERATE_SCRIPT", generated)
    monkeypatch.setattr(setup, "VERSION_MARKER", marker)
    monkeypatch.setattr(setup, "_node_executable", lambda: "node")
    monkeypatch.setattr(
        setup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("current runtime must not rebuild"),
    )

    assert setup.ensure_bgutil_provider() == server


def test_missing_node_fails_before_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)

    with pytest.raises(setup.ProvisionError, match="Node.js"):
        setup._node_executable()


def test_node_version_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="v18.20.0\n"),
    )

    with pytest.raises(setup.ProvisionError, match="< 20"):
        setup._node_executable()


def test_provisioner_is_pinned_and_browserless_by_contract() -> None:
    source = Path("tools/ensure_bgutil_provider.py").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert 'BGUTIL_VERSION = "1.3.1"' in source
    assert '"--branch",' in source
    assert "npm" in source and "tsc" in source
    assert "Chrome" not in source
    assert "nodriver" not in source
    assert "bgutil-ytdlp-pot-provider==1.3.1" in requirements


def test_launcher_removes_legacy_browser_provider_before_start() -> None:
    launcher = Path("Start Bot.bat").read_text(encoding="utf-8")
    cleanup = launcher.index("pip uninstall -y yt-dlp-getpot-wpc nodriver")
    provision = launcher.index("tools\\ensure_bgutil_provider.py")
    start = launcher.index('"%VENV_PYTHON%" bot_new.py')

    assert cleanup < provision < start
