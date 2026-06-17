from core.question_quality import (
    ensure_question_mark,
    is_generic_question,
    is_question_like,
    normalize_question_text,
    question_is_usable,
    question_key,
)


def test_question_quality_repairs_question_mark_for_question_like_start():
    assert ensure_question_mark("Как этот аргумент проверяет мою веру") == "Как этот аргумент проверяет мою веру?"
    assert is_question_like("Почему это важно") is True
    assert is_question_like("Это утверждение") is False


def test_question_quality_rejects_generic_questions():
    assert is_generic_question("Как это применить?") is True
    assert is_generic_question("Что это значит для меня?") is True
    assert is_generic_question("Как шесть дней творения связаны с авторитетом Бытия?") is False


def test_normalize_question_text_and_usable_contract():
    q = normalize_question_text("🟢 Как шесть дней творения связаны с авторитетом Писания")
    assert q == "Как шесть дней творения связаны с авторитетом Писания?"
    assert question_is_usable(q) is True
    assert question_is_usable("Как это применить?") is False
    assert question_key(q) == question_key("Как шесть дней творения связаны с авторитетом Писания?")
