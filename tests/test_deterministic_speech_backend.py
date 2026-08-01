from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from services.speech_backends import (
    BackendAudioSpec,
    BackendCapabilityError,
    BackendGenerationLengthRequest,
    BackendGenerationProfileRequest,
    BackendGenerationRequest,
    BackendSessionConfig,
    DeterministicSpeechBackend,
    backend_ids,
    get_backend,
    register_backend,
    select_production_backend,
    unregister_backend,
)


@pytest.fixture
def deterministic_backend():
    backend = DeterministicSpeechBackend()
    register_backend(backend)
    try:
        yield backend
    finally:
        unregister_backend(backend.backend_id)


def test_registry_accepts_a_distinct_second_backend(deterministic_backend):
    assert backend_ids() == ("deterministic-ci", "voxcpm2")
    assert get_backend("deterministic") is deterministic_backend
    assert get_backend("vox-cpm2").backend_id == "voxcpm2"


def test_deterministic_backend_is_not_selectable_for_production(
    deterministic_backend,
):
    with pytest.raises(BackendCapabilityError):
        select_production_backend(
            deterministic_backend.backend_id,
            default_backend_id="voxcpm2",
        )


def test_deterministic_backend_has_different_audio_and_no_model_knobs(
    deterministic_backend,
    tmp_path: Path,
):
    session = deterministic_backend.open_session(
        BackendSessionConfig(
            model_path=tmp_path,
            options={"sample_rate": 22_050},
        )
    )
    assert session.audio_spec == BackendAudioSpec(
        encode_sample_rate=None,
        output_sample_rate=22_050,
        seconds_per_step=None,
        cache_length=None,
    )
    request = BackendGenerationRequest(
        text="Проверка второго движка.",
        reference_audio=tmp_path / "not-required.wav",
        seed=7,
        duration_budget=0.2,
    )
    first = session.generate(request)
    second = session.generate(request)
    assert first == second
    assert any(abs(value) > 0.001 for value in first)

    length = deterministic_backend.plan_generation_length(
        session.audio_spec,
        BackendGenerationLengthRequest(duration_budget=1.0, attempt=1),
    )
    profile = deterministic_backend.plan_generation_profile(
        BackendGenerationProfileRequest(attempt=1),
    )
    assert length.backend_options == {}
    assert profile.backend_options == {}


def test_deterministic_cli_produces_pcm_without_model_archive(tmp_path: Path):
    segments = tmp_path / "segments.json"
    output = tmp_path / "timeline.wav"
    segments.write_text(
        json.dumps({"segments": [{"id": 1, "text": "Тест"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.speech_backends.deterministic_runtime",
            "--segments-json",
            str(segments),
            "--output",
            str(output),
            "--video-duration",
            "0.5",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert output.is_file()
    with wave.open(str(output), "rb") as handle:
        assert handle.getframerate() == 22_050
        assert handle.getnchannels() == 1
        assert handle.getnframes() > 1_000
