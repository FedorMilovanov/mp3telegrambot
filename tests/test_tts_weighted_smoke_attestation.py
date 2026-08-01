from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.tts_weighted_smoke import (
    TTS_WEIGHTED_SMOKE_POLICY,
    TTS_WEIGHTED_SMOKE_REPORT_POLICY,
)
from services.tts_weighted_smoke_attestation import (
    TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY,
    WeightedTTSSmokeAttestationContext,
    build_weighted_tts_attestation,
    validate_weighted_tts_attestation,
    write_weighted_tts_attestation,
)
from services.tts_weighted_smoke_runner import (
    TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
    TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY,
)


PROFILE_FINGERPRINT = "a" * 64
SOURCE_SHA = "b" * 64
MODEL_SHA = "c" * 64
COMMIT_SHA = "d" * 40


def _profile() -> dict:
    return {
        "profile_id": "voxcpm2-production-v1",
        "backend_id": "voxcpm2",
        "display_name": "VoxCPM2 production",
        "model_family": "voxcpm2",
        "model_revision": "local-archive-pinned-v1",
        "profile_fingerprint": PROFILE_FINGERPRINT,
        "source": {
            "schema_version": 1,
            "profile_id": "voxcpm2-production-v1",
            "backend_id": "voxcpm2",
            "model_revision": "local-archive-pinned-v1",
            "source": "voxcpm2-production-v1.json",
            "source_kind": "repository-manifest",
            "source_sha256": SOURCE_SHA,
            "manifest_policy": "strict-tts-profile-manifest-v1",
        },
    }


def _backend() -> dict:
    return {
        "backend_id": "voxcpm2",
        "family": "voxcpm2",
        "adapter_policy": "audited-voxcpm2-generation-call-v1",
        "runtime_module": "services.speech_backends.voxcpm2_runtime",
        "parameter_schema": ["threads", "steps", "cfg", "cache_length"],
        "output_contract": "mono-pcm-wav-segment-v1",
    }


def _doctor_report() -> dict:
    return {
        "schema_version": 1,
        "policy": TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
        "report_policy": TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY,
        "passed": True,
        "completed_at": "2026-08-01T18:30:00+00:00",
        "profile": _profile(),
        "backend": _backend(),
        "model": {"config_present": True, "config_sha256": MODEL_SHA},
        "imports": {
            "modules": [
                {"name": "numpy", "version": "2.4.6"},
                {"name": "soundfile", "version": "0.14.0"},
                {"name": "torch", "version": "2.9.0"},
                {"name": "voxcpm", "version": "0.1.0"},
            ],
            "torch": {
                "version": "2.9.0",
                "cuda_available": False,
                "cuda_device_count": 0,
            },
        },
        "reference": {
            "duration_seconds": 12.0,
            "sample_rate": 44_100,
            "channels": 1,
            "format": "WAV",
            "subtype": "PCM_16",
            "rms": 0.08,
            "bytes": 1_000_000,
        },
        "ffprobe": {"available": True, "version": "8.0"},
        "environment": {
            "backend_id": "voxcpm2",
            "set_keys": ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"],
            "removed_keys": [],
            "hf_hub_offline": True,
            "transformers_offline": True,
            "configured_threads": 4,
        },
        "storage": {
            "write": True,
            "fsync": True,
            "replace": True,
            "readback": True,
            "cleanup": True,
        },
        "runtime": {
            "python_version": "3.11.15",
            "implementation": "CPython",
            "platform_system": "Windows",
            "machine": "AMD64",
            "weights_loaded": False,
            "session_opened": False,
        },
    }


def _smoke_report() -> dict:
    return {
        "schema_version": 1,
        "policy": TTS_WEIGHTED_SMOKE_POLICY,
        "report_policy": TTS_WEIGHTED_SMOKE_REPORT_POLICY,
        "passed": True,
        "completed_at": "2026-08-01T18:31:00+00:00",
        "profile": _profile(),
        "backend": _backend(),
        "model": {"config_present": True, "config_sha256": MODEL_SHA},
        "request": {
            "text_sha256": "e" * 64,
            "text_characters": 55,
            "duration_budget": 4.0,
            "seed": 2026080101,
            "generation_length_plan": {"policy": "fixture-length-v1"},
            "generation_profile_plan": {"policy": "fixture-profile-v1"},
        },
        "reference": {
            "duration_seconds": 12.0,
            "sample_rate": 44_100,
            "channels": 1,
            "format": "WAV",
            "subtype": "PCM_16",
            "rms": 0.08,
            "bytes": 1_000_000,
        },
        "output": {
            "pcm": {
                "samples": 96_000,
                "sample_rate": 24_000,
                "duration_seconds": 4.0,
                "peak": 0.4,
                "rms": 0.08,
                "clipping_ratio": 0.0,
            },
            "readback": {
                "samples": 96_000,
                "sample_rate": 24_000,
                "duration_seconds": 4.0,
                "peak": 0.4,
                "rms": 0.08,
                "clipping_ratio": 0.0,
                "format": "WAV",
                "subtype": "PCM_24",
                "bytes": 288_044,
            },
            "ffprobe": {
                "codec_name": "pcm_s24le",
                "sample_rate": 24_000,
                "channels": 1,
                "duration_seconds": 4.0,
            },
            "audio_retained": False,
        },
        "execution_plan": {
            "required": True,
            "present": True,
            "policy": "backend-generation-execution-plan-v1",
            "backend_id": "voxcpm2",
            "adapter_policy": "audited-voxcpm2-generation-call-v1",
            "planned_max_len": 192,
            "executed_max_len": 288,
            "model_kwarg_names": ["text", "reference_wav_path", "max_len"],
            "model_scalar_arguments": {"max_len": 288, "seed": 2026080101},
            "accepted_optional_parameters": ["seed"],
            "omitted_optional_parameters": [],
        },
        "runtime": {
            "python_version": "3.11.15",
            "implementation": "CPython",
            "platform_system": "Windows",
            "synthesis_seconds": 38.5,
            "total_seconds": 42.0,
        },
    }


def _context() -> WeightedTTSSmokeAttestationContext:
    return WeightedTTSSmokeAttestationContext(
        repository="FedorMilovanov/mp3telegrambot",
        commit_sha=COMMIT_SHA,
        ref="refs/heads/main",
        event_name="workflow_dispatch",
        workflow_ref=(
            "FedorMilovanov/mp3telegrambot/"
            ".github/workflows/tts-weighted-smoke.yml@refs/heads/main"
        ),
        run_id=30712648726,
        run_attempt=1,
    )


def test_attestation_cross_checks_reports_and_writes_only_allowlisted_facts(
    tmp_path: Path,
) -> None:
    private_model = tmp_path / "private-model"
    private_reference = tmp_path / "private-reference.wav"
    attestation = build_weighted_tts_attestation(
        _doctor_report(),
        _smoke_report(),
        _context(),
        forbidden_values=(str(private_model), str(private_reference)),
    )
    destination = tmp_path / "attestation" / "attestation.json"
    write_weighted_tts_attestation(destination, attestation)
    retained = json.loads(destination.read_text(encoding="utf-8"))
    validate_weighted_tts_attestation(
        retained,
        forbidden_values=(str(private_model), str(private_reference)),
    )

    assert retained["policy"] == TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY
    assert retained["subject"]["commit_sha"] == COMMIT_SHA
    assert retained["subject"]["run_id"] == 30712648726
    assert retained["result"]["profile_id"] == "voxcpm2-production-v1"
    assert retained["result"]["model_config_sha256"] == MODEL_SHA
    assert retained["result"]["execution_present"] is True
    assert retained["result"]["doctor_weights_loaded"] is False
    assert retained["result"]["audio_retained"] is False
    assert len(retained["digest_sha256"]) == 64

    serialized = destination.read_text(encoding="utf-8")
    assert str(private_model) not in serialized
    assert str(private_reference) not in serialized
    assert "reference_wav_path" not in serialized
    assert '"reference":' not in serialized
    assert '"request":' not in serialized


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda doctor, smoke: smoke["profile"].update(
                {"profile_fingerprint": "f" * 64}
            ),
            "разным TTS profiles",
        ),
        (
            lambda doctor, smoke: smoke["model"].update(
                {"config_sha256": "f" * 64}
            ),
            "разным model configs",
        ),
        (
            lambda doctor, smoke: doctor["runtime"].update(
                {"session_opened": True}
            ),
            "не должен открывать",
        ),
        (
            lambda doctor, smoke: smoke["output"].update(
                {"audio_retained": True}
            ),
            "не подтверждает удаление audio",
        ),
    ],
)
def test_attestation_rejects_cross_report_or_privacy_invariant_breaks(
    mutator,
    message: str,
) -> None:
    doctor = _doctor_report()
    smoke = _smoke_report()
    mutator(doctor, smoke)
    with pytest.raises(ValueError, match=message):
        build_weighted_tts_attestation(doctor, smoke, _context())


def test_attestation_rejects_path_bearing_source_report_key() -> None:
    doctor = _doctor_report()
    doctor["model_path"] = "C:/private/model"
    with pytest.raises(RuntimeError, match="запрещённый key"):
        build_weighted_tts_attestation(doctor, _smoke_report(), _context())


def test_attestation_digest_detects_tampering() -> None:
    attestation = build_weighted_tts_attestation(
        _doctor_report(), _smoke_report(), _context()
    )
    tampered = copy.deepcopy(attestation)
    tampered["result"]["output_duration_seconds"] = 9.0
    with pytest.raises(ValueError, match="digest"):
        validate_weighted_tts_attestation(tampered)


def test_attestation_context_rejects_non_main_or_wrong_workflow() -> None:
    with pytest.raises(ValueError, match="ref"):
        WeightedTTSSmokeAttestationContext(
            repository="FedorMilovanov/mp3telegrambot",
            commit_sha=COMMIT_SHA,
            ref="refs/heads/feature",
            event_name="workflow_dispatch",
            workflow_ref=(
                "FedorMilovanov/mp3telegrambot/"
                ".github/workflows/tts-weighted-smoke.yml@refs/heads/feature"
            ),
            run_id=1,
            run_attempt=1,
        )
