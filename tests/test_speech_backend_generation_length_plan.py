from __future__ import annotations

from pathlib import Path

import pytest

from services.speech_backends import (
    GENERATION_LENGTH_POLICY,
    BackendAudioSpec,
    BackendGenerationLengthPlan,
    BackendGenerationRequest,
    default_backend,
)
from services.speech_backends.voxcpm2 import (
    GENERATION_LENGTH_POLICY as VOXCPM2_GENERATION_LENGTH_POLICY,
)
from tools.voxcpm2 import direct_max_quality_cli


ROOT = Path(__file__).resolve().parents[1]
RAW_CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"


def _audio_spec(seconds_per_step: float | None = 0.08) -> BackendAudioSpec:
    return BackendAudioSpec(16_000, 48_000, seconds_per_step, 4096)


def test_generation_length_plan_is_typed_and_serializable() -> None:
    plan = BackendGenerationLengthPlan(
        backend_id="Example-Backend",
        duration_budget=3.5,
        attempt=2,
        backend_options={"opaque_unit_limit": 44},
        metadata={"reason": "duration_budget"},
    )

    assert GENERATION_LENGTH_POLICY == "model-neutral-generation-length-plan-v1"
    assert plan.backend_id == "example-backend"
    assert plan.as_dict() == {
        "backend_id": "example-backend",
        "duration_budget": 3.5,
        "attempt": 2,
        "backend_options": {"opaque_unit_limit": 44},
        "metadata": {"reason": "duration_budget"},
        "generation_length_policy": GENERATION_LENGTH_POLICY,
    }

    with pytest.raises(ValueError, match="attempt"):
        BackendGenerationLengthPlan("example", 3.5, 0)


def test_voxcpm2_backend_owns_base_and_short_retry_windows() -> None:
    backend = default_backend()

    base = backend.plan_generation_length(
        _audio_spec(),
        duration_budget=4.0,
        attempt=1,
    )
    retry = backend.plan_generation_length(
        _audio_spec(),
        duration_budget=4.0,
        attempt=3,
        previous_output_durations=(1.0, 1.5),
    )
    mixed = backend.plan_generation_length(
        _audio_spec(),
        duration_budget=4.0,
        attempt=3,
        previous_output_durations=(1.0, 2.0),
    )

    assert VOXCPM2_GENERATION_LENGTH_POLICY == "voxcpm2-duration-to-token-window-v1"
    assert base.backend_options == {"min_len": 2, "max_len": 70}
    assert base.metadata["desired_steps"] == 50.0
    assert base.metadata["short_retry"] is False
    assert retry.backend_options == {"min_len": 21, "max_len": 70}
    assert retry.metadata["short_retry"] is True
    assert mixed.backend_options == {"min_len": 2, "max_len": 70}
    assert mixed.metadata["short_retry"] is False


def test_voxcpm2_length_planner_fails_without_internal_step_evidence() -> None:
    backend = default_backend()

    with pytest.raises(RuntimeError, match="seconds_per_step"):
        backend.plan_generation_length(
            _audio_spec(None),
            duration_budget=4.0,
            attempt=1,
        )

    with pytest.raises(ValueError, match="previous_output_durations"):
        backend.plan_generation_length(
            _audio_spec(),
            duration_budget=4.0,
            attempt=3,
            previous_output_durations=(float("nan"),),
        )


def test_request_factory_preserves_opaque_backend_plan_options() -> None:
    request = direct_max_quality_cli._build_generation_request(
        object(),
        text="Текст.",
        reference=Path("reference.wav"),
        cfg=1.8,
        steps=16,
        duration_budget=4.0,
        backend_options={"opaque_length_unit": 77},
        seed=11,
    )

    assert isinstance(request, BackendGenerationRequest)
    assert request.duration_budget == 4.0
    assert request.backend_options == {
        "opaque_length_unit": 77,
        "cfg": 1.8,
        "steps": 16,
    }


def test_raw_candidate_loop_contains_no_voxcpm2_length_math() -> None:
    source = RAW_CLI.read_text(encoding="utf-8")

    assert "speech_slot / seconds_per_step" not in source
    assert "desired_steps =" not in source
    assert "speech_slot * 0.48" not in source
    assert "desired_steps * 0.42" not in source
    assert "backend.plan_generation_length(" in source
    assert "backend_options=length_plan.backend_options" in source
    assert "previous_output_durations=tuple(" in source
