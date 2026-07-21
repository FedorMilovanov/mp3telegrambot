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
    assert is_generic_question("Что утверждает материал?") is True
    assert is_generic_question("Какой ответ верен?") is True
    assert is_generic_question("Как шесть дней творения связаны с авторитетом Бытия?") is False


def test_question_quality_rejects_pious_but_empty_shells():
    weak = [
        "Как нам больше доверять Богу?",
        "Какие практические шаги мы можем сделать?",
        "Как эта истина может изменить нашу жизнь?",
        "Как нам возрастать в вере?",
        "Над чем нам стоит задуматься?",
        "Чему этот материал учит нас?",
    ]
    for question in weak:
        assert is_generic_question(question) is True, question
        assert question_is_usable(question) is False, question


def test_question_quality_preserves_truth_and_distinction_questions():
    strong = [
        "Какая истина о Божьем характере делает тревогу несовместимой с аргументом Матфея 6?",
        "Почему совершенный вид глагола здесь не доказывает мгновенность всего процесса освящения?",
        "Где ты называешь осторожностью то, что в этой сцене является страхом потерять репутацию?",
        "Как различие между оправданием и освящением меняет ответ на обвинение совести?",
    ]
    for question in strong:
        assert is_generic_question(question) is False, question
        assert question_is_usable(question) is True, question


def test_normalize_question_text_and_usable_contract():
    q = normalize_question_text("🟢 Как шесть дней творения связаны с авторитетом Писания")
    assert q == "Как шесть дней творения связаны с авторитетом Писания?"
    assert question_is_usable(q) is True
    assert question_is_usable("Как это применить?") is False
    assert question_key(q) == question_key("Как шесть дней творения связаны с авторитетом Писания?")
