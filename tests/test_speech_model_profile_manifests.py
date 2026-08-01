from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.speech_backends import (
    DEFAULT_MODEL_PROFILE_ID,
    PROFILE_MANIFEST_POLICY,
    ProfileManifestError,
    default_model_profile,
    default_profile_manifest_root,
    load_profile_catalog,
    load_profile_manifest,
    model_profile_catalog_snapshot,
    model_profile_manifest_record,
    model_profile_manifest_records,
    model_profile_source_evidence,
)
from tools.check_tts_model_catalog import validate_catalog


def _manifest(profile_id: str = "voxcpm2-test-profile") -> dict:
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "backend_id": "voxcpm2",
        "display_name": "VoxCPM2 test",
        "model_family": "OpenBMB/VoxCPM2",
        "model_revision": "fixture-v1",
        "aliases": [f"{profile_id}-alias"],
        "production_enabled": True,
        "required_capabilities": [
            "voice_cloning",
            "reference_audio",
            "deterministic_seed",
            "pcm_output",
            "checkpointable_segments",
        ],
        "option_specs": [
            {
                "name": "threads",
                "value_type": "int",
                "default": 10,
                "minimum": 1,
                "maximum": 64,
            },
            {
                "name": "steps",
                "value_type": "int",
                "default": 16,
                "minimum": 1,
                "maximum": 256,
            },
            {
                "name": "cfg",
                "value_type": "float",
                "default": 1.8,
                "minimum": 0.1,
                "maximum": 10.0,
            },
            {
                "name": "cache_length",
                "value_type": "int",
                "default": 4096,
                "minimum": 2048,
                "maximum": 131072,
            },
            {
                "name": "base_seed",
                "value_type": "int",
                "default": 42,
                "minimum": 0,
                "maximum": 2147483647,
            },
        ],
        "backend_defaults": {
            "vox_archive": "C:/models/vox-fixture",
            "cpu_venv": "C:/venvs/vox-fixture",
        },
        "backend_override_keys": ["vox_archive", "cpu_venv"],
        "requires_execution_plan_evidence": True,
    }


def _write(root: Path, payload: dict, *, filename: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    name = filename or f"{payload['profile_id']}.json"
    path = root / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def test_builtin_catalog_is_manifest_backed_and_default_is_canonical() -> None:
    records = model_profile_manifest_records()
    snapshot = model_profile_catalog_snapshot()
    record = model_profile_manifest_record(DEFAULT_MODEL_PROFILE_ID)
    evidence = model_profile_source_evidence(DEFAULT_MODEL_PROFILE_ID)
    profile = default_model_profile()

    assert len(records) == 1
    assert record is records[0]
    assert records[0].profile is profile
    assert records[0].profile.profile_id == DEFAULT_MODEL_PROFILE_ID
    assert records[0].source_path.name == f"{DEFAULT_MODEL_PROFILE_ID}.json"
    assert len(records[0].source_sha256) == 64
    assert snapshot["policy"] == PROFILE_MANIFEST_POLICY
    assert snapshot["profile_count"] == 1
    assert snapshot["profiles"][0]["profile"]["model_revision"] == (
        "local-archive-pinned-v1"
    )
    assert evidence == {
        "schema_version": 1,
        "profile_id": DEFAULT_MODEL_PROFILE_ID,
        "backend_id": profile.backend_id,
        "model_revision": profile.model_revision,
        "source": f"{DEFAULT_MODEL_PROFILE_ID}.json",
        "source_kind": "repository-manifest",
        "source_sha256": records[0].source_sha256,
        "manifest_policy": PROFILE_MANIFEST_POLICY,
    }
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    assert "backend_defaults" not in serialized
    assert "speech_backend_config" not in serialized
    assert "vox_archive" not in serialized
    assert "cpu_venv" not in serialized
    assert str(profile.backend_defaults["vox_archive"]) not in serialized
    assert str(profile.backend_defaults["cpu_venv"]) not in serialized
    assert str(default_profile_manifest_root()) not in str(evidence["source"])


def test_valid_custom_catalog_passes_adapter_conformance(tmp_path: Path) -> None:
    path = _write(tmp_path, _manifest())
    record = load_profile_manifest(path, catalog_root=tmp_path)
    snapshot = validate_catalog(
        tmp_path,
        default_profile="voxcpm2-test-profile-alias",
    )

    assert record.profile.profile_id == "voxcpm2-test-profile"
    assert snapshot["canonical_default"] == "voxcpm2-test-profile"
    assert snapshot["profile_count"] == 1
    assert snapshot["adapter_contracts"]["voxcpm2"]["backend_id"] == "voxcpm2"


def test_manifest_rejects_duplicate_keys_and_non_finite_numbers(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate-profile.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )
    with pytest.raises(ProfileManifestError, match="дублирующийся ключ"):
        load_profile_manifest(duplicate, catalog_root=tmp_path)

    payload = _manifest("non-finite-profile")
    text = json.dumps(payload).replace('"default": 1.8', '"default": NaN', 1)
    non_finite = tmp_path / "non-finite-profile.json"
    non_finite.write_text(text, encoding="utf-8")
    with pytest.raises(ProfileManifestError, match="JSON constant запрещён"):
        load_profile_manifest(non_finite, catalog_root=tmp_path)


def test_manifest_rejects_unknown_fields_and_filename_mismatch(tmp_path: Path) -> None:
    unknown = _manifest("unknown-field-profile")
    unknown["python_factory"] = "dangerous.module:factory"
    path = _write(tmp_path, unknown)
    with pytest.raises(ProfileManifestError, match="неизвестные поля: python_factory"):
        load_profile_manifest(path, catalog_root=tmp_path)

    mismatch = _manifest("canonical-profile")
    path = _write(tmp_path, mismatch, filename="other-name.json")
    with pytest.raises(ProfileManifestError, match="должно совпадать"):
        load_profile_manifest(path, catalog_root=tmp_path)


def test_catalog_rejects_alias_collisions(tmp_path: Path) -> None:
    first = _manifest("first-profile")
    second = _manifest("second-profile")
    first["aliases"] = ["shared-alias"]
    second["aliases"] = ["shared-alias"]
    _write(tmp_path, first)
    _write(tmp_path, second)

    with pytest.raises(ProfileManifestError, match="shared-alias"):
        load_profile_catalog(tmp_path)


def test_manifest_must_be_direct_json_child_with_safe_size(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    path = _write(nested, _manifest("nested-profile"))
    with pytest.raises(ProfileManifestError, match="непосредственно"):
        load_profile_manifest(path, catalog_root=tmp_path)

    wrong_suffix = tmp_path / "wrong-profile.txt"
    wrong_suffix.write_text("{}", encoding="utf-8")
    with pytest.raises(ProfileManifestError, match="расширение .json"):
        load_profile_manifest(wrong_suffix, catalog_root=tmp_path)

    empty = tmp_path / "empty-profile.json"
    empty.write_bytes(b"")
    with pytest.raises(ProfileManifestError, match="Размер"):
        load_profile_manifest(empty, catalog_root=tmp_path)


def test_catalog_cli_rejects_unknown_or_disabled_default(tmp_path: Path) -> None:
    _write(tmp_path, _manifest("enabled-profile"))
    with pytest.raises(ProfileManifestError, match="не разрешён однозначно"):
        validate_catalog(tmp_path, default_profile="missing-profile")

    disabled = _manifest("disabled-profile")
    disabled["production_enabled"] = False
    _write(tmp_path, disabled)
    with pytest.raises(ProfileManifestError, match="отключён"):
        validate_catalog(tmp_path, default_profile="disabled-profile")
