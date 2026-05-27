"""Regression tests for v3 patch 40 — subtitle hints and short boundary padding."""

from pathlib import Path

from core.database import SETTINGS_DEFAULTS, SETTINGS_LABELS
from services.shorts_video import build_whisper_initial_prompt, _extract_subtitle_hint_terms, _polish_subtitle_text


def test_subtitle_settings_are_declared():
    assert SETTINGS_DEFAULTS["shorts_subtitles_gemini_hints"] is True
    assert SETTINGS_DEFAULTS["shorts_boundary_padding"] is True
    assert SETTINGS_LABELS["shorts_subtitles_gemini_hints"] == "🧠 Gemini hints"
    assert SETTINGS_LABELS["shorts_boundary_padding"] == "↔️ Запас краёв"


def test_whisper_initial_prompt_uses_gemini_terms_without_using_generated_subtitles():
    ai = {
        "real_author": "Джон МакАртур",
        "real_title": "Презренный Раб Иеговы",
        "whisper_hints": ["Исаия 53", "Раб Иеговы"],
        "terms_data": {
            "concepts": ["Заместительное искупление||..."],
            "scripture": ["Исаия 53:1–3||..."],
            "translations": [],
            "lexicon_notes": ["Ис. 53||эвед||עבד||..."],
        },
    }
    hints = _extract_subtitle_hint_terms(ai)
    assert "Джон МакАртур" in hints
    assert "Исаия 53" in hints
    assert "Заместительное искупление" in hints
    prompt = build_whisper_initial_prompt(ai)
    assert "Ключевые слова и имена" in prompt
    assert "Раб Иеговы" in prompt
    assert "Сохраняй богословские термины" in prompt
    assert build_whisper_initial_prompt(ai, use_gemini_hints=False).count("Ключевые слова") == 0


def test_subtitle_text_polish_applies_common_typos_and_spacing():
    assert _polish_subtitle_text(" Странный огонь , МакАртора . ") == "Чуждый огонь, МакАртура."


def test_shorts_pipeline_wires_boundary_padding_and_subtitle_hints():
    shorts = Path("pipelines/shorts.py").read_text(encoding="utf-8")
    video = Path("services/shorts_video.py").read_text(encoding="utf-8")
    assert 'await asettings_get("shorts_boundary_padding")' in shorts
    assert "SHORTS_PREROLL_SECONDS" in shorts
    assert "SHORTS_POSTROLL_SECONDS" in shorts
    assert "boundary_pad" in shorts
    assert 'settings_get("shorts_subtitles_gemini_hints")' in video
    assert "build_whisper_initial_prompt" in video
    assert "Gemini is used only as vocabulary/context hints" in video
