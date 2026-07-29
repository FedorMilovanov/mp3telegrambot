from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import expressive_continuity as expression


ROOT = Path(__file__).resolve().parents[1]


def test_expression_smoothing_limits_adjacent_jumps() -> None:
    values = [-2.0, 2.2, -2.1, 2.4, -1.8]
    smoothed = expression._smooth(values)
    assert len(smoothed) == len(values)
    assert all(-1.65 <= value <= 1.65 for value in smoothed)
    assert all(
        abs(right - left) <= 0.72 + 1e-9
        for left, right in zip(smoothed, smoothed[1:], strict=True)
    )


def test_stronger_arc_uses_expressive_real_reference() -> None:
    assert expression._reference_profile("reflective") == "extended"
    assert expression._reference_profile("warm") == "extended"
    assert expression._reference_profile("earnest") == "extended"
    assert expression._reference_profile("emphatic") == "composite"
    assert expression._reference_profile("passionate") == "composite"


def test_high_style_is_controlled_not_shouting() -> None:
    tier, instruction = expression._style(1.4, 0.4, "Это важно!")
    assert tier == "passionate"
    assert "controlled" in instruction
    assert "never shout" in instruction


def test_expressive_candidate_filter_rejects_shouting() -> None:
    common = {
        "active_ratio": 0.70,
        "max_internal_gap": 0.12,
        "rms_dbfs": -23.0,
        "voiced_ratio": 0.55,
        "speech_rate": 2.6,
    }
    segments = [
        {
            "id": 1,
            "expression_score": 0.55,
            "source_prosody": {
                **common,
                "start": 0.0,
                "end": 2.4,
                "f0_median": 105.0,
                "f0_p90": 145.0,
            },
        },
        {
            "id": 2,
            "expression_score": 0.82,
            "source_prosody": {
                **common,
                "start": 2.5,
                "end": 5.0,
                "f0_median": 118.0,
                "f0_p90": 162.0,
            },
        },
        {
            "id": 3,
            "expression_score": 1.20,
            "source_prosody": {
                **common,
                "start": 5.1,
                "end": 7.6,
                "f0_median": 260.0,
                "f0_p90": 350.0,
                "rms_dbfs": -10.0,
            },
        },
    ]
    candidates = expression._expressive_candidates(segments)
    ids = {int(item["id"]) for item in candidates}
    assert 2 in ids
    assert 3 not in ids


def test_clean_routes_apply_expression_without_tts_wrapper() -> None:
    for name in (
        "generic_clean_gemini_runtime.py",
        "generic_clean_direct_runtime.py",
        "generic_clean_custom_runtime.py",
    ):
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "expressive_continuity.plan_json" in source
        assert "build_controlled_expressive_reference" in source
        assert "semantic_tts_guard_v4.install" not in source
        assert "runpy.run_path" not in source


def test_gemini_route_uses_rhetoric_preserving_translation() -> None:
    route = (
        ROOT / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"
    ).read_text(encoding="utf-8")
    policy = (
        ROOT / "tools" / "voxcpm2" / "expressive_translation.py"
    ).read_text(encoding="utf-8")
    assert "production.translate_groups_max = expressive_translation.translate_groups" in route
    assert "metadata: dict[str, Any] | None" in policy
    assert "намеренные повторы" in policy
    assert "риторические вопросы" in policy
    assert "непрерывную мысль" in policy
    assert "Не превращай фразу в конспект" in policy
