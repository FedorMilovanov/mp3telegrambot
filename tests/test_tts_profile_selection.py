from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.speech_backends import DEFAULT_MODEL_PROFILE_ID, default_model_profile
from services.tts_profile_selection import (
    TTS_PROFILE_SELECTION_POLICY,
    normalize_new_production_tts_request,
    production_tts_profile_choice,
    production_tts_profile_choices,
    read_durable_request,
    rebind_production_tts_profile,
    write_durable_request,
)


def test_production_choices_are_validated_default_first_and_safe() -> None:
    choices = production_tts_profile_choices()
    default = choices[0]
    profile = default_model_profile()

    assert default.profile_id == DEFAULT_MODEL_PROFILE_ID
    assert default.is_default is True
    assert default.backend_id == profile.backend_id
    assert default.model_revision == profile.model_revision
    assert default.fingerprint == profile.fingerprint()
    assert default.source_kind == "repository-manifest"
    assert len(default.source_sha256) == 64
    payload = default.as_dict()
    assert payload["selection_policy"] == TTS_PROFILE_SELECTION_POLICY
    assert "backend_defaults" not in payload
    assert "speech_backend_config" not in payload
    assert str(profile.backend_defaults["vox_archive"]) not in json.dumps(payload)


def test_choice_resolution_is_canonical() -> None:
    choice = production_tts_profile_choice("default-tts")
    assert choice.profile_id == DEFAULT_MODEL_PROFILE_ID
    assert choice.is_default is True


def test_new_request_is_pinned_and_rebind_removes_stale_tts_fields() -> None:
    profile = default_model_profile()
    payload = normalize_new_production_tts_request(
        {
            "schema_version": 1,
            "video_id": "abcdefghijk",
            "translation_mode": "direct",
            "speech_options": {"threads": 8},
            "speech_backend_config": {"vox_archive": "D:/vox"},
        },
        profile.profile_id,
    )

    assert payload["speech_backend"] == profile.backend_id
    assert payload["speech_model_profile"] == profile.profile_id
    assert payload["speech_profile_fingerprint"] == profile.fingerprint()
    assert payload["speech_options"]["threads"] == 8
    assert payload["speech_backend_config"]["vox_archive"] == "D:/vox"

    rebound = rebind_production_tts_profile(
        {
            **payload,
            "speech_profile_fingerprint": "stale",
            "threads": 64,
            "cfg": 9.0,
            "vox_archive": "X:/stale",
        },
        profile.profile_id,
    )
    assert rebound["speech_profile_fingerprint"] == profile.fingerprint()
    assert rebound["speech_options"] == {
        spec.name: spec.default for spec in profile.option_specs
    }
    assert rebound["speech_backend_config"] == dict(profile.backend_defaults)
    assert rebound["threads"] == next(
        spec.default for spec in profile.option_specs if spec.name == "threads"
    )
    assert rebound["vox_archive"] == profile.backend_defaults["vox_archive"]


def test_durable_request_roundtrip_is_atomic_and_strict(tmp_path: Path) -> None:
    path = tmp_path / "project" / "request.json"
    payload = normalize_new_production_tts_request(
        {
            "schema_version": 1,
            "video_id": "abcdefghijk",
            "translation_mode": "gemini",
        },
        DEFAULT_MODEL_PROFILE_ID,
    )

    write_durable_request(path, payload)
    assert read_durable_request(path) == payload
    assert not list(path.parent.glob(".request.json.*.tmp"))
    assert path.read_bytes().endswith(b"\n")


def test_durable_request_reader_rejects_duplicate_keys_and_non_objects(
    tmp_path: Path,
) -> None:
    path = tmp_path / "request.json"
    path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="дублирующийся ключ"):
        read_durable_request(path)

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON-объект"):
        read_durable_request(path)

    path.write_text('{"cfg":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON constant запрещён"):
        read_durable_request(path)


def test_durable_request_writer_rejects_non_finite_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_durable_request(tmp_path / "request.json", {"cfg": float("nan")})
