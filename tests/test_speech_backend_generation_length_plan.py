from __future__ import annotations

from pathlib import Path

import pytest

from services.speech_backends import (
    GENERATION_LENGTH_POLICY,
    GENERATION_LENGTH_REQUEST_POLICY,
    BackendAudioSpec,
    BackendGenerationLengthPlan,
    BackendGenerationLengthRequest,
    BackendGenerationRequest,
    default_backend,
)
from services.speech_backends.voxcpm2 import (
    GENERATION_LENGTH_POLICY as VOXCPM2_GENERATION_LENGTH_POLICY,
)
from tools.voxcpm2 import direct_max_quality_cli


ROOT = Path(__file__).resolve().parents[1]
RAW_CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
FACADE = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli" / "__init__.py"


def _audio_spec(seconds_per_step: float | None = 0.08) -> BackendAudioSpec:
    return BackendAudioSpec(16_000, 48_000, seconds_per_step, 4096)


def _length_request(
    *,
    attempt: int = 1,
    previous: tuple[float, ...] = (),
    minimum_completion_ratio: float | None = None,
) -> BackendGenerationLengthRequest:
    return BackendGenerationLengthRequest(
        duration_budget=4.0,
        attempt=attempt,
        previous_output_durations=previous,
        minimum_completion_ratio=minimum_completion_ratio,
    )


def test_generation_length_request_is_typed_and_serializable() -> None:
    request = BackendGenerationLengthRequest(
        duration_budget=3.5,
        attempt=2,
        previous_output_durations=(1.0, 2.0),
        minimum_completion_ratio=0.58,
        metadata={"reason": "cadence"},
    )

    assert GENERATION_LENGTH_REQUEST_POLICY == (
        "model-neutral-generation-length-request-v1"
    )
    assert request.as_dict() == {
        "duration_budget": 3.5,
        "attempt": 2,
        "previous_output_durations": [1.0, 2.0],
        "minimum_completion_ratio": 0.58,
        "metadata": {"reason": "cadence"},
        "generation_length_request_policy": GENERATION_LENGTH_REQUEST_POLICY,
    }

    with pytest.raises(ValueError, match="attempt"):
        BackendGenerationLengthRequest(3.5, 0)
    with pytest.raises(ValueError, match="minimum_completion_ratio"):
        BackendGenerationLengthRequest(3.5, 1, minimum_completion_ratio=1.0)
    with pytest.raises(ValueError, match="previous_output_durations"):
        BackendGenerationLengthRequest(
            3.5,
            1,
            previous_output_durations=(float("nan"),),
        )


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


def test_voxcpm2_backend_owns_base_short_retry_and_completion_windows() -> None:
    backend = default_backend()

    base = backend.plan_generation_length(_audio_spec(), _length_request())
    retry = backend.plan_generation_length(
        _audio_spec(),
        _length_request(attempt=3, previous=(1.0, 1.5)),
    )
    mixed = backend.plan_generation_length(
        _audio_spec(),
        _length_request(attempt=3, previous=(1.0, 2.0)),
    )
    standalone = backend.plan_generation_length(
        _audio_spec(),
        _length_request(minimum_completion_ratio=0.40),
    )
    linked = backend.plan_generation_length(
        _audio_spec(),
        _length_request(minimum_completion_ratio=0.58),
    )
    linked_retry = backend.plan_generation_length(
        _audio_spec(),
        _length_request(
            attempt=3,
            previous=(1.0, 1.5),
            minimum_completion_ratio=0.58,
        ),
    )

    assert VOXCPM2_GENERATION_LENGTH_POLICY == "voxcpm2-duration-to-token-window-v2"
    assert base.backend_options == {"min_len": 2, "max_len": 70}
    assert base.metadata["desired_steps"] == 50.0
    assert base.metadata["short_retry"] is False
    assert retry.backend_options == {"min_len": 21, "max_len": 70}
    assert retry.metadata["short_retry"] is True
    assert mixed.backend_options == {"min_len": 2, "max_len": 70}
    assert standalone.backend_options == {"min_len": 20, "max_len": 70}
    assert linked.backend_options == {"min_len": 28, "max_len": 70}
    assert linked_retry.backend_options == {"min_len": 28, "max_len": 70}
    assert linked.metadata["length_request"]["minimum_completion_ratio"] == 0.58


def test_voxcpm2_length_planner_requires_typed_request_and_internal_evidence() -> None:
    backend = default_backend()

    with pytest.raises(RuntimeError, match="seconds_per_step"):
        backend.plan_generation_length(_audio_spec(None), _length_request())

    with pytest.raises(TypeError, match="BackendGenerationLengthRequest"):
        backend.plan_generation_length(  # type: ignore[arg-type]
            _audio_spec(),
            {"duration_budget": 4.0},
        )


def test_monolithic_facade_adds_neutral_cadence_hint() -> None:
    standalone = direct_max_quality_cli._build_generation_length_request(
        {"cadence_type": "standalone"},
        duration_budget=4.0,
        attempt=1,
        previous_output_durations=(),
    )
    linked = direct_max_quality_cli._build_generation_length_request(
        {"cadence_type": "linked"},
        duration_budget=4.0,
        attempt=2,
        previous_output_durations=(1.0,),
    )

    assert isinstance(standalone, BackendGenerationLengthRequest)
    assert standalone.minimum_completion_ratio == 0.40
    assert linked.minimum_completion_ratio == 0.58
    assert linked.metadata == {
        "policy": direct_max_quality_cli.GENERATION_LENGTH_HINT_POLICY,
        "cadence_type": "linked",
    }


def test_request_factory_preserves_backend_plan_options_without_interpretation() -> None:
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


def test_candidate_loop_and_facade_contain_no_voxcpm2_length_math() -> None:
    raw_source = RAW_CLI.read_text(encoding="utf-8")
    facade_source = FACADE.read_text(encoding="utf-8")

    assert "speech_slot / seconds_per_step" not in raw_source
    assert "desired_steps =" not in raw_source
    assert "speech_slot * 0.48" not in raw_source
    assert "desired_steps * 0.42" not in raw_source
    assert "BackendGenerationLengthRequest" in raw_source
    assert "_build_generation_length_request(" in raw_source
    assert "backend.plan_generation_length(audio_spec, length_request)" in raw_source
    assert "backend_options=length_plan.backend_options" in raw_source

    assert "option_int(\"max_len\"" not in facade_source
    assert "estimated_steps" not in facade_source
    assert "controlled_min_len" not in facade_source
    assert "backend_options[\"min_len\"]" not in facade_source
    assert "minimum_completion_ratio=minimum_ratio" in facade_source
    assert "backend_options=base_request.backend_options" in facade_source
