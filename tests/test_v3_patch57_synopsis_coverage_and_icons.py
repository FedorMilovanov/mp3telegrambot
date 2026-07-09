"""Regression tests for v3 patch 57 — synopsis coverage and false-view icon polish."""

from pathlib import Path

from converters.md_telegraph import _postprocess_telegraph_nodes
from core.prompts import build_audio_analysis_prompt, STUDY_ANALYSIS_PROMPT


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    return ""


def test_audio_prompt_requires_timestamp_coverage_to_final_part():
    prompt = build_audio_analysis_prompt("T", "A", "55:00", 3300, mode="deep")
    assert "последний смысловой таймкод" in prompt
    assert "последние 10–15%" in prompt


def test_synopsis_uses_more_timestamp_anchors_and_larger_long_targets():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "_ts_raw[:24]" in src
    assert "Покрой ход материала до последнего опорного таймкода" in src
    assert "get_synopsis_density_profile(_duration)" in src
    from core.synopsis_quality import get_synopsis_density_profile
    assert get_synopsis_density_profile(3600).sections == "10-16"
    assert get_synopsis_density_profile(3600).total_chars == "9000"


def test_study_prompt_prefers_cross_mark_for_false_views():
    assert "используй ❌, не ⚠️" in STUDY_ANALYSIS_PROMPT
    assert "ортодоксального ответа используй ✅" in STUDY_ANALYSIS_PROMPT


def test_telegraph_postprocess_replaces_warning_icon_for_false_view_context():
    nodes = [{"tag": "p", "children": ["⚠️ **Ложное мессианское ожидание** — заблуждение о политическом царе."]}]
    out = _postprocess_telegraph_nodes(nodes)
    flat = _flat(out[0])
    assert "⚠️" not in flat
    assert "❌" in flat


def test_synopsis_editpage_shrinks_before_dropping_toc_on_content_too_big():
    """AUDIT R19: раньше при CONTENT_TOO_BIG для части 1 TOC выбрасывался
    сразу и безусловно. Теперь сначала пробуем сжать часть (перенести
    последнюю секцию в следующую часть) и сохранить TOC; выбрасываем его
    насовсем только когда сжимать больше некуда. Полный тест алгоритма —
    tests/test_v3_audit_r19_toc_shrink_rebalance.py."""
    src = __import__("pathlib").Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "while len(part_secs) > 1:" in src
    assert "include_outline=False" in src
    assert "content-size fallback" in src
