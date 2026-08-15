from pathlib import Path
from types import SimpleNamespace

import services.shorts_duration_safety as safety


def _candidate(start: float, end: float) -> dict:
    return {
        "start_seconds": start,
        "end_seconds": end,
        "title": "Тестовый Short",
    }


def test_speed_scaled_candidate_is_not_compensated_twice():
    plan = safety.plan_short_source_window(
        _candidate(100.0, 370.0),
        speed=1.5,
        boundary_padding=False,
        source_duration=1000.0,
    )

    assert plan is not None
    start, end, snap_end = plan
    assert start == 100.0
    assert end == 370.0
    assert end - start == 270.0
    assert snap_end <= 370.0
    assert (end - start) / 1.5 == 180.0


def test_padding_is_reclaimed_inside_same_speed_source_budget():
    plan = safety.plan_short_source_window(
        _candidate(100.0, 368.0),
        speed=1.5,
        boundary_padding=True,
        source_duration=1000.0,
        pre_roll=1.5,
        post_roll=2.5,
    )

    assert plan is not None
    start, end, snap_end = plan
    assert start <= 100.0
    assert end >= 368.0
    assert end - start <= 270.0 + 1e-6
    assert snap_end - start <= 270.0 + 1e-6


def test_full_budget_candidate_never_grows_for_optional_padding():
    plan = safety.plan_short_source_window(
        _candidate(100.0, 370.0),
        speed=1.5,
        boundary_padding=True,
        source_duration=1000.0,
        pre_roll=1.5,
        post_roll=2.5,
    )

    assert plan == (100.0, 370.0, 370.0)


def test_candidate_over_public_speed_budget_fails_closed():
    assert safety.plan_short_source_window(
        _candidate(100.0, 370.1),
        speed=1.5,
        boundary_padding=False,
    ) is None
    assert safety.plan_short_source_window(
        _candidate(0.0, 181.0),
        speed=1.0,
        boundary_padding=False,
    ) is None


def test_source_duration_clamps_optional_tail_without_cutting_valid_semantics():
    plan = safety.plan_short_source_window(
        _candidate(850.0, 899.0),
        speed=1.0,
        boundary_padding=True,
        source_duration=900.0,
        pre_roll=1.5,
        post_roll=2.5,
    )

    assert plan is not None
    start, end, snap_end = plan
    assert start == 848.5
    assert end == 900.0
    assert snap_end <= 900.0


def test_public_duration_and_required_speed_contracts_are_fail_closed():
    assert safety.public_short_duration_ok(180.0)
    assert safety.public_short_duration_ok(180.04)
    assert not safety.public_short_duration_ok(180.051)
    assert not safety.public_short_duration_ok(float("nan"))
    assert not safety.public_short_duration_ok(float("inf"))

    assert not safety.short_speed_transform_required(1.0)
    assert safety.short_speed_transform_required(1.1)
    assert safety.short_speed_transform_required(1.3)
    assert safety.short_speed_transform_required(1.5)


def test_final_public_short_gate_checks_probe_duration_and_upload_cap():
    safe = SimpleNamespace(
        duration=179.9,
        width=720,
        height=1280,
        has_video=True,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="aac",
        size_mb=40.0,
    )
    assert safety.final_public_short_is_safe(safe, max_file_size_mb=50.0)

    too_long = SimpleNamespace(**{**safe.__dict__, "duration": 180.06})
    too_large = SimpleNamespace(**{**safe.__dict__, "size_mb": 51.0})
    assert not safety.final_public_short_is_safe(too_long, max_file_size_mb=50.0)
    assert not safety.final_public_short_is_safe(too_large, max_file_size_mb=50.0)


def test_pipeline_owns_duration_safety_without_installation_or_ambient_state():
    safety_source = Path("services/shorts_duration_safety.py").read_text(encoding="utf-8")
    pipeline_source = Path("pipelines/shorts.py").read_text(encoding="utf-8")
    renderer_source = Path("services/shorts_video.py").read_text(encoding="utf-8")

    assert "from contextvars import ContextVar" not in safety_source
    assert "install_shorts_duration_safety" not in safety_source
    assert "setattr(" not in safety_source
    assert "sys.modules" not in safety_source

    assert "window = plan_short_source_window(" in pipeline_source
    assert "silence_snap_max_end=snap_ceiling" in pipeline_source
    assert "short_speed_transform_required(speed)" in pipeline_source
    assert "required speed transform" in pipeline_source
    assert "final_public_short_is_safe(" in pipeline_source
    assert "speed_extra" not in pipeline_source

    assert "silence_snap_max_end: float | None = None" in renderer_source
    assert "adjusted_end = min(adjusted_end, hard_ceiling)" in renderer_source
    assert "end_seconds = min(end_seconds, hard_ceiling)" in renderer_source
    assert "import sys" not in renderer_source
    assert "sys.modules[" not in renderer_source
