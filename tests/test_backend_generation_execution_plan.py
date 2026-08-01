from __future__ import annotations

import json
from pathlib import Path

from services.speech_backends import (
    AuditedVoxCPM2Backend,
    BackendAudioSpec,
    BackendGenerationRequest,
    default_backend,
)
from services.speech_backends.audited_voxcpm2 import AuditedVoxCPM2Session
from services.speech_backends.execution_plan import GENERATION_EXECUTION_PLAN_POLICY


class _Model:
    def __init__(self) -> None:
        self.calls = []

    def generate(
        self,
        *,
        text,
        reference_wav_path,
        cfg_value,
        inference_timesteps,
        min_len,
        max_len,
        normalize,
        denoise,
        retry_badcase,
        retry_badcase_max_times,
        retry_badcase_ratio_threshold,
        seed,
    ):
        call = {
            "text": text,
            "reference_wav_path": reference_wav_path,
            "cfg_value": cfg_value,
            "inference_timesteps": inference_timesteps,
            "min_len": min_len,
            "max_len": max_len,
            "normalize": normalize,
            "denoise": denoise,
            "retry_badcase": retry_badcase,
            "retry_badcase_max_times": retry_badcase_max_times,
            "retry_badcase_ratio_threshold": retry_badcase_ratio_threshold,
            "seed": seed,
        }
        self.calls.append(call)
        return [0.0, 0.1]


def _request(tmp_path: Path) -> BackendGenerationRequest:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF")
    return BackendGenerationRequest(
        text="Число 25 процентов",
        reference_audio=reference,
        seed=42,
        duration_budget=2.0,
        backend_options={
            "cfg": 1.95,
            "steps": 20,
            "min_len": 12,
            "max_len": 70,
        },
    )


def test_exact_execution_plan_matches_real_model_kwargs(monkeypatch, tmp_path: Path):
    model = _Model()
    session = AuditedVoxCPM2Session(
        model,
        BackendAudioSpec(
            encode_sample_rate=16_000,
            output_sample_rate=48_000,
            seconds_per_step=0.08,
            cache_length=4096,
        ),
    )
    log = tmp_path / "execution-plans.jsonl"
    monkeypatch.setenv("DUB_BACKEND_EXECUTION_PLAN_LOG", str(log))

    assert session.generate(_request(tmp_path)) == [0.0, 0.1]

    plan = session.last_execution_plan
    assert plan is not None
    assert plan.planned_max_len == 70
    assert plan.executed_max_len == 102
    assert plan.model_kwargs == model.calls[0]
    assert plan.model_kwargs["normalize"] is True
    assert plan.model_kwargs["denoise"] is False
    assert plan.model_kwargs["retry_badcase"] is True
    assert plan.model_kwargs["retry_badcase_max_times"] == 2
    assert plan.model_kwargs["retry_badcase_ratio_threshold"] == 6.0

    persisted = json.loads(log.read_text(encoding="utf-8").strip())
    assert persisted["policy"] == GENERATION_EXECUTION_PLAN_POLICY
    assert persisted["planned_max_len"] == 70
    assert persisted["executed_max_len"] == 102
    assert persisted["model_kwargs"] == model.calls[0]


def test_default_production_backend_is_audited_voxcpm2():
    assert isinstance(default_backend(), AuditedVoxCPM2Backend)
