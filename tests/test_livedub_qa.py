"""Collect the retained LiveDub QA suite with current production contracts.

The historical suite is kept in ``livedub_qa_cases.py`` so its large body stays
byte-for-byte stable. This collector replaces only obsolete assertions whose
production contract intentionally changed: the old mode registry, old semantic
fallbacks, and the retired verbose LiveDub info presentation.
"""
from __future__ import annotations

from pathlib import Path
import runpy

from handlers.mode_command import (
    EDITORIAL_MODE,
    MODE_DESCRIPTIONS,
    MODE_LABELS,
    VALID_MODES,
)

_REPLACED_CASES = {
    "test_three_modes_defined",
    "test_livedub_light_model_default_fallbacks_are_alive_models",
    "test_livedub_info_card_module_contract",
    "test_livedub_info_youtube_description_contains_original_link",
    "test_livedub_info_message_escapes_html",
    "test_livedub_info_message_uses_safe_html_trim",
    "test_vot_token_is_documented_in_readme_help_and_status",
}
_CASES = runpy.run_path(str(Path(__file__).with_name("livedub_qa_cases.py")))
globals().update(
    {
        name: value
        for name, value in _CASES.items()
        if name.startswith("test_") and name not in _REPLACED_CASES
    }
)


def test_all_modes_defined() -> None:
    assert VALID_MODES == (
        "rus",
        "eng",
        "eng_fast",
        "eng_fast_qa",
        "shorts_max",
        EDITORIAL_MODE,
    )
    for mode in VALID_MODES:
        assert mode in MODE_LABELS
        assert mode in MODE_DESCRIPTIONS


def test_livedub_info_semantic_route_has_no_35_model_fallbacks(monkeypatch) -> None:
    from services.livedub_info import get_light_model, get_light_model_fallbacks

    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_INFO_FALLBACK_MODELS", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_LIGHT_FALLBACK_MODELS", "gemini-3.5-flash")

    assert get_light_model() == "gemini-3.6-flash"
    assert get_light_model_fallbacks() == []


def test_livedub_info_card_uses_concise_source_owned_presentation() -> None:
    from services.livedub_info import (
        _normalize_card,
        format_livedub_info_message,
        livedub_info_response_schema,
    )

    assert "telegram_description" in livedub_info_response_schema()["properties"]
    card = _normalize_card(
        {
            "telegram_description": "Короткое описание.",
            "youtube_title": "Название - Paul Washer",
            "youtube_description": "Описание YouTube.",
            "compact_subtitles": ["Тезис 1", "Тезис 2"],
            "hashtags": ["евангелие", "Paul Washer"],
        },
        "Fallback",
        source_url="https://youtu.be/original",
    )
    msg = format_livedub_info_message(card)
    assert "Готовое описание к переводу" not in msg
    assert "<b>Название</b>" in msg
    assert "Пол Вошер" in msg
    assert "Короткое описание." in msg
    assert "Оригинал на YouTube" in msg
    assert "https://youtu.be/original" in msg
    assert "Описание YouTube." not in msg


def test_livedub_info_message_escapes_only_visible_publication_fields() -> None:
    from services.livedub_info import format_livedub_info_message

    msg = format_livedub_info_message(
        {
            "telegram_description": "5 < 7 & важно",
            "youtube_title": "A < B",
            "youtube_description": "Use <tag> & quote",
            "compact_subtitles": ["x < y"],
            "hashtags": ["#ok"],
        }
    )
    assert "5 &lt; 7 &amp; важно" in msg
    assert "A &lt; B" in msg
    assert "Use &lt;tag&gt; &amp; quote" not in msg
    assert "x &lt; y" not in msg


def test_safe_html_trim_is_owned_by_presentation_policy() -> None:
    info = Path("services/livedub_info.py").read_text(encoding="utf-8")
    presentation = Path("services/livedub_info_presentation_policy.py").read_text(
        encoding="utf-8"
    )
    assert "presentation_policy.format_card_message(card)" in info
    assert "safe_trim_caption" in presentation
    assert "livedub_info_presentation.py" not in info


def test_vot_token_is_documented_in_readme_help_and_status() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    env = Path(".env.example").read_text(encoding="utf-8")
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")

    assert "VOT_API_TOKEN" in readme
    assert "LIVEDUB_TTS_FALLBACK=0" in readme
    assert "YTDLP_COOKIES_FROM_BROWSER" in env
    assert "YANDEX_OAUTH_TOKEN" in env
    assert "LIVEDUB_TITLE_TRANSLATE=1" in env
    assert "VOT_API_TOKEN" in commands
    assert "VOT_API_TOKEN/YANDEX_OAUTH_TOKEN" in commands
