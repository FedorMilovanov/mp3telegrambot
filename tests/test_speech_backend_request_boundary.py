from __future__ import annotations

from pathlib import Path

import pytest

from services.speech_backends import BackendCapabilities, register_backend, unregister_backend
from tools.voxcpm2 import generic_project_runtime


def _request(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "direct",
    }
    payload.update(updates)
    return payload


def test_control_plane_normalizes_backend_without_loading_synthesis_core() -> None:
    result = generic_project_runtime.validate_request_payload(_request())

    assert result["speech_backend"] == "voxcpm2"
    # The wizard/request barrier must remain independent from numpy, torch and
    # the model runtime. Heavy synthesis is loaded only by the selected engine.
    source = Path(generic_project_runtime.__file__).read_text(encoding="utf-8")
    assert "from tools.voxcpm2 import clean_production_core" not in source


def test_control_plane_resolves_backend_alias() -> None:
    result = generic_project_runtime.validate_request_payload(
        _request(speech_backend="OpenBMB")
    )

    assert result["speech_backend"] == "voxcpm2"


def test_control_plane_rejects_unknown_backend_before_queueing() -> None:
    with pytest.raises(RuntimeError, match="Некорректный speech_backend"):
        generic_project_runtime.validate_request_payload(
            _request(speech_backend="future-neural-engine")
        )


def test_control_plane_rejects_backend_without_production_capabilities() -> None:
    class IncompleteBackend:
        backend_id = "incomplete-engine"
        aliases = ("incomplete",)
        adapter_policy = "incomplete-v1"

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

    register_backend(IncompleteBackend())
    try:
        with pytest.raises(RuntimeError, match="production capabilities"):
            generic_project_runtime.validate_request_payload(
                _request(speech_backend="incomplete")
            )
    finally:
        unregister_backend("incomplete")
