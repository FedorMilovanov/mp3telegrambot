from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import ensure_bgutil_provider as setup


def _write_package_tree(server: Path) -> None:
    deps = {
        "axios": "1.19.0",
        "bgutils-js": "4.0.3",
        "canvas": "3.2.3",
        "commander": "15.0.0",
        "express": "5.2.1",
        "jsdom": "29.1.1",
        "proxy-agent": "8.0.2",
        "youtubei.js": "18.0.0",
    }
    package = {
        "name": "bgutil-ytdlp-pot-provider",
        "version": setup.BGUTIL_VERSION,
        "dependencies": {name: f"^{version}" for name, version in deps.items()},
    }
    packages: dict[str, object] = {
        "": {
            "name": "bgutil-ytdlp-pot-provider",
            "version": setup.BGUTIL_VERSION,
            "dependencies": package["dependencies"],
        }
    }
    installed_packages: dict[str, object] = {}
    node_modules = server / "node_modules"
    node_modules.mkdir(parents=True)
    for name, version in deps.items():
        key = f"node_modules/{name}"
        packages[key] = {"version": version}
        installed_packages[key] = {"version": version}
        target = node_modules.joinpath(*name.split("/"))
        target.mkdir(parents=True)
        (target / "package.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8"
        )

    (server / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (server / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages}), encoding="utf-8"
    )
    (node_modules / ".package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": installed_packages}),
        encoding="utf-8",
    )


def test_offline_integrity_uses_node_syntax_check_not_provider_version_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    script = server / "build" / "generate_once.js"
    script.parent.mkdir(parents=True)
    script.write_text("export const ok = true;\n", encoding="utf-8")
    _write_package_tree(server)

    captured: dict[str, object] = {}

    def fake_owned(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup, "_owned_run", fake_owned)

    assert (
        setup._require_script_version("node", script=script, cwd=server)
        == setup.BGUTIL_VERSION
    )
    assert captured["command"] == ["node", "--check", str(script)]
    assert "--version" not in captured["command"]
    assert captured["timeout"] == setup._SCRIPT_PROBE_TIMEOUT_SEC


def test_offline_integrity_rejects_missing_installed_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    script = server / "build" / "generate_once.js"
    script.parent.mkdir(parents=True)
    script.write_text("export const ok = true;\n", encoding="utf-8")
    _write_package_tree(server)
    (server / "node_modules" / "jsdom" / "package.json").unlink()
    (server / "node_modules" / "jsdom").rmdir()

    monkeypatch.setattr(
        setup,
        "_owned_run",
        lambda command, **_kwargs: SimpleNamespace(
            args=command, stdout="", stderr="", returncode=0
        ),
    )

    with pytest.raises(setup.ProvisionError, match="missing installed package"):
        setup._require_script_version("node", script=script, cwd=server)


def test_offline_integrity_rejects_dependency_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    script = server / "build" / "generate_once.js"
    script.parent.mkdir(parents=True)
    script.write_text("export const ok = true;\n", encoding="utf-8")
    _write_package_tree(server)
    axios_package = server / "node_modules" / "axios" / "package.json"
    axios_package.write_text(
        json.dumps({"name": "axios", "version": "0.0.0"}), encoding="utf-8"
    )

    monkeypatch.setattr(
        setup,
        "_owned_run",
        lambda command, **_kwargs: SimpleNamespace(
            args=command, stdout="", stderr="", returncode=0
        ),
    )

    with pytest.raises(setup.ProvisionError, match="dependency version drift"):
        setup._require_script_version("node", script=script, cwd=server)


def test_runtime_files_require_npm_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    server = provider / "server"
    script = server / "build" / "generate_once.js"
    plugin = provider / "plugin" / "yt_dlp_plugins" / "extractor" / "getpot_bgutil.py"
    marker = provider / ".mp3bot-bgutil-version"
    script.parent.mkdir(parents=True)
    plugin.parent.mkdir(parents=True)
    (server / "node_modules").mkdir(parents=True)
    script.write_text("// built\n", encoding="utf-8")
    plugin.write_text("__version__='1.3.1'\n", encoding="utf-8")
    marker.write_text(
        f"{setup.BGUTIL_VERSION}@{setup.BGUTIL_COMMIT}\n", encoding="utf-8"
    )

    monkeypatch.setattr(setup, "SERVER_ROOT", server)
    monkeypatch.setattr(setup, "GENERATE_SCRIPT", script)
    monkeypatch.setattr(setup, "PLUGIN_ENTRY", plugin)
    monkeypatch.setattr(setup, "NODE_MODULES", server / "node_modules")
    monkeypatch.setattr(setup, "VERSION_MARKER", marker)

    assert setup._runtime_files_current() is False


def test_source_does_not_execute_generate_once_for_version() -> None:
    source = Path("tools/ensure_bgutil_provider.py").read_text(encoding="utf-8")
    assert '[node, str(script), "--version"]' not in source
    assert '[node, "--check", str(script)]' in source
    assert "_require_dependency_metadata" in source
