"""Collect the retained LiveDub QA suite with current production contracts.

The historical suite is kept in ``livedub_qa_cases.py`` so its large body stays
byte-for-byte stable. This collector replaces only obsolete assertions whose
production contract intentionally changed: the old mode registry, old semantic
fallbacks, the retired verbose LiveDub info presentation, retired Gemini model
contracts, and the retired Deno-first yt-dlp runtime policy.
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
    "test_eng_fast_qa_mode_registered_and_wired",
    "test_env_example_gemini_model_is_not_truncated",
    "test_livedub_light_model_default_fallbacks_are_alive_models",
    # The GEMINI_LIGHT_* knobs were deleted (nothing read them); the legacy
    # case asserts their presence in .env.example, so it is replaced below.
    "test_livedub_light_model_env_documented",
    "test_livedub_info_card_module_contract",
    "test_livedub_info_youtube_description_contains_original_link",
    "test_livedub_info_message_escapes_html",
    "test_livedub_info_message_uses_safe_html_trim",
    "test_vot_token_is_documented_in_readme_help_and_status",
    "test_ytdlp_js_runtime_version_filter",
    # P0-3: title implementation and HTML delivery now have separate source
    # owners; the two-module replacement lives in test_livedub_title.py.
    "test_livedub_send_video_caption_includes_title_and_html_parse_mode",
    # P0-3: ENG Quick finalization now has its own source owner; replacements
    # live in test_livedub_quick_stage.py beside the executed behavior tests.
    "test_eng_quick_never_silent",
    "test_auth_required_marker_and_user_hint",
    # P0-3: свежая отправка MP3/PDF вынесена в отдельную стадию.
    "test_upload_retry_honors_retry_after",
    "test_mp3_chapters_wired_both_branches",
    "test_audio_file_id_saved_on_first_send",
    "test_cache_mp3_resend_wiring",
    "test_chat_actions_during_uploads",
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


def test_eng_fast_qa_mode_registered_and_wired_for_gemini_38() -> None:
    mode = Path("handlers/mode_command.py").read_text(encoding="utf-8")
    assert "eng_fast_qa" in mode
    assert "⚡🔍 ENG Quick QA" in mode
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "LIVEDUB_QUICK_QA_MAX_DURATION" in pipe
    assert "LIVEDUB_QUICK_QA_MODEL" in pipe
    assert "LIVEDUB_QUICK_QA_THINKING" in pipe
    assert "LiveDubQuickQA" in pipe
    assert "apply_qa_audio_fixes" in pipe
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "LIVEDUB_QUICK_QA_MAX_DURATION=120" in env
    from core.model_catalog import MODEL_PRIMARY

    assert f"LIVEDUB_QUICK_QA_MODEL={MODEL_PRIMARY}" in env
    assert "LIVEDUB_QUICK_QA_THINKING=high" in env


def test_env_example_gemini_38_model_is_not_truncated() -> None:
    """Copying .env.example must select the exact production primary ID."""
    from core.model_catalog import MODEL_PRIMARY

    env = Path(".env.example").read_text(encoding="utf-8")
    assert f"GEMINI_MODEL={MODEL_PRIMARY}" in env
    assert f"GEMINI_MODEL={MODEL_PRIMARY[:-1]}\n" not in env


def test_livedub_light_model_env_documented() -> None:
    """Utility-модель задаётся ролью каталога, а не мёртвыми GEMINI_LIGHT_*.

    Историческая ручка GEMINI_LIGHT_MODEL не читалась кодом (маршрут берётся
    из `core.model_catalog`), а стартовая политика её перезаписывала — то есть
    она была декоративной. Имена удалены из кода и из документации; тест
    фиксирует именно это, чтобы они не вернулись как «настройки».
    """
    from core.model_catalog import MODEL_UTILITY

    env = Path(".env.example").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    assert f"MP3BOT_UTILITY_MODEL={MODEL_UTILITY}" in env
    assert "LIVEDUB_INFO_CARD=1" in env
    for removed in (
        "GEMINI_LIGHT_MODEL=",
        "GEMINI_LIGHT_FALLBACK_MODELS=",
        "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=",
    ):
        assert removed not in env, f"{removed} вернулся в .env.example"
        assert removed not in readme, f"{removed} вернулся в README"


def test_livedub_info_semantic_route_has_no_35_model_fallbacks(monkeypatch) -> None:
    from core.model_catalog import MODEL_PRIMARY
    from services.livedub_info import get_light_model, get_light_model_fallbacks

    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_INFO_FALLBACK_MODELS", "gemini-3.5-flash")
    monkeypatch.setenv("MP3BOT_UTILITY_MODEL", "gemini-3.5-flash-lite")

    assert get_light_model() == MODEL_PRIMARY
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


def test_upload_retry_honors_retry_after() -> None:
    stage = Path("pipelines/delivery_stage.py").read_text(encoding="utf-8")
    assert stage.count('getattr(upload_err, "retry_after", None)') == 1


def test_mp3_chapters_wired_both_branches() -> None:
    main = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = Path("pipelines/delivery_stage.py").read_text(encoding="utf-8")
    assert "deps.embed_chapters(" in stage
    assert "await deliver_result(" in main
    assert "await deliver_cached_result(" in main


def test_audio_file_id_saved_on_first_send() -> None:
    main = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = Path("pipelines/delivery_stage.py").read_text(encoding="utf-8")
    assert "await deps.save_file_id(request.media_id, file_id)" in stage
    assert "await deliver_result(" in main
    assert "await deliver_cached_result(" in main


def test_cache_mp3_resend_wiring() -> None:
    main = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = Path("pipelines/delivery_stage.py").read_text(encoding="utf-8")
    assert 'cached_file_id=(cached or {}).get("audio_file_id")' in main
    assert "file_id протух" in stage
    assert 'await deps.save_file_id(request.media_id, "")' in stage


def test_chat_actions_during_uploads() -> None:
    main = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = Path("pipelines/delivery_stage.py").read_text(encoding="utf-8")
    assert 'send_action("upload_voice")' in stage
    assert 'action="upload_video"' in main
