from __future__ import annotations

import pytest

from services.speech_backends import (
    DEFAULT_BACKEND_ID,
    BackendCapabilities,
    BackendCapabilityError,
    UnknownSpeechBackendError,
    normalize_production_backend,
    register_backend,
    select_production_backend,
    unregister_backend,
)


def test_control_plane_uses_default_and_resolves_aliases() -> None:
    default_selection = select_production_backend(
        None,
        default_backend_id=DEFAULT_BACKEND_ID,
    )
    alias_backend_id = normalize_production_backend(
        "OpenBMB",
        default_backend_id=DEFAULT_BACKEND_ID,
    )

    assert default_selection.backend_id == "voxcpm2"
    assert default_selection.capabilities.voice_cloning is True
    assert alias_backend_id == "voxcpm2"
    assert default_selection.as_dict()["control_plane_policy"].startswith(
        "speech-backend-control-plane-"
    )


def test_control_plane_rejects_unknown_backend() -> None:
    with pytest.raises(UnknownSpeechBackendError, match="Неизвестный speech backend"):
        normalize_production_backend(
            "not-registered",
            default_backend_id=DEFAULT_BACKEND_ID,
        )


def test_control_plane_rejects_non_string_false_instead_of_silently_defaulting() -> None:
    with pytest.raises(UnknownSpeechBackendError):
        normalize_production_backend(
            False,
            default_backend_id=DEFAULT_BACKEND_ID,
        )


def test_control_plane_fails_closed_on_missing_capabilities() -> None:
    class IncompleteBackend:
        backend_id = "control-plane-incomplete"
        aliases = ("control-plane-incomplete-alias",)
        adapter_policy = "test-incomplete-v1"

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(
                voice_cloning=False,
                reference_audio=True,
                deterministic_seed=True,
                style_instruction=False,
                cpu_inference=True,
                pcm_output=True,
                checkpointable_segments=True,
            )

    backend = IncompleteBackend()
    register_backend(backend)
    try:
        with pytest.raises(BackendCapabilityError, match="production capabilities"):
            select_production_backend(
                "control-plane-incomplete-alias",
                default_backend_id=DEFAULT_BACKEND_ID,
            )
    finally:
        unregister_backend(backend.backend_id)
