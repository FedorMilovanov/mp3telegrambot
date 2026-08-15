from pathlib import Path

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


def test_pipeline_guard_passes_silence_ceiling_explicitly_and_keeps_final_probe_gate():
    safety_source = Path("services/shorts_duration_safety.py").read_text(encoding="utf-8")
    renderer_source = Path("services/shorts_video.py").read_text(encoding="utf-8")

    assert "required_speed_transform_failed" in safety_source
    assert "public_short_duration_ok" in safety_source
    assert "speed_transform" in safety_source
    assert "silence_snap_max_end=snap_ceiling" in safety_source
    assert "_RENDER_SNAP_MAX_END" not in safety_source
    assert "short_video_impl._find_silence_end" not in safety_source

    assert "silence_snap_max_end: float | None = None" in renderer_source
    assert "adjusted_end = min(adjusted_end, hard_ceiling)" in renderer_source
    assert "end_seconds = min(end_seconds, hard_ceiling)" in renderer_source
    assert "sys.modules" not in renderer_source


def test_ordinary_duration_safety_installs_before_factory_video_capture():
    gate = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")

    ordinary_pos = gate.index("if not install_shorts_duration_safety():")
    factory_video_pos = gate.index("if not install_factory_video_quality_policy():")
    disk_pos = gate.index("if not install_factory_disk_guard():")

    assert ordinary_pos < factory_video_pos < disk_pos
