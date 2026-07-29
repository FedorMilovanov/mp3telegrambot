from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (
    voxcpm2_cpu_shorts_production as entrypoint,
)


def _arguments(tmp_path: Path, *, cache_length: str = "4096") -> list[str]:
    return [
        "voxcpm2_cpu_shorts_production.py",
        "--work-dir",
        str(tmp_path / "work"),
        "--archive-root",
        str(tmp_path / "archive"),
        "--cache-length",
        cache_length,
    ]


def _patch_contract(monkeypatch, tmp_path: Path, *, fingerprint: str = "render-current") -> None:
    monkeypatch.setattr(entrypoint.sys, "argv", _arguments(tmp_path))
    monkeypatch.setattr(entrypoint.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(
        entrypoint.clean_runtime_contract,
        "build_fingerprints",
        lambda **_kwargs: {"render_contract_sha256": fingerprint},
    )


def test_stale_manual_marker_clears_checkpoint_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_contract(monkeypatch, tmp_path)
    work = tmp_path / "work"
    for name in ("checkpoints", "segments_clean", "segments_fitted", "attempts"):
        directory = work / name
        directory.mkdir(parents=True)
        (directory / "artifact.bin").write_bytes(b"old")
    (work / "checkpoints" / "segment_01.json").write_text("{}", encoding="utf-8")
    marker = work / "direct_cli_runtime.marker.json"
    marker.write_text(
        json.dumps({"policy": entrypoint.MARKER_POLICY, "render_contract_sha256": "old"}),
        encoding="utf-8",
    )

    marker_path, expected = entrypoint._prepare_runtime_marker()

    assert marker_path == marker
    assert expected["render_contract_sha256"] == "render-current"
    assert not marker.exists()
    assert not (work / "checkpoints").exists()
    assert not (work / "segments_clean").exists()
    assert not (work / "segments_fitted").exists()
    assert not (work / "attempts").exists()


def test_current_manual_marker_keeps_checkpoint_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_contract(monkeypatch, tmp_path)
    work, expected = entrypoint._runtime_contract()
    checkpoint = work / "checkpoints" / "segment_01.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}", encoding="utf-8")
    marker = work / "direct_cli_runtime.marker.json"
    marker.write_text(json.dumps(expected), encoding="utf-8")

    entrypoint._prepare_runtime_marker()

    assert checkpoint.exists()
    assert not marker.exists()


def test_failed_manual_render_does_not_commit_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "direct_cli_runtime.marker.json"
    monkeypatch.setattr(
        entrypoint,
        "_prepare_runtime_marker",
        lambda: (marker, {"policy": entrypoint.MARKER_POLICY}),
    )

    def fail() -> None:
        marker.write_text("partial", encoding="utf-8")
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        entrypoint.run(fail)
    assert not marker.exists()


def test_successful_manual_render_commits_strict_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "direct_cli_runtime.marker.json"
    expected = {
        "schema_version": 1,
        "policy": entrypoint.MARKER_POLICY,
        "render_contract_sha256": "render-current",
        "cache_length": 4096,
        "python_executable": "python.exe",
    }
    monkeypatch.setattr(entrypoint, "_prepare_runtime_marker", lambda: (marker, expected))
    entrypoint.run(lambda: None)
    assert json.loads(marker.read_text(encoding="utf-8")) == expected
    assert "NaN" not in marker.read_text(encoding="utf-8")


@pytest.mark.parametrize("cache_length", ["0", "2047", "131073", "nan"])
def test_manual_cache_length_contract_rejects_invalid_values(
    monkeypatch,
    tmp_path: Path,
    cache_length: str,
) -> None:
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        _arguments(tmp_path, cache_length=cache_length),
    )
    monkeypatch.setattr(
        entrypoint.clean_runtime_contract,
        "build_fingerprints",
        lambda **_kwargs: {"render_contract_sha256": "unused"},
    )
    with pytest.raises(RuntimeError, match="cache-length"):
        entrypoint._runtime_contract()
