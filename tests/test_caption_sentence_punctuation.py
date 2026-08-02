from converters.caption import (
    _polish_caption_sentence_punctuation,
    build_caption,
)


def test_sentence_punctuation_moves_before_trailing_emoji() -> None:
    assert _polish_caption_sentence_punctuation(
        "готовности к духовным испытаниям ⚔️."
    ) == "готовности к духовным испытаниям. ⚔️"


def test_question_and_exclamation_move_before_trailing_emoji() -> None:
    assert _polish_caption_sentence_punctuation("Готов ли ты 🕊️?") == "Готов ли ты? 🕊️"
    assert _polish_caption_sentence_punctuation("Бодрствуйте ⚔️!") == "Бодрствуйте! ⚔️"


def test_emoji_inside_sentence_and_plain_punctuation_are_unchanged() -> None:
    assert _polish_caption_sentence_punctuation("⚔️ Боритесь за веру.") == "⚔️ Боритесь за веру."
    assert _polish_caption_sentence_punctuation("Слово (лат.).") == "Слово (лат.)."


def test_caption_polishes_main_topic_and_timestamp_topics() -> None:
    caption = build_caption(
        performer="Пол Вошер",
        title="Люди Слова",
        duration=60,
        file_size_mb=1.0,
        ai_data={
            "real_author": "Пол Вошер",
            "real_title": "Люди Слова",
            "main_topic": "Готовность к духовным испытаниям ⚔️.",
            "timestamps": "0:00 Бодрствуйте ⚔️!",
        },
    )
    assert "Готовность к духовным испытаниям. ⚔️" in caption
    assert "0:00 Бодрствуйте! ⚔️" in caption
    assert "⚔️." not in caption
