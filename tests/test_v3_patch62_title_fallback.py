"""Regression tests for v3 patch 62 — safe public title fallback on title-topic mismatch."""

from pathlib import Path

from core.title_topic_audit import choose_safe_public_title


def test_choose_safe_public_title_uses_fallback_only_when_warning_present():
    ai = {"real_title": "Святой Дух Откровения", "title_topic_warning": "low overlap"}
    assert choose_safe_public_title(ai, "Пастырская верность и библейская экклезиология") == (
        "Пастырская верность и библейская экклезиология"
    )
    assert choose_safe_public_title({"real_title": "Святой Дух Откровения"}, "Fallback") == "Святой Дух Откровения"


def test_main_pipeline_applies_title_topic_fallback_before_publication():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "from core.title_topic_audit import choose_safe_public_title" in src
    assert 'if ai_data.get("title_topic_warning")' in src
    assert "using fallback title for publication" in src
    assert 'ai_data = {**ai_data, "real_title": _safe_title}' in src
