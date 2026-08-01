from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from services.speech_backends import BackendAudioSpec, BackendGenerationRequest
from services.speech_backends.voxcpm2 import (
    GENERATION_CALL_POLICY,
    VoxCPM2Session,
)
from tools.voxcpm2 import direct_max_quality_cli


ROOT = Path(__file__).resolve().parents[1]


class _FakeModel:
    def __init__(self) -> None:
        self.kwargs = None

    def generate(
        self,
        text,
        reference_wav_path,
        cfg_value,
        inference_timesteps,
        min_len,
        max_len,
        normalize,
        denoise,
        seed=None,
    ):
        self.kwargs = locals().copy()
        return "wav"


def _session() -> VoxCPM2Session:
    return VoxCPM2Session(
        _FakeModel(),
        BackendAudioSpec(16000, 48000, 0.08, 4096),
    )


def test_voxcpm2_session_accepts_only_typed_generation_request() -> None:
    session = _session()
    request = BackendGenerationRequest(
        text="Проверка 2026.",
        reference_audio=Path("reference.wav"),
        seed=7,
        backend_options={"cfg": 1.8, "steps": 16, "min_len": 2, "max_len": 40},
    )

    assert GENERATION_CALL_POLICY == "typed-backend-generation-request-v1"
    assert tuple(inspect.signature(session.generate).parameters) == ("request",)
    assert session.generate(request) == "wav"

    with pytest.raises(TypeError):
        session.generate(  # type: ignore[call-arg]
            text="legacy",
            reference=Path("reference.wav"),
            seed=7,
        )


def test_production_candidate_hook_builds_neutral_request() -> None:
    captured: list[BackendGenerationRequest] = []

    class CapturingSession:
        supports_continuation_context = False

        def generate(self, request: BackendGenerationRequest):
            captured.append(request)
            return "wav"

    result = direct_max_quality_cli._backend_generate(
        CapturingSession(),
        text="Текст.",
        reference=Path("reference.wav"),
        cfg=1.8,
        steps=16,
        min_len=2,
        max_len=40,
        seed=11,
    )

    assert result == "wav"
    assert len(captured) == 1
    assert isinstance(captured[0], BackendGenerationRequest)
    assert captured[0].text
    assert captured[0].reference_audio.name == "reference.wav"


def test_adapter_source_has_no_legacy_generation_translation() -> None:
    source = (ROOT / "services" / "speech_backends" / "voxcpm2.py").read_text(
        encoding="utf-8"
    )

    assert "_legacy_request" not in source
    assert "legacy_kwargs" not in source
    assert "request: BackendGenerationRequest | None" not in source
