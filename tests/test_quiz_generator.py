from services.quiz_generator import _format_ai_context, _parse_quiz_json, quiz_response_schema


def test_parse_quiz_json_requires_four_unique_options_and_valid_correct():
    raw = """
    [
      {"question":"Что утверждает материал?","options":["A","B","C","D"],"correct":1,"explanation":"Потому что таков ход аргумента."},
      {"question":"bad","options":["A","A","C","D"],"correct":0,"explanation":"x"},
      {"question":"bad2","options":["A","B","C"],"correct":0,"explanation":"x"},
      {"question":"bad3","options":["A","B","C","D"],"correct":9,"explanation":"x"}
    ]
    """
    parsed = _parse_quiz_json(raw)
    assert parsed == [{
        "question": "Что утверждает материал?",
        "options": ["A", "B", "C", "D"],
        "correct": 1,
        "explanation": "Потому что таков ход аргумента.",
    }]


def test_parse_quiz_json_dedupes_questions_and_trims_lengths():
    long_q = "В" * 400
    raw = str([
        {"question": long_q, "options": ["A" * 150, "B", "C", "D"], "correct": 0, "explanation": "E" * 250},
        {"question": long_q, "options": ["A", "B", "C", "D"], "correct": 0, "explanation": "dup"},
    ]).replace("'", '"')
    parsed = _parse_quiz_json(raw)
    assert parsed and len(parsed) == 1
    assert len(parsed[0]["question"]) <= 255
    assert len(parsed[0]["options"][0]) <= 100
    assert len(parsed[0]["explanation"]) <= 200


def test_quiz_context_includes_argument_terms_and_timestamps():
    ctx = _format_ai_context({
        "main_topic": "Главная тема",
        "analysis_summary": "Аналитика",
        "argument_arc": "Аргумент",
        "timestamps": "0:00 Начало\n10:00 Середина",
        "key_categories": ["Категория"],
        "terms_data": {"concepts": ["Термин || объяснение"], "scripture": ["Быт. 1:1 || роль"]},
        "questions": ["🟢 Вопрос?"],
    })
    for needle in ("Главная тема", "Аргумент", "0:00 Начало", "Категория", "Термин", "Быт. 1:1"):
        assert needle in ctx


def test_quiz_schema_requires_expected_fields():
    schema = quiz_response_schema()
    item = schema["items"]
    assert set(item["required"]) == {"question", "options", "correct", "explanation"}
    assert item["properties"]["options"]["type"] == "array"


def test_quiz_env_int_is_robust(monkeypatch):
    from services.quiz_generator import _env_int

    monkeypatch.setenv("QUIZ_QUESTION_COUNT", "not-an-int")
    assert _env_int("QUIZ_QUESTION_COUNT", 10) == 10
    monkeypatch.setenv("QUIZ_QUESTION_COUNT", "999")
    assert _env_int("QUIZ_QUESTION_COUNT", 10) == 20
    monkeypatch.setenv("QUIZ_QUESTION_COUNT", "0")
    assert _env_int("QUIZ_QUESTION_COUNT", 10) == 1


def test_quiz_prompt_avoids_literal_bad_third_person_example():
    from services.quiz_generator import QUIZ_PROMPT

    assert "автор показывает" not in QUIZ_PROMPT
    assert "роль/имя автора + показывает" in QUIZ_PROMPT


def test_parse_quiz_json_accepts_wrapped_questions_and_letter_or_text_answer():
    raw = """
    {"questions": [
      {"question":"Какой ответ верен?","options":["Первый","Второй","Третий","Четвёртый"],"correct":"B","explanation":"Второй вариант отражает аргумент."},
      {"question":"Какой текст ключевой?","options":["Бытие 1","Иона 2","Руфь 1","Есфирь 4"],"answer":"Бытие 1","explanation":"Материал строится вокруг Бытия 1."}
    ]}
    """
    parsed = _parse_quiz_json(raw)
    assert parsed and [q["correct"] for q in parsed] == [1, 0]


def test_parse_quiz_json_rejects_all_or_none_style_options():
    raw = """
    [
      {"question":"Что верно?","options":["A","B","Все перечисленное","D"],"correct":2,"explanation":"bad"},
      {"question":"Что неверно?","options":["A","B","Нет правильного ответа","D"],"correct":2,"explanation":"bad"}
    ]
    """
    assert _parse_quiz_json(raw) is None


def test_parse_quiz_json_accepts_one_based_and_cyrillic_letter_answers():
    raw = """
    [
      {"question":"Первый?","options":["A","B","C","D"],"correct":"1","explanation":"Первый вариант."},
      {"question":"Третий?","options":["A","B","C","D"],"correct":"В","explanation":"Кириллическая В соответствует третьему варианту."},
      {"question":"Четвёртый?","options":["A","B","C","D"],"correct":"вариант Г","explanation":"Кириллическая Г соответствует четвёртому варианту."}
    ]
    """
    parsed = _parse_quiz_json(raw)
    assert parsed and [q["correct"] for q in parsed] == [1, 2, 3]
