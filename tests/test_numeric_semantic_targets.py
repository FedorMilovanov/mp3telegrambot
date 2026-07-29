from __future__ import annotations

from tools.voxcpm2 import professional_audio_qa_v45 as qa
from tools.voxcpm2 import russian_spoken_numbers as numbers


def _auto(heard: str, *, language: str = "ru", probability: float = 0.98):
    return qa.semantic_tts_guard_v4.legacy.compare_spoken_text(
        "заведомо другой текст",
        heard,
        language,
        probability,
    )


def test_integer_to_words_preserves_exact_value() -> None:
    assert numbers.integer_to_words(3) == "три"
    assert numbers.integer_to_words(2026) == "две тысячи двадцать шесть"
    assert numbers.integer_to_words(-15) == "минус пятнадцать"


def test_numeric_text_normalizes_percent_currency_and_decimal() -> None:
    assert numbers.normalize_numeric_text("Рост 15%.") == "Рост пятнадцать процентов."
    assert numbers.normalize_numeric_text("Цена $5.") == "Цена пять долларов."
    assert numbers.normalize_numeric_text("Значение 3,5.") == (
        "Значение три целых пять десятых."
    )


def test_correct_spoken_number_rescues_numeric_target() -> None:
    spoken, result = qa._numeric_semantic_target(
        "У нас 3 книги.",
        _auto("у нас три книги"),
    )
    assert spoken == "У нас три книги."
    assert result["passed"] is True
    assert result["numeric_normalization_rescued"] is True
    assert result["numeric_target_original"] == "У нас 3 книги."


def test_wrong_spoken_number_does_not_pass() -> None:
    spoken, result = qa._numeric_semantic_target(
        "У нас 3 книги.",
        _auto("у нас пять книг"),
    )
    assert spoken == "У нас три книги."
    assert result["passed"] is False
    assert result["numeric_normalization_rescued"] is False


def test_foreign_audio_is_not_rescued_by_numeric_target() -> None:
    _spoken, result = qa._numeric_semantic_target(
        "У нас 3 книги.",
        _auto("we have three books", language="en", probability=0.99),
    )
    assert result["passed"] is False
    assert result["foreign_language"] is True


def test_text_without_numbers_keeps_original_semantic_object() -> None:
    original = _auto("обычная фраза")
    spoken, result = qa._numeric_semantic_target("Обычная фраза.", original)
    assert spoken == "Обычная фраза."
    assert result == original
