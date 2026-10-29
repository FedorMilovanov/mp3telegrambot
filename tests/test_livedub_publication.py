import pytest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from services import livedub_info_presentation_policy as info_policy
from services import livedub_publication as publication
from services import livedub_publication_core as publication_core
from services.livedub_publication import (
    _canonical_title,
    _fallback_description,
    format_audio_caption,
    format_video_caption,
)
from services.livedub_qa_trust import _candidate_windows, _confirmed_result


def test_russian_publication_title_uses_project_title_case() -> None:
    assert (
        _canonical_title("борьба с сексуальной безнравственностью и порнографией")
        == "Борьба с Сексуальной Безнравственностью и Порнографией"
    )


def test_shared_title_parser_failure_uses_local_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_parser(_value: str) -> None:
        raise RuntimeError("shared parser unavailable")

    monkeypatch.setattr("services.livedub_output_policy._split_title_author", fail_parser)

    assert publication_core.split_title_author("Title - Author") == ("Title", "Author")


def test_title_normalizer_failure_uses_local_title_case(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_normalizer(_value: str) -> None:
        raise RuntimeError("normalizer unavailable")

    monkeypatch.setattr("core.text_utils.normalize_title_text", fail_normalizer)

    assert publication_core.canonical_title("борьба с искушением") == "Борьба с Искушением"


def _patch_malformed_gemini_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.globals.GEMINI_CLIENTS", [object()])
    monkeypatch.setattr("core.globals.make_text_config_smart", lambda **_kwargs: object())

    async def malformed_response(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(text="not-json")

    monkeypatch.setattr("services.gemini_router.gemini_generate", malformed_response)


def test_malformed_second_chance_title_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_malformed_gemini_response(monkeypatch)

    assert asyncio.run(info_policy._translate_title_second_chance("Title - Author")) is None


def test_malformed_publication_metadata_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_malformed_gemini_response(monkeypatch)

    assert asyncio.run(publication._generate_quality_publication("Title - Author")) is None


def test_audio_caption_has_description_and_source_but_no_language_label() -> None:
    caption = format_audio_caption(
        {
            "title": "Борьба с Сексуальной Безнравственностью и Порнографией",
            "author": "Тим Конвей",
            "description": "Тим Конвей последовательно раскрывает главную тему и её практическое значение.",
            "source_url": "https://youtu.be/example",
        }
    )
    assert "🎙️" in caption
    assert "Оригинал видео" in caption
    assert "Русская аудиоверсия" not in caption
    assert "Яндекс" not in caption


def test_video_caption_is_one_polished_card_without_provider_label() -> None:
    original = (
        "<b>The Battle Against Sexual Immorality & Pornography - Tim Conway</b>\n"
        "🎬 Живые голоса Яндекса\n"
        "🩹 В 1 месте русский дубляж приглушён"
    )
    caption = format_video_caption(
        {
            "title": "Борьба с Сексуальной Безнравственностью и Порнографией",
            "author": "Тим Конвей",
            "description": "Тим Конвей рассматривает заявленную тему и последовательно раскрывает основные мысли.",
            "source_url": "https://youtu.be/example",
        },
        original,
    )
    assert "<b>Борьба с Сексуальной Безнравственностью и Порнографией - Тим Конвей</b>" in caption
    assert "🎙️" in caption
    assert "Оригинал видео" in caption
    assert "🩹" in caption
    assert "Живые голоса Яндекса" not in caption


def test_fallback_description_is_metadata_only_and_does_not_invent_content() -> None:
    text = _fallback_description("Как Побеждать Искушение", "Тим Конвей")
    assert "Как Побеждать Искушение" in text
    assert "Тим Конвей" in text
    assert "стих" not in text.lower()
    assert "раскрывает" not in text.lower()
    assert "последовательно" not in text.lower()


def test_inline_publication_generator_is_sampling_free_36_high() -> None:
    src = Path("services/livedub_publication.py").read_text(encoding="utf-8")
    assert "_generate_quality_publication" in src
    assert "DEFAULT_INFO_MODEL" in src
    assert 'thinking_level="high"' in src
    assert "temperature=" not in src
    assert 'thinking_level="minimal"' not in src
    assert "_generate_light_publication" not in src


def test_candidate_windows_merge_nearby_findings_and_cover_later_one() -> None:
    issues = [
        {"time": "18:32"},
        {"time": "18:54"},
        {"time": "21:10"},
    ]
    windows = _candidate_windows(
        issues,
        1800,
        max_issues=8,
        before_sec=14,
        after_sec=30,
    )
    assert len(windows) == 2
    first_start, first_length = windows[0]
    assert first_start <= 18 * 60 + 32 <= first_start + first_length
    assert first_start <= 18 * 60 + 54 <= first_start + first_length


def test_focused_confirmation_drops_same_time_but_unrelated_wording() -> None:
    primary = {
        "score": 70,
        "issues": [
            {
                "time": "18:32",
                "heard": "если твой правый глаз соблазняет тебя",
                "problem": "неверная цитата",
                "should_be": "другая формулировка",
                "severity": "major",
            }
        ],
    }
    validation = {
        "issues": [
            {
                "time": "18:34",
                "heard": "совсем другая фраза о другом вопросе",
                "problem": "иная проблема",
                "should_be": "другая формулировка",
                "severity": "major",
            }
        ]
    }
    result = _confirmed_result(primary, validation)
    assert result["issues"] == []
    assert "score" not in result
