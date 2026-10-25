"""Regression tests for v3 patch 74 — surgical runtime regressions."""

from pathlib import Path

from converters.md_telegraph import _postprocess_telegraph_nodes
from core.json_parser import _parse_gemini_response
from core.synopsis_quality import synopsis_density_score
from core.title_topic_audit import audit_title_topic_consistency
from core.globals import _is_gemini_3x


def test_json_parser_recovers_truncated_object_without_final_top_level_brace():
    raw = '''```json
{
  "real_author": "A",
  "real_title": "T",
  "real_event": "",
  "format": "sermon",
  "main_topic": "Topic",
  "timestamps": [
    {"time":"0:00", "topic":"Start"},
    {"time":"10:00", "topic":"Middle"}
'''
    parsed = _parse_gemini_response(raw, duration=3600)
    assert parsed is not None
    assert parsed["real_author"] == "A"
    assert "0:00 Start" in parsed["timestamps"]
    # Canonical defaults survive recovery even though truncated JSON lacked them.
    assert parsed["questions"] == []
    assert parsed["whisper_hints"] == []
    assert parsed["hermeneutic_method"] == "mixed"


def test_postprocess_double_slash_preserves_urls_and_paragraph_boundaries():
    nodes = [{"tag": "p", "children": ["См. https://example.com/a / / Новый абзац"]}]
    out = _postprocess_telegraph_nodes(nodes)
    text = out[0]["children"][0]
    assert "https://example.com/a" in text
    assert "a\n\nНовый абзац" in text


def test_synopsis_density_score_does_not_let_coverage_beat_deep_content():
    deep = [{"title": "A", "time": "0:00", "content": "x" * 3000}]
    thin_late = [
        {"title": "A", "time": "0:00", "content": "x" * 200},
        {"title": "B", "time": "55:00", "content": "y" * 200},
    ]
    assert synopsis_density_score(deep, 3600) > synopsis_density_score(thin_late, 3600)


def test_title_topic_audit_warns_when_only_one_weak_overlap_exists():
    issue = audit_title_topic_consistency(
        "Святой Дух Откровения",
        "Пастырская верность и церковная дисциплина. Дух служителя проявляется в терпении.",
        [],
    )
    assert issue is not None
    assert issue.code == "title_topic_low_overlap"


def test_gemini_3x_detection_is_exact_model_family():
    assert _is_gemini_3x("gemini-3.5-flash") is True
    assert _is_gemini_3x("gemini-3-flash-preview") is True
    # Gemini 4 also uses thinking config and must not receive temperature.
    assert _is_gemini_3x("gemini-4-argon") is True
    assert _is_gemini_3x("my-gemini-3-proxy") is False
    assert _is_gemini_3x("notgemini-3.5") is False
    assert _is_gemini_3x("gemini-40-argon") is False


def test_content_audit_logging_uses_explicit_ternary_not_short_circuit():
    pages = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    telegraph = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "and logger.warning or logger.info" not in pages
    assert "and logger.warning or logger.info" not in telegraph
    assert "logger.warning if has_content_audit_warnings" in pages
    assert "logger.warning if has_content_audit_warnings" in telegraph


def test_cache_caption_branch_no_longer_deletes_timestamps_as_first_fallback():
    # P0-3, шаг 5: обрезка подписи вынесена в общую стадию. Кэш-хит больше не
    # содержит своей копии алгоритма — значит и «сначала удалить таймкоды»
    # вернуться не может: порядок шагов один на оба пути.
    stage = Path("pipelines/caption_stage.py").read_text(encoding="utf-8")
    assert "for mini_limit in MINI_TIMESTAMP_LIMITS:" in stage
    assert stage.index("MINI_TIMESTAMP_LIMITS:") < stage.index('"timestamps": "",')

    presentation = Path("pipelines/cached_presentation_stage.py").read_text(encoding="utf-8")
    assert "fit_caption(" in presentation, "кэш-хит обязан использовать общую стадию подписи"
    assert "_trim_timestamps(" not in presentation, "своя копия обрезки в кэш-хите вернулась"

    main_src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "await prepare_cached_presentation(" in main_src
