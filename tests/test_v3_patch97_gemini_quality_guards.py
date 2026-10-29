import pytest
from pathlib import Path


def test_audio_analysis_is_primary_model_only_even_with_legacy_lite_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import gemini_analyze

    monkeypatch.delenv("AUDIO_ANALYSIS_FALLBACK_MODE", raising=False)
    assert gemini_analyze._audio_fallback_models("gemini-3.7-flash") == ["gemini-3.7-flash"]

    monkeypatch.setenv("AUDIO_ANALYSIS_FALLBACK_MODE", "lite")
    assert gemini_analyze._audio_fallback_models("gemini-3.7-flash") == ["gemini-3.7-flash"]


def test_audio_and_telegraph_quota_are_not_model_global() -> None:
    """Gemini API limits are project-scoped, not model-global across keys."""
    audio_src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    pages_src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")

    assert "mark_model_exhausted" not in audio_src
    assert "limits are project-scoped" in audio_src
    assert "mark_model_exhausted" not in pages_src
    assert "is_model_exhausted" not in pages_src
    assert "gemini_generate(" in pages_src


def test_pipeline_skips_ai_pages_when_audio_analysis_missing() -> None:
    """Без анализа AI-страницы не публикуются — иначе конспект был бы выдуман.

    Раньше пять флагов гасились прямо в теле пайплайна (`_feat_* = False`).
    Теперь это решение живёт в `pipelines/publication_plan.py`, поэтому проверка
    идёт по контракту: пайплайн использует план, а план без анализа пуст
    (матрица — в `tests/test_publication_plan.py`).
    """
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    context_src = Path("pipelines/publication_context_stage.py").read_text(encoding="utf-8")
    assert "prepare_publication_context(" in src
    assert "AI analysis is missing/invalid" in context_src
    assert "build_plan(_all_settings, ai_data=ai_data)" in context_src
    compact_src = "".join(src.split())
    assert "_feat_study_analysis=_plan.study_analysis" in compact_src
    assert "_feat_reflection_application=_plan.reflection_application" in compact_src
    plan_src = Path("pipelines/publication_plan.py").read_text(encoding="utf-8")
    assert "if not analysis_available:" in plan_src


def test_short_rutube_matches_require_stricter_score() -> None:
    src = Path("services/search.py").read_text(encoding="utf-8")
    assert "duration and duration < 300" in src
    assert "0.75" in src
