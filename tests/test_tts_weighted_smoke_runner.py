from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
import pytest
import soundfile as sf

from services.speech_backends import (
    BackendIdentity,
    BackendProcessEnvironment,
    ModelOptionSpec,
    SpeechModelProfile,
    SpeechModelResolution,
)
from services.tts_weighted_smoke import WeightedTTSSmokeRuntime
from services.tts_weighted_smoke_runner import (
    TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
    WeightedTTSSmokeRunnerConfig,
    _probe_ffprobe,
    run_weighted_tts_runner_doctor,
)


class _DoctorBackend:
    backend_id = "fake-doctor"

    def __init__(self, *, runtime_module: str = "json") -> None:
        self.runtime_module = runtime_module
        self.identity_calls = 0
        self.open_session_calls = 0

    def identity(self, model_root: Path) -> BackendIdentity:
        self.identity_calls += 1
        return BackendIdentity(
            backend_id=self.backend_id,
            family="fake-doctor-family",
            adapter_policy="fake-doctor-adapter-v1",
            model_path=str(Path(model_root).resolve()),
            runtime_module=self.runtime_module,
            parameter_schema=("threads",),
            output_contract="mono-pcm-wav-segment-v1",
        )

    def process_environment(
        self,
        request: dict[str, Any],
        *,
        base_environment: Mapping[str, str] | None = None,
    ) -> BackendProcessEnvironment:
        del request, base_environment
        return BackendProcessEnvironment(
            backend_id=self.backend_id,
            set_values=(
                ("HF_HUB_OFFLINE", "1"),
                ("TRANSFORMERS_OFFLINE", "1"),
                ("PYTHONUTF8", "1"),
            ),
            removed_keys=("HTTP_PROXY",),
        )

    def open_session(self, _config: Any) -> Any:
        self.open_session_calls += 1
        raise AssertionError("Runner doctor must never open a model session")


def _profile() -> SpeechModelProfile:
    return SpeechModelProfile(
        profile_id="fake-doctor-profile",
        backend_id="fake-doctor",
        display_name="Fake doctor profile",
        model_family="fake-doctor-family",
        model_revision="doctor-fixture-v1",
        production_enabled=True,
        required_capabilities=(),
        option_specs=(
            ModelOptionSpec("threads", "int", 3, minimum=1, maximum=64),
        ),
    )


def _runtime(backend: _DoctorBackend) -> WeightedTTSSmokeRuntime:
    profile = _profile()
    resolution = SpeechModelResolution(
        profile_id=profile.profile_id,
        backend_id=profile.backend_id,
        profile_fingerprint=profile.fingerprint(),
        request={
            "schema_version": 1,
            "speech_backend": profile.backend_id,
            "speech_model_profile": profile.profile_id,
            "speech_profile_fingerprint": profile.fingerprint(),
            "speech_options": {"threads": 3},
            "threads": 3,
        },
        options={"threads": 3},
        backend_config={},
    )
    return WeightedTTSSmokeRuntime(
        backend=backend,
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
            "manifest_policy": "doctor-fixture-source-v1",
        },
    )


def _reference(path: Path, *, seconds: float = 3.0, amplitude: float = 0.08) -> None:
    sample_rate = 16_000
    index = np.arange(int(sample_rate * seconds), dtype=np.float32)
    samples = amplitude * np.sin(2.0 * math.pi * 180.0 * index / sample_rate)
    sf.write(str(path), samples, sample_rate, subtype="PCM_16", format="WAV")


def _config(tmp_path: Path) -> WeightedTTSSmokeRunnerConfig:
    model_root = tmp_path / "private-model-root"
    model_root.mkdir()
    (model_root / "config.json").write_text('{"fixture":true}', encoding="utf-8")
    reference = tmp_path / "private-reference.wav"
    _reference(reference)
    return WeightedTTSSmokeRunnerConfig(
        profile_id="fake-doctor-profile",
        model_root=model_root,
        reference_wav=reference,
        work_dir=tmp_path / "private-doctor-work",
        expected_python=Path(sys.executable),
    )


def test_runner_doctor_passes_without_opening_model_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke_runner as doctor

    monkeypatch.setattr(
        doctor,
        "_probe_ffprobe",
        lambda: {"available": True, "version": "fixture-7.0"},
    )
    backend = _DoctorBackend()
    config = _config(tmp_path)

    report = run_weighted_tts_runner_doctor(config, runtime=_runtime(backend))
    report_path = config.work_dir / "report.json"
    serialized = report_path.read_text(encoding="utf-8")

    assert report["passed"] is True
    assert report["policy"] == TTS_WEIGHTED_SMOKE_RUNNER_POLICY
    assert report["runtime"]["weights_loaded"] is False
    assert report["runtime"]["session_opened"] is False
    assert report["environment"]["hf_hub_offline"] is True
    assert report["environment"]["transformers_offline"] is True
    assert report["environment"]["configured_threads"] == 3
    assert report["storage"] == {
        "write": True,
        "fsync": True,
        "replace": True,
        "readback": True,
        "cleanup": True,
    }
    assert report["model"]["config_present"] is True
    assert len(report["model"]["config_sha256"]) == 64
    assert {item["name"] for item in report["imports"]["modules"]} == {
        "json",
        "numpy",
        "soundfile",
    }
    assert backend.identity_calls == 1
    assert backend.open_session_calls == 0
    assert report_path.is_file()
    assert list(config.work_dir.iterdir()) == [report_path]
    for private in (
        config.model_root,
        config.reference_wav,
        config.work_dir,
        Path(sys.executable).resolve(),
        Path.cwd().resolve(),
    ):
        assert str(private) not in serialized
    assert "model_path" not in serialized
    assert "speech_backend_config" not in serialized


def test_runner_doctor_fails_closed_on_missing_runtime_import(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke_runner as doctor

    monkeypatch.setattr(
        doctor,
        "_probe_ffprobe",
        lambda: {"available": True, "version": "fixture"},
    )
    backend = _DoctorBackend(runtime_module="module_that_must_not_exist_12345")
    config = _config(tmp_path)

    with pytest.raises(RuntimeError, match="required module"):
        run_weighted_tts_runner_doctor(config, runtime=_runtime(backend))

    assert backend.open_session_calls == 0
    assert not config.work_dir.exists()


def test_runner_doctor_rejects_bad_reference_before_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke_runner as doctor

    monkeypatch.setattr(
        doctor,
        "_probe_ffprobe",
        lambda: {"available": True, "version": "fixture"},
    )
    backend = _DoctorBackend()
    config = _config(tmp_path)
    config.reference_wav.write_bytes(b"not-a-wave")

    with pytest.raises(RuntimeError, match="не читается"):
        run_weighted_tts_runner_doctor(config, runtime=_runtime(backend))

    assert backend.open_session_calls == 0
    assert not config.work_dir.exists()


def test_runner_doctor_rejects_missing_ffprobe_before_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke_runner as doctor

    def fail_probe() -> dict[str, Any]:
        raise RuntimeError("ffprobe не найден")

    monkeypatch.setattr(doctor, "_probe_ffprobe", fail_probe)
    backend = _DoctorBackend()
    config = _config(tmp_path)

    with pytest.raises(RuntimeError, match="ffprobe"):
        run_weighted_tts_runner_doctor(config, runtime=_runtime(backend))

    assert backend.open_session_calls == 0
    assert not config.work_dir.exists()


def test_runner_doctor_storage_failure_cleans_probe_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import services.tts_weighted_smoke_runner as doctor

    monkeypatch.setattr(
        doctor,
        "_probe_ffprobe",
        lambda: {"available": True, "version": "fixture"},
    )
    monkeypatch.setattr(
        doctor.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace blocked")),
    )
    config = _config(tmp_path)

    with pytest.raises(OSError, match="replace blocked"):
        run_weighted_tts_runner_doctor(
            config,
            runtime=_runtime(_DoctorBackend()),
        )

    assert config.work_dir.is_dir()
    assert list(config.work_dir.iterdir()) == []


def test_expected_python_mismatch_stops_before_backend_discovery(tmp_path: Path) -> None:
    backend = _DoctorBackend()
    config = _config(tmp_path)
    other = tmp_path / "private-other-python.exe"
    other.write_text("not python", encoding="utf-8")
    mismatched = WeightedTTSSmokeRunnerConfig(
        profile_id=config.profile_id,
        model_root=config.model_root,
        reference_wav=config.reference_wav,
        work_dir=config.work_dir,
        expected_python=other,
    )

    with pytest.raises(RuntimeError, match="не тем Python interpreter"):
        run_weighted_tts_runner_doctor(mismatched, runtime=_runtime(backend))

    assert backend.identity_calls == 0
    assert backend.open_session_calls == 0
    assert not config.work_dir.exists()


def test_ffprobe_probe_requires_binary(monkeypatch) -> None:
    import services.tts_weighted_smoke_runner as doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="ffprobe не найден"):
        _probe_ffprobe()


def test_runner_doctor_report_is_valid_json(monkeypatch, tmp_path: Path) -> None:
    import services.tts_weighted_smoke_runner as doctor

    monkeypatch.setattr(
        doctor,
        "_probe_ffprobe",
        lambda: {"available": True, "version": "fixture"},
    )
    config = _config(tmp_path)
    report = run_weighted_tts_runner_doctor(
        config,
        runtime=_runtime(_DoctorBackend()),
    )
    loaded = json.loads((config.work_dir / "report.json").read_text(encoding="utf-8"))
    assert loaded == report
