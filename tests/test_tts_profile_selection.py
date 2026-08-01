from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.dub_studio import DubStore
from services.speech_backends import (
    DEFAULT_MODEL_PROFILE_ID,
    SpeechModelProfile,
    default_model_profile,
    register_model_profile,
    unregister_model_profile,
)
from services.tts_profile_selection import (
    TTS_PROFILE_SELECTION_POLICY,
    TTS_PROJECT_REBIND_POLICY,
    normalize_new_production_tts_request,
    production_tts_profile_choice,
    production_tts_profile_choices,
    read_durable_request,
    rebind_inactive_project_tts_profile,
    rebind_production_tts_profile,
    write_durable_request,
)


@pytest.fixture
def alternate_profile() -> SpeechModelProfile:
    base = default_model_profile()
    profile = SpeechModelProfile(
        profile_id="voxcpm2-rebind-test-v2",
        backend_id=base.backend_id,
        display_name="VoxCPM2 rebind test",
        model_family=base.model_family,
        model_revision="fixture-rebind-v2",
        production_enabled=True,
        required_capabilities=base.required_capabilities,
        option_specs=base.option_specs,
        backend_defaults=base.backend_defaults,
        backend_override_keys=base.backend_override_keys,
        requires_execution_plan_evidence=base.requires_execution_plan_evidence,
    )
    register_model_profile(profile)
    try:
        yield profile
    finally:
        unregister_model_profile(profile.profile_id)


def _project_with_request(tmp_path: Path) -> tuple[DubStore, dict, Path, dict]:
    store = DubStore(tmp_path / "studio")
    project = store.create_project(
        "generic_short_v1",
        owner_user_id=123,
        owner_chat_id=456,
        metadata={
            "video_id": "abcdefghijk",
            "translation_mode": "direct",
            "speech_model_profile": DEFAULT_MODEL_PROFILE_ID,
        },
    )
    request = normalize_new_production_tts_request(
        {
            "schema_version": 1,
            "video_id": "abcdefghijk",
            "source_url": "https://youtube.com/watch?v=abcdefghijk",
            "translation_mode": "direct",
        },
        DEFAULT_MODEL_PROFILE_ID,
    )
    path = store.root / "projects" / str(project["id"]) / "request.json"
    write_durable_request(path, request)
    return store, project, path, request


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


def test_inactive_project_rebind_updates_request_metadata_and_event(
    tmp_path: Path,
    alternate_profile: SpeechModelProfile,
) -> None:
    store, project, path, previous = _project_with_request(tmp_path)

    result = rebind_inactive_project_tts_profile(
        store,
        str(project["id"]),
        owner_user_id=123,
        request_path=path,
        profile_value=alternate_profile.profile_id,
    )
    saved = read_durable_request(path)
    updated = store.get_project(str(project["id"]))

    assert result.changed is True
    assert result.previous_profile_id == DEFAULT_MODEL_PROFILE_ID
    assert result.choice.profile_id == alternate_profile.profile_id
    assert result.as_dict()["rebind_policy"] == TTS_PROJECT_REBIND_POLICY
    assert saved["speech_model_profile"] == alternate_profile.profile_id
    assert saved["speech_profile_fingerprint"] == alternate_profile.fingerprint()
    assert saved["speech_options"] == {
        spec.name: spec.default for spec in alternate_profile.option_specs
    }
    assert saved != previous
    assert updated["metadata"]["speech_model_profile"] == alternate_profile.profile_id
    assert updated["metadata"]["speech_model_revision"] == (
        alternate_profile.model_revision
    )
    assert updated["stage"] == "tts_profile_rebound"
    with store.connect() as conn:
        event = conn.execute(
            """
            SELECT event_type, payload_json FROM dub_events
            WHERE project_id=? ORDER BY id DESC LIMIT 1
            """,
            (str(project["id"]),),
        ).fetchone()
    assert event["event_type"] == "tts_profile_rebound"
    assert json.loads(event["payload_json"])["policy"] == TTS_PROJECT_REBIND_POLICY


def test_same_current_profile_is_a_noop(tmp_path: Path) -> None:
    store, project, path, previous = _project_with_request(tmp_path)

    result = rebind_inactive_project_tts_profile(
        store,
        str(project["id"]),
        owner_user_id=123,
        request_path=path,
        profile_value=DEFAULT_MODEL_PROFILE_ID,
    )

    assert result.changed is False
    assert read_durable_request(path) == previous
    assert store.get_project(str(project["id"]))["stage"] == "created"


def test_project_rebind_rejects_wrong_owner_and_active_project(
    tmp_path: Path,
    alternate_profile: SpeechModelProfile,
) -> None:
    store, project, path, _previous = _project_with_request(tmp_path)

    with pytest.raises(PermissionError, match="не ваш"):
        rebind_inactive_project_tts_profile(
            store,
            str(project["id"]),
            owner_user_id=999,
            request_path=path,
            profile_value=alternate_profile.profile_id,
        )

    store.enqueue_job(str(project["id"]), "render_direct")
    with pytest.raises(RuntimeError, match="draft/failed/cancelled"):
        rebind_inactive_project_tts_profile(
            store,
            str(project["id"]),
            owner_user_id=123,
            request_path=path,
            profile_value=alternate_profile.profile_id,
        )


def test_project_rebind_restores_request_when_db_event_fails(
    monkeypatch,
    tmp_path: Path,
    alternate_profile: SpeechModelProfile,
) -> None:
    store, project, path, previous = _project_with_request(tmp_path)

    def fail_event(*_args, **_kwargs) -> None:
        raise RuntimeError("EVENT_SENTINEL")

    monkeypatch.setattr(store, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="EVENT_SENTINEL"):
        rebind_inactive_project_tts_profile(
            store,
            str(project["id"]),
            owner_user_id=123,
            request_path=path,
            profile_value=alternate_profile.profile_id,
        )

    assert read_durable_request(path) == previous
    unchanged = store.get_project(str(project["id"]))
    assert unchanged["metadata"]["speech_model_profile"] == DEFAULT_MODEL_PROFILE_ID
    assert unchanged["stage"] == "created"
