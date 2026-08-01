from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import sys

import pytest

from services.speech_backends import BackendAudioSpec, BackendGenerationRequest
from services.speech_backends.voxcpm2 import (
    GENERATION_CALL_POLICY,
    VoxCPM2Session,
)
from tools.voxcpm2 import direct_max_quality_cli


ROOT = Path(__file__).resolve().parents[1]
RAW_CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"


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


def _generation_kwargs() -> dict[str, object]:
    return {
        "text": "Текст.",
        "reference": Path("reference.wav"),
        "cfg": 1.8,
        "steps": 16,
        "duration_budget": 4.0,
        "backend_options": {"min_len": 2, "max_len": 40},
        "seed": 11,
    }


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
        **_generation_kwargs(),
    )

    assert result == "wav"
    assert len(captured) == 1
    assert isinstance(captured[0], BackendGenerationRequest)
    assert captured[0].text
    assert captured[0].reference_audio.name == "reference.wav"
    assert captured[0].duration_budget == 4.0
    assert captured[0].backend_options["max_len"] == 40
    assert direct_max_quality_cli.GENERATION_REQUEST_FACTORY_POLICY == (
        "typed-generation-request-factory-v2"
    )


def test_raw_cli_generation_boundary_does_not_depend_on_package_override() -> None:
    spec = importlib.util.spec_from_file_location(
        "tests._standalone_direct_max_quality_cli",
        RAW_CLI,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        captured: list[BackendGenerationRequest] = []

        class CapturingSession:
            def generate(self, request: BackendGenerationRequest):
                captured.append(request)
                return "raw-wav"

        result = module._backend_generate(
            CapturingSession(),
            **_generation_kwargs(),
        )
    finally:
        sys.modules.pop(spec.name, None)

    assert result == "raw-wav"
    assert len(captured) == 1
    assert isinstance(captured[0], BackendGenerationRequest)
    assert captured[0].backend_options["steps"] == 16
    assert captured[0].backend_options["min_len"] == 2


def test_package_overrides_request_factory_not_backend_execution() -> None:
    facade_source = (
        ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli" / "__init__.py"
    ).read_text(encoding="utf-8")
    raw_source = RAW_CLI.read_text(encoding="utf-8")

    assert "_legacy._build_generation_request = _build_generation_request" in facade_source
    assert "_legacy._backend_generate =" not in facade_source
    assert "base_request = _legacy_build_generation_request(session, **kwargs)" in facade_source
    assert "request = _build_generation_request(session, **kwargs)" in raw_source
    assert "return session.generate(request)" in raw_source


def test_adapter_source_has_no_legacy_generation_translation() -> None:
    source = (ROOT / "services" / "speech_backends" / "voxcpm2.py").read_text(
        encoding="utf-8"
    )

    assert "_legacy_request" not in source
    assert "legacy_kwargs" not in source
    assert "request: BackendGenerationRequest | None" not in source
