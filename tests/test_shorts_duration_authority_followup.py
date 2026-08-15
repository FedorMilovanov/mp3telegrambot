from pathlib import Path

import services.shorts_duration_safety as safety


def _candidate(start: float, end: float) -> dict:
    return {"start_seconds": start, "end_seconds": end, "title": "Short"}


def test_semantic_candidate_past_source_duration_is_rejected():
    assert safety.plan_short_source_window(
        _candidate(850.0, 901.0),
        speed=1.0,
        boundary_padding=True,
        source_duration=900.0,
        pre_roll=1.5,
        post_roll=2.5,
    ) is None


def test_nonfinite_optional_padding_does_not_poison_window():
    plan = safety.plan_short_source_window(
        _candidate(10.0, 100.0),
        speed=1.0,
        boundary_padding=True,
        source_duration=500.0,
        pre_roll=float("nan"),
        post_roll=float("inf"),
    )
    assert plan is not None
    assert plan[0] == 10.0
    assert plan[1] == 100.0


def test_saved_replay_start_uses_actual_reclaimed_render_window():
    plan = safety.plan_short_source_window(
        _candidate(100.0, 368.0),
        speed=1.5,
        boundary_padding=True,
        source_duration=1000.0,
        pre_roll=1.5,
        post_roll=2.5,
    )
    assert plan is not None
    actual_start = plan[0]
    pipeline_start = 98.5
    assert actual_start != pipeline_start
    assert safety.authoritative_short_source_start(pipeline_start, plan[:2]) == actual_start
    assert safety.authoritative_short_source_start(pipeline_start, None) == pipeline_start


def test_duration_owner_wires_authoritative_delivery_timing():
    safety_source = Path("services/shorts_duration_safety.py").read_text(encoding="utf-8")
    pipeline_source = Path("pipelines/shorts.py").read_text(encoding="utf-8")

    assert "resolve_delivery_timing(" in pipeline_source
    assert "source_start=render_start" in pipeline_source
    assert "raw_duration=raw_duration" in pipeline_source
    assert "final_duration=final_probe.duration" in pipeline_source
    assert "timing.source_start" in pipeline_source
    assert "timing.source_end" in pipeline_source

    assert "resolve_delivery_timing =" not in safety_source
    assert "setattr(" not in safety_source
    assert "sys.modules" not in safety_source
