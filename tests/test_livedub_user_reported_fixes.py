from services.livedub_output_policy import (
    _clean_audio_caption,
    _clean_video_caption,
    _russian_heading_case,
    _split_title_author,
)
from services.livedub_qa_trust import (
    _confirmed_result,
    _issues_match,
    _unconfirmed_failure_result,
)


def test_livedub_video_caption_hides_provider_label():
    caption = (
        "<b>Борьба с Сексуальной Распущенностью и Порнографией - Тим Конвей</b>\n"
        "🎬 Живые голоса Яндекса"
    )
    cleaned = _clean_video_caption(caption)
    assert "Живые голоса Яндекса" not in cleaned
    assert "Тим Конвей" in cleaned


def test_livedub_audio_caption_is_neutral():
    assert _clean_audio_caption(
        "🎧 Чистая аудиодорожка русского перевода Яндекса"
    ) == "🎧 Русская аудиоверсия"


def test_tim_conway_is_split_and_canonicalized():
    title, author = _split_title_author(
        "The Battle Against Sexual Immorality & Pornography - Tim Conway"
    )
    assert title == "The Battle Against Sexual Immorality & Pornography"
    assert author == "Тим Конвей"


def test_russian_heading_keeps_prepositions_lowercase():
    assert _russian_heading_case(
        "Борьба С Сексуальной Распущенностью И Порнографией"
    ) == "Борьба с Сексуальной Распущенностью и Порнографией"


def _issue(time: str, heard: str, problem: str, severity: str = "major") -> dict:
    return {
        "time": time,
        "heard": heard,
        "problem": problem,
        "should_be": "Исправленный смысл фразы",
        "severity": severity,
    }


def test_qa_confirmation_requires_time_and_lexical_agreement():
    first = _issue("18:32", "Если твой правый глаз соблазняет тебя", "Неверная цитата")
    same = _issue("18:35", "Если твой правый глаз соблазняет тебя", "Ошибка в цитате")
    unrelated = _issue("18:34", "Совсем другая фраза о другом вопросе", "Иная проблема")
    assert _issues_match(first, same)
    assert not _issues_match(first, unrelated)


def test_only_confirmed_qa_issues_survive_and_major_needs_two_major_votes():
    confirmed = _issue("18:32", "правый глаз соблазняет тебя", "ошибка в цитате", "major")
    rejected = _issue("21:00", "несуществующая фраза", "ложная тревога", "major")
    validation = _issue("18:36", "правый глаз соблазняет тебя", "ошибка цитирования", "minor")

    result = _confirmed_result(
        {"score": 70, "issues": [confirmed, rejected]},
        {"score": 90, "issues": [validation]},
    )

    assert len(result["issues"]) == 1
    assert result["issues"][0]["severity"] == "minor"
    assert result["_qa_unconfirmed_dropped"] == 1


def test_failed_confirmation_does_not_publish_or_autofix_candidates():
    result = _unconfirmed_failure_result(
        {"score": 60, "issues": [_issue("01:00", "фраза", "возможная ошибка")]}
    )
    assert result["issues"] == []
    assert "score" not in result
    assert result["_qa_confirmation_failed"] is True
