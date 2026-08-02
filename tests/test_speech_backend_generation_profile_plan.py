from __future__ import annotations

from pathlib import Path

import pytest

from services.speech_backends import (
    GENERATION_PROFILE_POLICY,
    GENERATION_PROFILE_REQUEST_POLICY,
    BackendGenerationProfilePlan,
    BackendGenerationProfileRequest,
    default_backend,
)
from services.speech_backends.voxcpm2 import (
    GENERATION_PROFILE_POLICY as VOXCPM2_GENERATION_PROFILE_POLICY,
)
from tools.voxcpm2 import direct_max_quality_cli
from tools.voxcpm2 import direct_max_quality_render


ROOT = Path(__file__).resolve().parents[1]
RAW_CLI = ROOT / "tools" / "voxcpm2" / "_direct_max_quality_cli_base.py"
RAW_RENDER = ROOT / "tools" / "voxcpm2" / "direct_max_quality_render.py"
RENDER_FACADE = (
    ROOT / "tools" / "voxcpm2" / "direct_max_quality_render" / "__init__.py"
)


def _request(attempt: int, *, cfg: float = 1.9, steps: int = 16):
    return BackendGenerationProfileRequest(
        attempt=attempt,
        base_backend_options={"cfg": cfg, "steps": steps},
    )


def test_generation_profile_request_and_plan_are_typed_and_serializable() -> None:
    request = BackendGenerationProfileRequest(
        attempt=2,
        base_backend_options={"opaque_quality": 17},
        metadata={"reason": "retry"},
    )
    plan = BackendGenerationProfilePlan(
        backend_id="Example-Backend",
        attempt=2,
        backend_options={"opaque_attempt_option": 19},
        metadata={"reason": "backend"},
    )

    assert GENERATION_PROFILE_REQUEST_POLICY == (
        "model-neutral-generation-profile-request-v1"
    )
    assert GENERATION_PROFILE_POLICY == "model-neutral-generation-profile-plan-v1"
    assert request.as_dict() == {
        "attempt": 2,
        "base_backend_options": {"opaque_quality": 17},
        "metadata": {"reason": "retry"},
        "generation_profile_request_policy": GENERATION_PROFILE_REQUEST_POLICY,
    }
    assert plan.backend_id == "example-backend"
    assert plan.as_dict() == {
        "backend_id": "example-backend",
        "attempt": 2,
        "backend_options": {"opaque_attempt_option": 19},
        "metadata": {"reason": "backend"},
        "generation_profile_policy": GENERATION_PROFILE_POLICY,
    }

    with pytest.raises(ValueError, match="attempt"):
        BackendGenerationProfileRequest(0)
    with pytest.raises(ValueError, match="backend_id"):
        BackendGenerationProfilePlan("", 1)


def test_voxcpm2_backend_owns_all_five_active_retry_profiles() -> None:
    backend = default_backend()
    plans = [backend.plan_generation_profile(_request(attempt)) for attempt in range(1, 6)]

    assert VOXCPM2_GENERATION_PROFILE_POLICY == (
        "voxcpm2-adaptive-generation-profile-v1"
    )
    assert plans[0].backend_options == {"cfg": 1.9, "steps": 16}
    assert plans[1].backend_options["cfg"] == pytest.approx(1.98)
    assert plans[1].backend_options["steps"] == 22
    assert plans[2].backend_options["cfg"] == pytest.approx(1.82)
    assert plans[2].backend_options["steps"] == 26
    assert plans[3].backend_options["cfg"] == pytest.approx(1.93)
    assert plans[3].backend_options["steps"] == 30
    assert plans[4].backend_options["cfg"] == pytest.approx(1.78)
    assert plans[4].backend_options["steps"] == 34
    assert all(plan.backend_id == backend.backend_id for plan in plans)
    assert [plan.attempt for plan in plans] == [1, 2, 3, 4, 5]
    assert plans[4].metadata["profile_request"]["attempt"] == 5


def test_voxcpm2_profile_planner_preserves_caps_and_fails_closed() -> None:
    backend = default_backend()

    attempt_two = backend.plan_generation_profile(_request(2, cfg=2.14, steps=29))
    attempt_four = backend.plan_generation_profile(_request(4, cfg=1.0, steps=30))
    attempt_five = backend.plan_generation_profile(_request(5, cfg=1.5, steps=30))

    assert attempt_two.backend_options == {"cfg": 2.15, "steps": 30}
    assert attempt_four.backend_options == {"cfg": 1.5, "steps": 36}
    assert attempt_five.backend_options == {"cfg": 1.45, "steps": 40}

    with pytest.raises(ValueError, match="Неподдерживаемая попытка"):
        backend.plan_generation_profile(_request(6))
    with pytest.raises(ValueError, match="base_backend_options.cfg"):
        backend.plan_generation_profile(_request(1, cfg=float("nan")))
    with pytest.raises(ValueError, match="base_backend_options.steps"):
        backend.plan_generation_profile(
            BackendGenerationProfileRequest(
                attempt=1,
                base_backend_options={"cfg": 1.9, "steps": True},
            )
        )
    with pytest.raises(TypeError, match="BackendGenerationProfileRequest"):
        backend.plan_generation_profile({"attempt": 1})  # type: ignore[arg-type]


def test_candidate_loop_builds_and_merges_opaque_profile_plans() -> None:
    request = direct_max_quality_cli._build_generation_profile_request(
        {"cfg": 1.9, "steps": 16},
        attempt=3,
    )
    merged = direct_max_quality_cli._merge_backend_options(
        {"opaque_length": 70},
        {"opaque_profile": 26},
    )

    assert isinstance(request, BackendGenerationProfileRequest)
    assert request.attempt == 3
    assert request.base_backend_options == {"cfg": 1.9, "steps": 16}
    assert merged == {"opaque_length": 70, "opaque_profile": 26}

    with pytest.raises(RuntimeError, match="conflict|конфликт"):
        direct_max_quality_cli._merge_backend_options(
            {"same": 1},
            {"same": 2},
        )


def test_legacy_profile_helpers_delegate_without_owning_retry_math() -> None:
    assert direct_max_quality_render._generation_profile(1, 1.9, 16) == (1.9, 16)
    cfg, steps = direct_max_quality_render._generation_profile(5, 1.9, 16)
    assert cfg == pytest.approx(1.78)
    assert steps == 34

    raw_cli_source = RAW_CLI.read_text(encoding="utf-8")
    raw_render_source = RAW_RENDER.read_text(encoding="utf-8")
    facade_source = RENDER_FACADE.read_text(encoding="utf-8")

    assert "_generation_profile," not in raw_cli_source
    assert "backend.plan_generation_profile(profile_request)" in raw_cli_source
    assert "cfg_value, step_count" not in raw_cli_source
    assert "cfg=cfg_value" not in raw_cli_source
    assert "steps=step_count" not in raw_cli_source
    assert "backend_options.update(" not in raw_cli_source
    assert "profile_plan.backend_options" in raw_cli_source

    for source in (raw_render_source, facade_source):
        assert "if attempt == 2:" not in source
        assert "base_cfg + 0.08" not in source
        assert "base_steps + 18" not in source
        assert ".plan_generation_profile(" in source
