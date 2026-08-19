from __future__ import annotations

import inspect
from pathlib import Path

from services import gemini_analyze
from services import telegraph_pages


def test_primary_audio_emergency_env_cannot_enable_lite(monkeypatch):
    monkeypatch.setenv("AUDIO_ANALYSIS_FALLBACK_MODE", "lite")
    assert gemini_analyze._audio_fallback_models("gemini-3.7-flash") == [
        "gemini-3.7-flash"
    ]


def test_telegraph_semantic_fallback_is_disabled_by_default():
    parameter = inspect.signature(telegraph_pages._gemini_text_request).parameters[
        "allow_model_fallback"
    ]
    assert parameter.default is False


def test_old_process_global_fallback_bookkeeping_is_removed():
    telegraph = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    pipeline = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    caption = Path("converters/caption.py").read_text(encoding="utf-8")
    assert "_gemini_last_was_fallback" not in telegraph
    assert "_gemini_last_was_fallback" not in pipeline
    assert "_gemini_was_fallback" not in caption
