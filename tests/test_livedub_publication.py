from services.livedub_publication import (
    _canonical_title,
    _fallback_description,
    format_audio_caption,
    format_video_caption,
)
from services.livedub_qa_trust import _candidate_windows, _confirmed_result


def test_russian_publication_title_uses_project_title_case():
    assert _canonical_title(
        "борьба с сексуальной безнравственностью и порнографией"
    ) == "Борьба с Сексуальной Безнравственностью и Порнографией"


def test_audio_caption_has_description_and_source_but_no_language_label():
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


def test_video_caption_is_one_polished_card_without_provider_label():
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


def test_fallback_description_does_not_invent_content_beyond_title():
    text = _fallback_description("Как Побеждать Искушение", "Тим Конвей")
    assert "Как Побеждать Искушение" in text
    assert "Тим Конвей" in text
    assert "стих" not in text.lower()


def test_candidate_windows_merge_nearby_findings_and_cover_later_one():
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


def test_focused_confirmation_drops_same_time_but_unrelated_wording():
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
