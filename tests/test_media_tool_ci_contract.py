from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
import yaml

from tools import verify_media_tools


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _load_ci() -> dict[str, Any]:
    payload = yaml.load(CI.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def _step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


def test_required_ci_provisions_and_identifies_approved_media_tools() -> None:
    jobs = _load_ci()["jobs"]

    linux_steps = jobs["test"]["steps"]
    linux_install = _step(linux_steps, "Install approved FFmpeg/FFprobe")
    assert "ffmpeg=7:6.1.1-3ubuntu5" in linux_install["run"]
    linux_verify = _step(linux_steps, "Verify media tool provenance")
    assert "--expected-version 6.1.1" in linux_verify["run"]
    linux_tests = _step(linux_steps, "Run tests")
    assert linux_tests["env"]["MP3_REQUIRE_MEDIA_INTEGRATION"] == "1"
    assert linux_steps.index(linux_verify) < linux_steps.index(linux_tests)

    windows_steps = jobs["windows-runtime"]["steps"]
    windows_install = _step(windows_steps, "Install approved FFmpeg/FFprobe")
    assert "ffmpeg --version=9.0.2" in windows_install["run"]
    windows_verify = _step(windows_steps, "Verify media tool provenance")
    assert "--expected-version 9.0.2" in windows_verify["run"]
    windows_tests = _step(windows_steps, "Run Windows-sensitive regression tests")
    assert windows_tests["env"]["MP3_REQUIRE_MEDIA_INTEGRATION"] == "1"
    assert windows_steps.index(windows_verify) < windows_steps.index(windows_tests)


def test_media_tool_inspection_rejects_mismatched_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_media_tools, "_resolve", lambda name: f"/approved/{name}")
    monkeypatch.setattr(
        verify_media_tools,
        "_query_version",
        lambda executable: f"6.1.1-3ubuntu5-{Path(executable).name}",
    )
    monkeypatch.setattr(verify_media_tools, "_sha256", lambda path: "a" * 64 if path.endswith("ffmpeg") else "b" * 64)

    with pytest.raises(verify_media_tools.MediaToolError, match="version mismatch"):
        verify_media_tools.inspect_media_tools("6.1.1")


def test_media_tool_inspection_accepts_approved_package_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_media_tools, "_resolve", lambda name: f"/approved/{name}")
    monkeypatch.setattr(verify_media_tools, "_query_version", lambda executable: "6.1.1-3ubuntu5")
    monkeypatch.setattr(verify_media_tools, "_sha256", lambda path: "c" * 64)

    identities = verify_media_tools.inspect_media_tools("6.1.1")

    assert [identity.name for identity in identities] == ["ffmpeg", "ffprobe"]
    assert {identity.sha256 for identity in identities} == {"c" * 64}
    assert {identity.version for identity in identities} == {"6.1.1-3ubuntu5"}


def test_media_tool_main_prints_version_and_binary_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verify_media_tools, "_resolve", lambda name: f"/approved/{name}")
    monkeypatch.setattr(verify_media_tools, "_query_version", lambda executable: "9.0.2-essentials")
    monkeypatch.setattr(verify_media_tools, "_sha256", lambda path: "d" * 64)

    assert verify_media_tools.main(["--expected-version", "9.0.2"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"ffmpeg: path=/approved/ffmpeg version=9.0.2-essentials sha256={'d' * 64}",
        f"ffprobe: path=/approved/ffprobe version=9.0.2-essentials sha256={'d' * 64}",
    ]


def test_media_tool_inspection_rejects_unapproved_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_media_tools, "_resolve", lambda name: f"/approved/{name}")
    monkeypatch.setattr(verify_media_tools, "_query_version", lambda executable: "7.0.0")

    with pytest.raises(verify_media_tools.MediaToolError, match="not approved"):
        verify_media_tools.inspect_media_tools("6.1.1")


def test_authoritative_media_mode_fails_instead_of_skipping_when_tools_are_absent(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(empty_path)
    env["MP3_REQUIRE_MEDIA_INTEGRATION"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_livedub_audio_media_integration.py",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode != 0, output
    assert "authoritative media lane requires both FFmpeg and FFprobe" in output
    assert "FAILED tests/test_livedub_audio_media_integration.py" in output
    assert "skipped" not in output
