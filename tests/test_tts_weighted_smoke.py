from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
import pytest
import soundfile as sf

from services.speech_backends import (
    BackendAudioSpec,
    BackendGenerationExecutionPlan,
    BackendGenerationLengthPlan,
    BackendGenerationProfilePlan,
    BackendIdentity,
    BackendProcessEnvironment,
    ModelOptionSpec,
    SpeechModelProfile,
    SpeechModelResolution,
)
from services.speech_backends.execution_plan import append_execution_plan_from_environment
from services.tts_weighted_smoke import (
    TTS_WEIGHTED_SMOKE_POLICY,
    WeightedTTSSmokeConfig,
    WeightedTTSSmokeRuntime,
    normalize_generated_pcm,
    run_weighted_tts_smoke,
)


class _FakeSession:
    supports_continuation_context = False

    def __init__(self, *, write_evidence: bool = True, invalid: str = "") -> None:
        self.audio_spec = BackendAudioSpec(
            encode_sample_rate=16_000,
            output_sample_rate=24_000,
            seconds_per_step=0.08,
            cache_length=4096,
        )
        self.write_evidence = write_evidence
        self.invalid = invalid
        self.requests: list[Any] = []

    def generate(self, request: Any) -> Any:
        self.requests.append(request)
        if self.write_evidence:
            append_execution_plan_from_environment(
                BackendGenerationExecutionPlan(
                    backend_id="fake-weighted",
                    adapter_policy="fake-weighted-adapter-v1",
                    request_fingerprint="f" * 64,
                    planned_max_len=48,
                    executed_max_len=70,
                    model_kwargs={
                        "text": request.text,
                        "reference_wav_path": str(request.reference_audio),
                        "cfg_value": 1.8,
                        "inference_timesteps": 12,
                        "min_len": 2,
                        "max_len": 70,
                        "normalize": False,
                        "denoise": False,
                        "seed": request.seed,
                    },
                    accepted_optional_parameters=("seed",),
                    omitted_optional_parameters=("retry_badcase",),
                )
            )
        if self.invalid == "nan":
            return np.array([0.1, np.nan, 0.2], dtype=np.float32)
        if self.invalid == "silent":
            return np.zeros(24_000, dtype=np.float32)
        if self.invalid == "ambiguous":
            return np.zeros((32, 32), dtype=np.float32)
        total = 24_000
        index = np.arange(total, dtype=np.float32)
        return 0.08 * np.sin(2.0 * math.pi * 220.0 * index / 24_000.0)


class _FakeBackend:
    backend_id = "fake-weighted"
    adapter_policy = "fake-weighted-adapter-v1"

    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.session_configs: list[Any] = []

    def process_environment(
        self,
        request: dict[str, Any],
        *,
        base_environment: Mapping[str, str] | None = None,
    ) -> BackendProcessEnvironment:
        del request, base_environment
        return BackendProcessEnvironment(
            backend_id=self.backend_id,
            set_values=(("PYTHONUTF8", "1"),),
            removed_keys=(),
        )

    def identity(self, model_root: Path) -> BackendIdentity:
        return BackendIdentity(
            backend_id=self.backend_id,
            family="weighted-fixture",
            adapter_policy=self.adapter_policy,
            model_path=str(Path(model_root).resolve()),
            runtime_module="tests.fake_weighted_runtime",
            parameter_schema=("threads", "steps", "cfg", "cache_length"),
            output_contract="mono-pcm-wav-segment-v1",
        )

    def open_session(self, config: Any) -> _FakeSession:
        self.session_configs.append(config)
        return self.session

    def plan_generation_length(self, audio_spec: Any, request: Any) -> Any:
        assert audio_spec is self.session.audio_spec
        return BackendGenerationLengthPlan(
            backend_id=self.backend_id,
            duration_budget=request.duration_budget,
            attempt=request.attempt,
            backend_options={"min_len": 2, "max_len": 48},
            metadata={"fixture": True},
        )

    def plan_generation_profile(self, request: Any) -> Any:
        return BackendGenerationProfilePlan(
            backend_id=self.backend_id,
            attempt=request.attempt,
            backend_options={"cfg": 1.8, "steps": 12},
            metadata={"fixture": True},
        )


def _profile(*, evidence: bool = True) -> SpeechModelProfile:
    return SpeechModelProfile(
        profile_id="fake-weighted-profile",
        backend_id="fake-weighted",
        display_name="Fake weighted profile",
        model_family="fake-weighted-family",
        model_revision="fixture-v1",
        production_enabled=True,
        required_capabilities=(),
        option_specs=(
            ModelOptionSpec("threads", "int", 1, minimum=1, maximum=64),
            ModelOptionSpec("steps", "int", 12, minimum=1, maximum=256),
            ModelOptionSpec("cfg", "float", 1.8, minimum=0.1, maximum=10.0),
            ModelOptionSpec(
                "cache_length",
                "int",
                4096,
                minimum=2048,
                maximum=131072,
            ),
            ModelOptionSpec(
                "base_seed",
                "int",
                123,
                minimum=0,
                maximum=2_147_483_647,
            ),
        ),
        requires_execution_plan_evidence=evidence,
    )


def _runtime(session: _FakeSession, *, evidence: bool = True) -> WeightedTTSSmokeRuntime:
    profile = _profile(evidence=evidence)
    options = {spec.name: spec.default for spec in profile.option_specs}
    resolution = SpeechModelResolution(
        profile_id=profile.profile_id,
        backend_id=profile.backend_id,
        profile_fingerprint=profile.fingerprint(),
        request={
            "schema_version": 1,
            "speech_backend": profile.backend_id,
            "speech_model_profile": profile.profile_id,
            "speech_profile_fingerprint": profile.fingerprint(),
            "speech_options": options,
            **options,
        },
        options=options,
        backend_config={},
    )
    return WeightedTTSSmokeRuntime(
        backend=_FakeBackend(session),
        profile=profile,
        resolution=resolution,
        source_evidence={
            "schema_version": 1,
            "profile_id": profile.profile_id,
            "backend_id": profile.backend_id,
            "model_revision": profile.model_revision,
            "source": "runtime-registration",
            "source_kind": "runtime-registration",
            "source_sha256": "",
            "manifest_policy": "fixture-source-v1",
        },
    )


def _reference(path: Path) -> None:
    sample_rate = 16_000
    index = np.arange(sample_rate * 3, dtype=np.float32)
    samples = 0.08 * np.sin(2.0 * math.pi * 180.0 * index / sample_rate)
    sf.write(str(path), samples, sample_rate, subtype="PCM_16", format="WAV")


def _config(tmp_path: Path) -> WeightedTTSSmokeConfig:
    model = tmp_path / "private-model-root"
    model.mkdir()
    (model / "config.json").write_text('{"fixture":true}', encoding="utf-8")
    reference = tmp_path / "private-reference.wav"
    _reference(reference)
    return WeightedTTSSmokeConfig(
        profile_id="fake-weighted-profile",
        model_root=model,
        reference_wav=reference,
        work_dir=tmp_path / "private-work",
        expected_python=Path(sys.executable),
        duration_budget=1.0,
        seed=42,
    )


def _fake_probe(output: Path) -> dict[str, Any]:
    info = sf.info(str(output))
    return {
        "codec_name": "pcm_s24le",
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "duration_seconds": round(float(info.duration), 6),
    }


def test_weighted_smoke_runs_real_session_and_keeps_only_safe_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke as smoke

    monkeypatch.setattr(smoke, "_ffprobe_output", _fake_probe)
    session = _FakeSession(write_evidence=True)
    runtime = _runtime(session)
    config = _config(tmp_path)

    report = run_weighted_tts_smoke(config, runtime=runtime)
    report_path = config.work_dir / "report.json"
    serialized = report_path.read_text(encoding="utf-8")

    assert report["passed"] is True
    assert report["policy"] == TTS_WEIGHTED_SMOKE_POLICY
    assert report["profile"]["profile_id"] == "fake-weighted-profile"
    assert report["execution_plan"]["present"] is True
    assert report["execution_plan"]["model_scalar_arguments"]["cfg_value"] == 1.8
    assert report["output"]["audio_retained"] is False
    assert report["output"]["pcm"]["sample_rate"] == 24_000
    assert len(session.requests) == 1
    assert not (config.work_dir / "weighted-smoke.wav").exists()
    assert not (config.work_dir / "execution-plan.jsonl").exists()
    assert report_path.is_file()
    for private in (
        config.model_root,
        config.reference_wav,
        config.work_dir,
        Path(sys.executable),
    ):
        assert str(private) not in serialized
    assert "reference_wav_path" not in serialized
    assert "model_path" not in serialized
    assert "backend_defaults" not in serialized


def test_weighted_smoke_fails_closed_without_required_execution_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke as smoke

    monkeypatch.setattr(smoke, "_ffprobe_output", _fake_probe)
    config = _config(tmp_path)
    with pytest.raises(RuntimeError, match="без execution-plan evidence"):
        run_weighted_tts_smoke(
            config,
            runtime=_runtime(_FakeSession(write_evidence=False), evidence=True),
        )
    assert not (config.work_dir / "weighted-smoke.wav").exists()
    assert not (config.work_dir / "execution-plan.jsonl").exists()
    assert not (config.work_dir / "report.json").exists()


@pytest.mark.parametrize("invalid", ["nan", "silent", "ambiguous"])
def test_weighted_smoke_rejects_invalid_pcm(
    monkeypatch,
    tmp_path: Path,
    invalid: str,
) -> None:
    import services.tts_weighted_smoke as smoke

    monkeypatch.setattr(smoke, "_ffprobe_output", _fake_probe)
    config = _config(tmp_path)
    with pytest.raises(RuntimeError):
        run_weighted_tts_smoke(
            config,
            runtime=_runtime(_FakeSession(invalid=invalid)),
        )
    assert not (config.work_dir / "weighted-smoke.wav").exists()


def test_pcm_normalizer_accepts_both_channel_orders() -> None:
    base = np.linspace(-0.1, 0.1, 100, dtype=np.float32)
    first = normalize_generated_pcm(np.stack([base, base], axis=0))
    last = normalize_generated_pcm(np.stack([base, base], axis=1))
    assert first.shape == (100,)
    assert last.shape == (100,)
    assert np.allclose(first, base)
    assert np.allclose(last, base)


def test_expected_python_mismatch_fails_before_model_load(tmp_path: Path) -> None:
    config = _config(tmp_path)
    other = tmp_path / "other-python.exe"
    other.write_text("not python", encoding="utf-8")
    mismatched = WeightedTTSSmokeConfig(
        profile_id=config.profile_id,
        model_root=config.model_root,
        reference_wav=config.reference_wav,
        work_dir=config.work_dir,
        expected_python=other,
        duration_budget=config.duration_budget,
    )
    with pytest.raises(RuntimeError, match="не тем Python interpreter"):
        run_weighted_tts_smoke(mismatched, runtime=_runtime(_FakeSession()))
    assert not config.work_dir.exists()


def test_temporary_backend_environment_is_restored(monkeypatch, tmp_path: Path) -> None:
    import services.tts_weighted_smoke as smoke

    monkeypatch.setattr(smoke, "_ffprobe_output", _fake_probe)
    monkeypatch.setenv("PYTHONUTF8", "original-fixture")
    config = _config(tmp_path)
    run_weighted_tts_smoke(config, runtime=_runtime(_FakeSession()))
    assert os.environ["PYTHONUTF8"] == "original-fixture"
