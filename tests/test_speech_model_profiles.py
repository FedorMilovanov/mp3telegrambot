from __future__ import annotations

import pytest

from services.speech_backends import (
    DEFAULT_BACKEND_ID,
    DEFAULT_MODEL_PROFILE_ID,
    ModelOptionSpec,
    SpeechModelConfigurationError,
    SpeechModelProfile,
    SpeechModelProfileDisabledError,
    SpeechModelProfileMismatchError,
    UnknownSpeechModelProfileError,
    get_model_profile,
    normalize_production_speech_request,
    register_model_profile,
    select_production_speech,
    unregister_model_profile,
)


def test_default_profile_resolves_typed_options_and_backend_config() -> None:
    normalized = normalize_production_speech_request(
        {
            "speech_backend": "OpenBMB",
            "speech_model_profile": "voxcpm2-default",
            "speech_options": {"threads": 12, "cfg": 1.95},
            "speech_backend_config": {
                "vox_archive": r"C:\models\vox-a",
                "cpu_venv": r"C:\venvs\vox-a",
            },
        },
        default_backend_id=DEFAULT_BACKEND_ID,
        default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
    )

    assert normalized["speech_backend"] == "voxcpm2"
    assert normalized["speech_model_profile"] == DEFAULT_MODEL_PROFILE_ID
    assert normalized["speech_options"] == {
        "threads": 12,
        "steps": 16,
        "cfg": 1.95,
        "cache_length": 4096,
        "base_seed": 2026072800,
    }
    assert normalized["threads"] == 12
    assert normalized["cfg"] == 1.95
    assert normalized["vox_archive"] == r"C:\models\vox-a"
    assert normalized["cpu_venv"] == r"C:\venvs\vox-a"
    assert len(normalized["speech_profile_fingerprint"]) == 64


def test_profile_rejects_unknown_option_and_conflicting_flat_value() -> None:
    with pytest.raises(SpeechModelConfigurationError, match="не поддерживает speech_options"):
        normalize_production_speech_request(
            {"speech_options": {"temperature": 0.7}},
            default_backend_id=DEFAULT_BACKEND_ID,
            default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
        )

    with pytest.raises(SpeechModelConfigurationError, match="Конфликт"):
        normalize_production_speech_request(
            {"threads": 8, "speech_options": {"threads": 12}},
            default_backend_id=DEFAULT_BACKEND_ID,
            default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
        )


def test_profile_rejects_stale_fingerprint() -> None:
    with pytest.raises(SpeechModelConfigurationError, match="fingerprint"):
        normalize_production_speech_request(
            {"speech_profile_fingerprint": "0" * 64},
            default_backend_id=DEFAULT_BACKEND_ID,
            default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
        )


def test_unknown_profile_is_distinct_from_unknown_backend() -> None:
    with pytest.raises(UnknownSpeechModelProfileError, match="Неизвестный TTS model profile"):
        select_production_speech(
            None,
            "future-voice-model",
            request={},
            default_backend_id=DEFAULT_BACKEND_ID,
            default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
        )


def test_profile_can_pin_an_alternative_revision_without_new_adapter() -> None:
    profile = SpeechModelProfile(
        profile_id="voxcpm2-canary-v2",
        backend_id="voxcpm2",
        display_name="VoxCPM2 canary",
        model_family="OpenBMB/VoxCPM2",
        model_revision="canary-checkpoint-2026-08",
        aliases=("canary-tts",),
        option_specs=(
            ModelOptionSpec("threads", "int", 6, minimum=1, maximum=64),
            ModelOptionSpec("steps", "int", 24, minimum=1, maximum=256),
            ModelOptionSpec("cfg", "float", 1.9, minimum=0.1, maximum=10.0),
            ModelOptionSpec("cache_length", "int", 8192, minimum=2048, maximum=131072),
            ModelOptionSpec("base_seed", "int", 42, minimum=0, maximum=2147483647),
        ),
        backend_defaults={
            "vox_archive": r"C:\models\vox-canary",
            "cpu_venv": r"C:\venvs\vox-canary",
        },
        backend_override_keys=("vox_archive", "cpu_venv"),
        requires_execution_plan_evidence=True,
    )
    register_model_profile(profile)
    try:
        selection = select_production_speech(
            None,
            "canary-tts",
            request={},
            default_backend_id=DEFAULT_BACKEND_ID,
            default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
        )
        assert selection.backend_id == "voxcpm2"
        assert selection.model_profile.model_revision == "canary-checkpoint-2026-08"
        assert selection.resolution.options["steps"] == 24
        assert selection.resolution.backend_config["vox_archive"].endswith("vox-canary")
        assert get_model_profile("canary-tts") is profile
    finally:
        unregister_model_profile(profile.profile_id)


def test_disabled_and_mismatched_profiles_fail_closed() -> None:
    disabled = SpeechModelProfile(
        profile_id="voxcpm2-disabled-test",
        backend_id="voxcpm2",
        display_name="Disabled test model",
        model_family="OpenBMB/VoxCPM2",
        model_revision="disabled",
        production_enabled=False,
    )
    mismatch = SpeechModelProfile(
        profile_id="voxcpm2-mismatch-test",
        backend_id="voxcpm2",
        display_name="Mismatch test model",
        model_family="OpenBMB/VoxCPM2",
        model_revision="mismatch",
        required_capabilities=(),
    )
    register_model_profile(disabled)
    register_model_profile(mismatch)
    try:
        with pytest.raises(SpeechModelProfileDisabledError):
            select_production_speech(
                None,
                disabled.profile_id,
                request={},
                default_backend_id=DEFAULT_BACKEND_ID,
                default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
            )
        with pytest.raises(SpeechModelProfileMismatchError):
            select_production_speech(
                "deterministic-ci",
                mismatch.profile_id,
                request={},
                default_backend_id=DEFAULT_BACKEND_ID,
                default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
                required_capabilities=(),
            )
    finally:
        unregister_model_profile(disabled.profile_id)
        unregister_model_profile(mismatch.profile_id)
