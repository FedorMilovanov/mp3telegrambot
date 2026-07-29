from __future__ import annotations

from pathlib import Path

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


def test_decimal_money_is_one_exact_value_not_two_numbers() -> None:
    assert numbers.normalize_numeric_text("Цена $5.50.") == (
        "Цена пять долларов пятьдесят центов."
    )
    assert numbers.normalize_numeric_text("Цена 12,05 ₽.") == (
        "Цена двенадцать рублей пять копеек."
    )
    groups = numbers.numeric_anchor_groups("Цена $5.50.")
    assert groups == [["пять долларов пятьдесят центов"]]


def test_decimal_percent_is_consumed_before_plain_decimal() -> None:
    assert numbers.normalize_numeric_text("Рост 3,5%.") == (
        "Рост три целых пять десятых процента."
    )
    groups = numbers.numeric_anchor_groups("Рост 3,5%.")
    assert len(groups) == 1
    assert "три с половиной процента" in groups[0]


def test_date_is_consumed_before_decimal_normalization() -> None:
    assert numbers.normalize_numeric_text("Встреча 29.07.2026.") == (
        "Встреча двадцать девятого июля две тысячи двадцать шестого года."
    )
    groups = numbers.numeric_anchor_groups("Встреча 29.07.2026.")
    assert len(groups) == 1
    assert any("двадцать девятого июля" in variant for variant in groups[0])
    assert any("двадцать девять июля" in variant for variant in groups[0])


def test_correct_spoken_number_rescues_numeric_target() -> None:
    spoken, result = qa._numeric_semantic_target(
        "У нас 3 книги.",
        _auto("у нас три книги"),
    )
    assert spoken == "У нас три книги."
    assert result["passed"] is True
    assert result["numeric_normalization_rescued"] is True
    assert result["numeric_anchors_passed"] is True
    assert result["numeric_target_original"] == "У нас 3 книги."


def test_wrong_spoken_number_does_not_pass_despite_fuzzy_similarity() -> None:
    spoken, result = qa._numeric_semantic_target(
        "У нас 3 книги.",
        _auto("у нас пять книг"),
    )
    assert spoken == "У нас три книги."
    assert result["sequence_similarity"] > 0.54
    assert result["numeric_anchors_passed"] is False
    assert result["passed"] is False
    assert result["numeric_normalization_rescued"] is False


def test_decimal_money_change_does_not_pass() -> None:
    _spoken, result = qa._numeric_semantic_target(
        "Цена $5.50.",
        _auto("цена пять долларов пятнадцать центов"),
    )
    assert result["numeric_anchors_passed"] is False
    assert result["passed"] is False


def test_one_day_date_change_does_not_pass_high_string_similarity() -> None:
    _spoken, result = qa._numeric_semantic_target(
        "Встреча 29.07.2026.",
        _auto("встреча двадцать восьмого июля две тысячи двадцать шестого года"),
    )
    assert result["sequence_similarity"] > 0.80
    assert result["numeric_anchors_passed"] is False
    assert result["passed"] is False


def test_foreign_audio_is_not_rescued_by_numeric_target() -> None:
    _spoken, result = qa._numeric_semantic_target(
        "У нас 3 книги.",
        _auto("we have three books", language="en", probability=0.99),
    )
    assert result["passed"] is False
    assert result["foreign_language"] is True


def test_forced_russian_cannot_change_numeric_value(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = "У нас три книги."
    groups = numbers.numeric_anchor_groups("У нас 3 книги.")
    monkeypatch.setattr(
        qa.semantic_tts_guard_v4.legacy,
        "_transcribe",
        lambda _clip, *, language=None: ("у нас пять книг", "ru", 0.99),
    )
    result = qa._forced_russian_fallback(
        tmp_path / "clip.wav",
        target,
        _auto("у нас пять книг"),
        numeric_anchor_groups=groups,
    )
    assert result["forced_russian"]["sequence_similarity"] > 0.54
    assert result["forced_russian"]["numeric_anchors_passed"] is False
    assert result["forced_russian_rescued"] is False
    assert result["passed"] is False


def test_text_without_numbers_keeps_original_semantic_object() -> None:
    original = _auto("обычная фраза")
    spoken, result = qa._numeric_semantic_target("Обычная фраза.", original)
    assert spoken == "Обычная фраза."
    assert result == original
