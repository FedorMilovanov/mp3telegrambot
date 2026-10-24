"""Regression tests for v3 patch 62 — safe public title fallback on title-topic mismatch."""

from pathlib import Path

from core.title_topic_audit import choose_safe_public_title


def test_choose_safe_public_title_uses_fallback_only_when_warning_present():
    ai = {"real_title": "Святой Дух Откровения", "title_topic_warning": "low overlap", "main_topic": "Пастырская верность и библейская экклезиология"}
    assert choose_safe_public_title(ai, "Пастырская верность и библейская экклезиология") == (
        "Пастырская верность и библейская экклезиология"
    )
    assert choose_safe_public_title({"real_title": "Святой Дух Откровения"}, "Fallback") == "Святой Дух Откровения"


def test_analysis_stage_applies_title_topic_fallback_before_publication():
    """Подмена заголовка происходит в стадии анализа, до конспекта.

    После выноса стадии (P0-3) проверка идёт по её файлу: поведение закреплено
    поведенческими тестами в tests/test_analysis_stage.py, а здесь — что
    пайплайн не завёл вторую копию решения.
    """
    stage = Path("pipelines/analysis_stage.py").read_text(encoding="utf-8")
    assert "from core.title_topic_audit import choose_safe_public_title" in stage
    assert 'if not ai_data.get("title_topic_warning")' in stage
    assert "using fallback title for publication" in stage
    assert 'return {**ai_data, "real_title": safe_title}, True' in stage

    pipeline = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "run_analysis(" in pipeline
    assert 'if ai_data.get("title_topic_warning")' not in pipeline
