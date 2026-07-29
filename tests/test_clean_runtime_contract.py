from __future__ import annotations

from pathlib import Path

import pytest

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_runtime_contract as contract


def test_runtime_settings_reject_nonfinite_and_absurd_values() -> None:
    request = {"video_id": "project", "cfg": float("nan")}
    with pytest.raises(RuntimeError, match="cfg должен быть конечным"):
        contract.normalize_settings(request, duration=30.0)
    with pytest.raises(RuntimeError, match="video_duration"):
        contract.normalize_settings({"video_id": "project"}, duration=float("inf"))
    with pytest.raises(RuntimeError, match="threads"):
        contract.normalize_settings(
            {"video_id": "project", "threads": contract.MAX_THREADS + 1},
            duration=30.0,
        )
    with pytest.raises(RuntimeError, match="steps"):
        contract.normalize_settings(
            {"video_id": "project", "steps": contract.MAX_STEPS + 1},
            duration=30.0,
        )
    with pytest.raises(RuntimeError, match="original_level"):
        contract.normalize_settings(
            {"video_id": "project", "original_level": 1.01},
            duration=30.0,
        )
    with pytest.raises(RuntimeError, match="base_seed"):
        contract.normalize_settings(
            {"video_id": "project", "base_seed": contract.MAX_BASE_SEED + 1},
            duration=30.0,
        )


def test_valid_runtime_settings_are_canonical() -> None:
    result = contract.normalize_settings(
        {
            "video_id": "abc",
            "threads": "10",
            "steps": "16",
            "cfg": "1.8",
            "base_seed": "42",
            "original_level": "0.18",
        },
        duration="59.5",
    )
    assert result == {
        "video_id": "abc",
        "duration": 59.5,
        "threads": 10,
        "steps": 16,
        "cfg": 1.8,
        "base_seed": 42,
        "original_level": 0.18,
    }


def test_render_and_release_fingerprints_change_independently(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "render.py").write_text("render-v1", encoding="utf-8")
    (repo / "release.py").write_text("release-v1", encoding="utf-8")
    cpu = tmp_path / "python.exe"
    cpu.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(contract, "_RENDER_MODULES", ("render.py",))
    monkeypatch.setattr(contract, "_RELEASE_MODULES", ("release.py",))
    monkeypatch.setattr(
        contract,
        "_model_manifest",
        lambda _archive: {"path": "model", "artifacts": [{"name": "x", "size": 1}]},
    )
    monkeypatch.setattr(
        contract,
        "_voxcpm_runtime",
        lambda _python: {"version": "2.0", "module": "voxcpm.py", "size": 10, "mtime_ns": 1},
    )

    first = contract.build_fingerprints(repo=repo, archive=tmp_path, cpu_python=cpu)
    (repo / "render.py").write_text("render-v2", encoding="utf-8")
    second = contract.build_fingerprints(repo=repo, archive=tmp_path, cpu_python=cpu)
    assert first["render_contract_sha256"] != second["render_contract_sha256"]
    assert first["release_contract_sha256"] == second["release_contract_sha256"]

    (repo / "release.py").write_text("release-v2", encoding="utf-8")
    third = contract.build_fingerprints(repo=repo, archive=tmp_path, cpu_python=cpu)
    assert second["render_contract_sha256"] == third["render_contract_sha256"]
    assert second["release_contract_sha256"] != third["release_contract_sha256"]


def test_model_manifest_tracks_weight_metadata(monkeypatch, tmp_path: Path) -> None:
    model = tmp_path / "snapshot"
    model.mkdir()
    (model / "config.json").write_text('{"model":"v2"}', encoding="utf-8")
    weights = model / "model.safetensors"
    weights.write_bytes(b"weights-v1")
    monkeypatch.setattr(contract, "discover_model", lambda _archive: model)
    first = contract._model_manifest(tmp_path)
    weights.write_bytes(b"weights-v2-longer")
    second = contract._model_manifest(tmp_path)
    assert first != second
    assert first["artifacts"][0]["name"] == "config.json"


def test_clean_core_requires_current_marker_fingerprints() -> None:
    source = Path(clean.__file__).read_text(encoding="utf-8")
    assert clean.POLICY == "clean-direct-production-v2"
    assert "render_contract_sha256" in source
    assert "release_contract_sha256" in source
    assert "checkpoints не соответствуют renderer/model/runtime fingerprint" in source
    assert "clean_runtime_contract.build_fingerprints(" in source
    assert '"schema_version": 3' in source
    assert '"release_complete": False' in source
    assert "release_complete=True" in source
