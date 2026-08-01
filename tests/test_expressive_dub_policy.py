from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import expressive_continuity as expression


ROOT = Path(__file__).resolve().parents[1]


def test_expression_smoothing_limits_adjacent_jumps() -> None:
    values = [-2.0, 2.2, -2.1, 2.4, -1.8]
    smoothed = expression._monolithic_scores(values)
    assert len(smoothed) == len(values)
    assert all(-0.65 <= value <= 0.68 for value in smoothed)
    assert all(
        abs(right - left) <= expression.MAX_ADJACENT_SCORE_STEP + 1e-9
        for left, right in zip(smoothed, smoothed[1:])
    )


def test_expression_uses_one_identity_reference() -> None:
    assert expression.REFERENCE_POLICY == "single-calm-identity-reference-v1"
    assert expression._tier([-0.5], 0) == "reflective"
    assert expression._tier([-0.2], 0) == "warm"
    assert expression._tier([0.2], 0) == "earnest"
    assert expression._tier([0.3, 0.5], 1) == "emphatic"


def test_high_style_remains_controlled() -> None:
    instruction = expression._style("emphatic", "terminal")
    assert "controlled" in instruction
    assert "never theatrical" in instruction
    assert "without a sudden emotional burst" in instruction


def test_isolated_strong_expression_is_downgraded() -> None:
    scores = expression._monolithic_scores([0.0, 0.68, 0.0])
    assert expression._tier(scores, 1) == "earnest"
    assert max(
        abs(right - left)
        for left, right in zip(scores, scores[1:])
    ) <= expression.MAX_ADJACENT_SCORE_STEP + 1e-9


def test_clean_routes_apply_expression_without_tts_wrapper() -> None:
    for name in (
        "generic_clean_gemini_runtime.py",
        "generic_clean_direct_runtime.py",
        "generic_clean_custom_runtime.py",
    ):
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "expressive_continuity.plan_json" in source
        assert "semantic_tts_guard_v4.install" not in source
        assert "runpy.run_path" not in source
    assert callable(expression.plan_json)
    assert callable(expression.plan_segments)
    assert expression.build_controlled_expressive_reference(
        source=Path("source.mp4"),
        segments=[],
        output=Path("expressive.wav"),
    ) is False


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
