from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import ensure_bgutil_provider as setup


def _write_ready_runtime(provider: Path) -> tuple[Path, Path, Path, Path]:
    server = provider / "server"
    generated = server / "build" / "generate_once.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("// ready\n", encoding="utf-8")
    (server / "node_modules").mkdir()
    plugin = provider / "plugin" / "yt_dlp_plugins" / "extractor" / "getpot_bgutil.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("__version__ = '1.3.1'\n", encoding="utf-8")
    marker = provider / ".mp3bot-bgutil-version"
    marker.write_text(
        f"{setup.BGUTIL_VERSION}@{setup.BGUTIL_COMMIT}\n",
        encoding="utf-8",
    )
    return server, generated, plugin, marker


def test_current_runtime_skips_network_and_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    server, generated, plugin, marker = _write_ready_runtime(provider)

    monkeypatch.setattr(setup, "PROVIDER_ROOT", provider)
    monkeypatch.setattr(setup, "SERVER_ROOT", server)
    monkeypatch.setattr(setup, "GENERATE_SCRIPT", generated)
    monkeypatch.setattr(setup, "PLUGIN_ENTRY", plugin)
    monkeypatch.setattr(setup, "NODE_MODULES", server / "node_modules")
    monkeypatch.setattr(setup, "VERSION_MARKER", marker)
    monkeypatch.setattr(setup, "_node_executable", lambda: "node")
    monkeypatch.setattr(setup, "_require_script_version", lambda *_args, **_kwargs: "1.3.1")
    monkeypatch.setattr(
        setup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("current runtime must not rebuild"),
    )

    assert setup.ensure_bgutil_provider() == server


def test_version_only_marker_is_not_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    generated = provider / "server" / "build" / "generate_once.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("// ready\n", encoding="utf-8")
    marker = provider / ".mp3bot-bgutil-version"
    marker.write_text(setup.BGUTIL_VERSION + "\n", encoding="utf-8")

    monkeypatch.setattr(setup, "GENERATE_SCRIPT", generated)
    monkeypatch.setattr(setup, "VERSION_MARKER", marker)

    assert setup._runtime_is_current() is False


def test_current_marker_without_python_plugin_is_not_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    server = provider / "server"
    generated = server / "build" / "generate_once.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("// ready\n", encoding="utf-8")
    (server / "node_modules").mkdir()
    marker = provider / ".mp3bot-bgutil-version"
    marker.write_text(
        f"{setup.BGUTIL_VERSION}@{setup.BGUTIL_COMMIT}\n",
        encoding="utf-8",
    )
    missing_plugin = provider / "plugin" / "yt_dlp_plugins" / "extractor" / "getpot_bgutil.py"

    monkeypatch.setattr(setup, "GENERATE_SCRIPT", generated)
    monkeypatch.setattr(setup, "PLUGIN_ENTRY", missing_plugin)
    monkeypatch.setattr(setup, "NODE_MODULES", server / "node_modules")
    monkeypatch.setattr(setup, "VERSION_MARKER", marker)

    assert setup._runtime_is_current() is False


def test_current_marker_without_node_modules_is_not_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    server, generated, plugin, marker = _write_ready_runtime(provider)
    (server / "node_modules").rmdir()

    monkeypatch.setattr(setup, "GENERATE_SCRIPT", generated)
    monkeypatch.setattr(setup, "PLUGIN_ENTRY", plugin)
    monkeypatch.setattr(setup, "NODE_MODULES", server / "node_modules")
    monkeypatch.setattr(setup, "VERSION_MARKER", marker)

    assert setup._runtime_is_current() is False


def test_missing_node_fails_before_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)

    with pytest.raises(setup.ProvisionError, match="Node.js"):
        setup._node_executable()


def test_node_version_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        setup,
        "_owned_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="v21.7.0\n", stderr="", returncode=0
        ),
    )

    with pytest.raises(setup.ProvisionError, match="< 22"):
        setup._node_executable()


def test_compiled_script_offline_smoke_requires_exact_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "generate_once.js"
    script.write_text("// compiled\n", encoding="utf-8")
    monkeypatch.setattr(
        setup,
        "_owned_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="1.3.1\n", stderr="", returncode=0
        ),
    )

    assert setup._require_script_version("node", script=script, cwd=tmp_path) == "1.3.1"

    monkeypatch.setattr(
        setup,
        "_owned_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="", stderr="ERR_MODULE_NOT_FOUND commander", returncode=1
        ),
    )
    with pytest.raises(setup.ProvisionError, match="offline smoke test"):
        setup._require_script_version("node", script=script, cwd=tmp_path)


def test_command_timeout_is_bounded_and_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    async def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["npm", "ci"], timeout=1)

    monkeypatch.setattr(setup, "run_cancellable_process", timeout)
    with pytest.raises(setup.ProvisionError, match="timed out"):
        setup._run(["npm", "ci"], timeout=1)


def test_build_command_inherits_stdio_but_keeps_shared_process_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_owned(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, None, None)

    monkeypatch.setattr(setup, "run_cancellable_process", fake_owned)
    setup._run(["npm", "ci"], timeout=12)

    assert captured["command"] == ["npm", "ci"]
    assert captured["timeout"] == 12
    assert captured["text"] is True
    assert captured["capture_output"] is False


def test_windows_command_shim_is_run_through_comspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    command = setup._platform_command(
        [r"C:\Program Files\nodejs\npm.cmd", "ci"],
        platform_name="nt",
    )

    assert command[:4] == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
    ]
    assert "npm.cmd" in command[4]
    assert "ci" in command[4]


def test_exact_checkout_fetches_reviewed_commit(monkeypatch, tmp_path):
    commands: list[tuple[list[str], Path | None]] = []

    def fake_run(command, *, cwd=None, **_kwargs):
        commands.append((command, cwd))

    monkeypatch.setattr(setup, "_run", fake_run)
    staging = tmp_path / "source"
    setup._checkout_exact_source("git", staging)

    assert staging.is_dir()
    assert (["git", "init"], staging) in commands
    assert (
        ["git", "fetch", "--depth", "1", "origin", setup.BGUTIL_COMMIT],
        staging,
    ) in commands
    assert (["git", "checkout", "--detach", "FETCH_HEAD"], staging) in commands


def test_kernel_lock_rejects_second_owner_and_survives_holder_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    lock_path = runtime_root / ".bgutil-provision.lock"
    monkeypatch.setattr(setup, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(setup, "PROVISION_LOCK", lock_path)

    project_root = Path(__file__).resolve().parents[1]
    child_code = "\n".join(
        [
            "import sys, time",
            "from pathlib import Path",
            "from tools import ensure_bgutil_provider as setup",
            "lock = Path(sys.argv[1])",
            "setup.RUNTIME_ROOT = lock.parent",
            "setup.PROVISION_LOCK = lock",
            "with setup._provision_lock(wait_seconds=30):",
            "    print('LOCKED', flush=True)",
            "    time.sleep(60)",
        ]
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(lock_path)],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert child.stdout is not None
        marker = child.stdout.readline().strip()
        if marker != "LOCKED":
            stderr = child.stderr.read() if child.stderr is not None else ""
            pytest.fail(f"lock holder did not start: marker={marker!r} stderr={stderr}")

        with pytest.raises(setup.ProvisionError, match="Другая установка"):
            with setup._provision_lock(wait_seconds=0.3):
                pytest.fail("second process acquired a live kernel lock")

        child.kill()
        child.wait(timeout=10)

        # The file intentionally remains, but the kernel ownership is gone as
        # soon as the holder dies; no stale-age or unlink recovery is needed.
        assert lock_path.exists()
        with setup._provision_lock(wait_seconds=2):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_lock_wait_rejects_nonfinite_values() -> None:
    with pytest.raises(setup.ProvisionError, match="finite"):
        with setup._provision_lock(wait_seconds=float("inf")):
            pass


def test_provisioner_is_exact_source_and_browserless_by_contract() -> None:
    source = Path("tools/ensure_bgutil_provider.py").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("requirements-lock.txt").read_text(encoding="utf-8")
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")

    assert 'BGUTIL_VERSION = "1.3.1"' in source
    assert setup.BGUTIL_COMMIT == "a0be2352807e3bd6991f09d2cab685a0ab825b26"
    assert '"fetch", "--depth", "1", "origin", BGUTIL_COMMIT' in source
    assert '"node_modules" / ".bin"' in source
    assert '"ci", "--no-audit", "--no-fund"' in source
    assert "--version" in source
    assert "PROVISION_LOCK" in source
    assert "run_cancellable_process" in source
    assert "uuid.uuid4" in source
    assert "_LOCK_STALE_SEC" not in source
    assert "PROVISION_LOCK.unlink" not in source
    assert "npx" not in source
    assert "Chrome" not in source
    assert "nodriver" not in source
    assert "bgutil-ytdlp-pot-provider==" not in requirements
    assert "bgutil-ytdlp-pot-provider==" not in lock
    assert "--no-plugin-dirs" in config
    assert "--plugin-dirs .runtime/bgutil-ytdlp-pot-provider" in config


def test_launcher_reconciles_legacy_providers_before_shared_provisioner() -> None:
    launcher = Path("Start Bot.bat").read_text(encoding="utf-8")
    cleanup = launcher.index(
        "pip uninstall -y yt-dlp-getpot-wpc nodriver bgutil-ytdlp-pot-provider"
    )
    provision = launcher.index("tools\\ensure_bgutil_provider.py")
    start = launcher.index('"%VENV_PYTHON%" bot_new.py')

    assert cleanup < provision < start
    assert ".wpc-provider-removed" not in launcher
    assert ".bgutil-wheel-removed" not in launcher
