from __future__ import annotations

import json

import pytest

from handlers.dub_wizard import (
    _catalog_text,
    _env_json_object,
    _extract_youtube_video_id,
    _parse_selection_value,
    _request_payload,
    _selection_keyboard,
    _selection_state,
)
from services.speech_backends import DEFAULT_MODEL_PROFILE_ID, default_model_profile


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://youtube.com/shorts/tNlIoCeGyLk?si=abc", "tNlIoCeGyLk"),
        ("https://youtu.be/tNlIoCeGyLk", "tNlIoCeGyLk"),
        ("https://www.youtube.com/watch?v=tNlIoCeGyLk", "tNlIoCeGyLk"),
    ],
)
def test_extract_youtube_video_id(url: str, video_id: str) -> None:
    actual, canonical = _extract_youtube_video_id(url)
    assert actual == video_id
    assert canonical.endswith(video_id)


def test_rejects_non_youtube_url() -> None:
    with pytest.raises(ValueError, match="YouTube"):
        _extract_youtube_video_id("https://example.com/video")


def test_request_payload_pins_and_normalizes_selected_profile(monkeypatch) -> None:
    for name in (
        "DUB_TTS_OPTIONS_JSON",
        "DUB_TTS_BACKEND_CONFIG_JSON",
        "DUB_VOX_THREADS",
        "DUB_VOX_STEPS",
        "DUB_VOX_CFG",
        "DUB_VOX_CACHE_LENGTH",
        "DUB_VOX_BASE_SEED",
        "DUB_VOX_ARCHIVE",
        "DUB_CPU_VENV",
    ):
        monkeypatch.delenv(name, raising=False)

    payload = _request_payload(
        "tNlIoCeGyLk",
        "https://youtube.com/watch?v=tNlIoCeGyLk",
        "gemini",
        DEFAULT_MODEL_PROFILE_ID,
    )
    profile = default_model_profile()

    assert payload["schema_version"] == 1
    assert payload["speech_model_profile"] == profile.profile_id
    assert payload["speech_backend"] == profile.backend_id
    assert payload["speech_profile_fingerprint"] == profile.fingerprint()
    assert payload["speech_options"] == {
        spec.name: spec.default for spec in profile.option_specs
    }
    assert payload["speech_backend_config"] == dict(profile.backend_defaults)
    assert payload["translation_mode"] == "gemini"


def test_request_payload_merges_generic_and_legacy_overrides(monkeypatch) -> None:
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", '{"threads":8,"steps":20}')
    monkeypatch.setenv("DUB_TTS_BACKEND_CONFIG_JSON", '{"vox_archive":"D:/vox"}')
    monkeypatch.setenv("DUB_VOX_CFG", "2.1")
    monkeypatch.setenv("DUB_CPU_VENV", "D:/venv")

    payload = _request_payload(
        "tNlIoCeGyLk",
        "https://youtube.com/watch?v=tNlIoCeGyLk",
        "direct",
        DEFAULT_MODEL_PROFILE_ID,
    )

    assert payload["speech_options"]["threads"] == 8
    assert payload["speech_options"]["steps"] == 20
    assert payload["speech_options"]["cfg"] == 2.1
    assert payload["speech_backend_config"] == {
        "vox_archive": "D:/vox",
        "cpu_venv": "D:/venv",
    }


def test_request_payload_rejects_conflicting_override_sources(monkeypatch) -> None:
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", '{"threads":8}')
    monkeypatch.setenv("DUB_VOX_THREADS", "10")

    with pytest.raises(ValueError, match="Конфликт настройки threads"):
        _request_payload(
            "tNlIoCeGyLk",
            "https://youtube.com/watch?v=tNlIoCeGyLk",
            "gemini",
            DEFAULT_MODEL_PROFILE_ID,
        )


def test_env_json_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", '{"threads":8,"threads":9}')
    with pytest.raises(ValueError, match="дублирующийся ключ"):
        _env_json_object("DUB_TTS_OPTIONS_JSON")

    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", "[1,2]")
    with pytest.raises(ValueError, match="JSON-объект"):
        _env_json_object("DUB_TTS_OPTIONS_JSON")

    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", '{"cfg":NaN}')
    with pytest.raises(ValueError, match="JSON constant запрещён"):
        _env_json_object("DUB_TTS_OPTIONS_JSON")


def test_profile_callbacks_are_short_opaque_and_session_bound() -> None:
    state = _selection_state("gemini")
    markup = _selection_keyboard(state, 0)
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and "|profile|" in button.callback_data
    ]

    assert callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
    assert all(DEFAULT_MODEL_PROFILE_ID not in value for value in callbacks)
    assert _parse_selection_value(state, callbacks[0].rsplit("|", 1)[1]) == 0

    stale = dict(state)
    stale["selection_token"] = "deadbeef"
    with pytest.raises(RuntimeError, match="устарела"):
        _parse_selection_value(stale, callbacks[0].rsplit("|", 1)[1])


def test_catalog_exposes_identity_not_backend_paths() -> None:
    text, page, page_count = _catalog_text(0)
    profile = default_model_profile()

    assert page == 0
    assert page_count >= 1
    assert profile.profile_id in text
    assert profile.model_revision in text
    assert profile.fingerprint()[:12] in text
    assert str(profile.backend_defaults["vox_archive"]) not in text
    assert str(profile.backend_defaults["cpu_venv"]) not in text


def test_generic_env_json_stays_json_serializable(monkeypatch) -> None:
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", json.dumps({"threads": 7}))
    assert _env_json_object("DUB_TTS_OPTIONS_JSON") == {"threads": 7}
