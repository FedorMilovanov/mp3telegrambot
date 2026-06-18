from services.quiz_generator import _format_ai_context, _parse_quiz_json, _sanitize_poll_payload, quiz_response_schema


def test_parse_quiz_json_requires_four_unique_options_and_valid_correct():
    raw = """
    [
      {"question":"Почему авторитет Бытия важен для аргумента о творении?","options":["Текст Бытия задаёт норму для толкования дней","Жанр Бытия задаёт символическую рамку для дней","Порядок дней служит только литературной организацией","Богословский вывод строится на поздней традиции"],"correct":0,"explanation":"Материал связывает выводы о творении с доверием к тексту Бытия."},
      {"question":"bad","options":["A","A","C","D"],"correct":0,"explanation":"x"},
      {"question":"bad2","options":["A","B","C"],"correct":0,"explanation":"x"},
      {"question":"bad3","options":["A","B","C","D"],"correct":9,"explanation":"x"}
    ]
    """
    parsed = _parse_quiz_json(raw)
    assert parsed == [{
        "question": "Почему авторитет Бытия важен для аргумента о творении?",
        "options": [
            "Текст Бытия задаёт норму для толкования дней",
            "Жанр Бытия задаёт символическую рамку для дней",
            "Порядок дней служит только литературной организацией",
            "Богословский вывод строится на поздней традиции",
        ],
        "correct": 0,
        "explanation": "Материал связывает выводы о творении с доверием к тексту Бытия.",
    }]


def test_parse_quiz_json_dedupes_questions_and_trims_lengths():
    long_q = "Как " + ("В" * 400)
    raw = str([
        {
            "question": long_q,
            "options": [
                "Историко-грамматическое толкование дней творения " * 4,
                "Литературно-рамочное толкование дней творения",
                "Догматически-символическое толкование дней творения",
                "Литургически-поэтическое толкование дней творения",
            ],
            "correct": 0,
            "explanation": "E" * 250,
        },
        {
            "question": long_q,
            "options": [
                "Историко-грамматическое толкование",
                "Литературно-рамочное толкование",
                "Догматически-символическое толкование",
                "Литургически-поэтическое толкование",
            ],
            "correct": 0,
            "explanation": "dup",
        },
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
    assert "близкими по смысловой категории" in QUIZ_PROMPT
    assert "карикатуры" in QUIZ_PROMPT


def test_parse_quiz_json_accepts_wrapped_questions_and_letter_or_text_answer():
    raw = """
    {"questions": [
      {"question":"Почему ответ о буквальном дне лучше отражает ход аргумента?","options":["Он сохраняет связь текста с авторитетом Писания","Он переносит акцент на литературную рамку Писания","Он связывает день прежде всего с богослужебным символом","Он читает последовательность как богословскую схему"],"correct":"A","explanation":"Первый вариант прямо связан с аргументом материала."},
      {"question":"Какой акцент в Бытии 1 используется для разбора творения?","options":["Повтор вечер-утро как указание на последовательность дней","Повтор вечер-утро как чисто литургическая формула","Повтор вечер-утро как символ заветного покоя","Повтор вечер-утро как рамка поздней редакции"],"answer":"Повтор вечер-утро как указание на последовательность дней","explanation":"Материал строит аргумент вокруг повторяющейся формулы Бытия 1."}
    ]}
    """
    parsed = _parse_quiz_json(raw)
    assert parsed and [q["correct"] for q in parsed] == [0, 0]


def test_parse_quiz_json_rejects_all_or_none_style_options():
    raw = """
    [
      {"question":"Почему буквальное творение связано с авторитетом Писания?","options":["Текст Бытия задаёт рамку","Жанр отменяет историю","Все перечисленное","Аргумент не касается Библии"],"correct":2,"explanation":"bad"},
      {"question":"Как материал связывает грехопадение и творение?","options":["Через историчность Бытия","Через отказ от экзегезы","Нет правильного ответа","Через отрицание Адама"],"correct":2,"explanation":"bad"}
    ]
    """
    assert _parse_quiz_json(raw) is None


def test_parse_quiz_json_accepts_one_based_and_cyrillic_letter_answers():
    raw = """
    [
      {"question":"Почему первый день важен для структуры повествования?","options":["Ритм вечера и утра задаёт последовательное чтение дней","Ритм вечера и утра служит литургическим рефреном дней","Формула дня связывает текст с богословской схемой дней","Повтор формулы переводит дни в поэтическую композицию"],"correct":"1","explanation":"Строковая 1 трактуется как первый пункт списка."},
      {"question":"Какой вывод лучше соответствует аргументу о третьем дне?","options":["Формула дня отделяет текст от порядка творения","Порядок дня служит только литературной симметрии","Повторяющаяся формула поддерживает последовательность дня","Третий день задаёт символическую модель храма"],"correct":"В","explanation":"Кириллическая В соответствует третьему варианту."},
      {"question":"Какой вариант точнее передаёт итоговый богословский акцент?","options":["Традиция помогает уточнить смысл текста Писания","Исторический вопрос подчинён систематической схеме","Экзегеза должна быть согласована с культурной моделью","Текст Писания должен формировать богословский вывод"],"correct":"вариант Г","explanation":"Кириллическая Г соответствует четвёртому варианту."}
    ]
    """
    parsed = _parse_quiz_json(raw)
    assert parsed and [q["correct"] for q in parsed] == [0, 2, 3]


def test_parse_quiz_json_rejects_meta_questions_and_placeholder_options():
    raw = """
    [
      {"question":"Что утверждает материал?","options":["Текст Бытия важен","Жанр всё отменяет","Вопрос неясен","Вывод вторичен"],"correct":0,"explanation":"Слишком общий вопрос."},
      {"question":"Почему авторитет Бытия важен для аргумента о творении?","options":["A","B","C","D"],"correct":0,"explanation":"Плейсхолдеры не годятся."}
    ]
    """
    assert _parse_quiz_json(raw) is None


def test_parse_quiz_json_rejects_obvious_and_category_mismatched_distractors():
    raw = """
    [
      {"question":"Почему авторитет Бытия важен для аргумента о творении?","options":["Текст Бытия задаёт норму для толкования дней","Жанр отменяет историю","Вопрос неясен для слушателя","Порядок дней не имеет значения"],"correct":0,"explanation":"Слишком очевидные неправильные варианты."},
      {"question":"Какой нюанс важен в разборе первого дня?","options":["Повтор вечер-утро задаёт последовательность дней","Младенцы наследуют славу","Музыка формирует настроение","Политическая власть меняет тему"],"correct":0,"explanation":"Варианты из разных категорий не создают умный тест."}
    ]
    """
    assert _parse_quiz_json(raw) is None


def test_sanitize_poll_payload_enforces_telegram_limits_and_quality():
    payload = _sanitize_poll_payload({
        "question": "Почему буквальное чтение дней творения связано с авторитетом Писания" + " очень" * 80,
        "options": [
            "Текст Бытия задаёт норму для толкования дней",
            "Жанр Бытия задаёт символическую рамку для дней",
            "Порядок дней служит литературной организацией",
            "Богословский вывод строится на поздней традиции",
        ],
        "correct": 0,
        "explanation": "Объяснение" * 40,
    }, index=12, total=20)
    assert payload
    assert len(payload["question"]) <= 300
    assert payload["question"].startswith("❓ 12/20: ")
    assert payload["question"].endswith("…?")
    assert len(payload["explanation"]) <= 200

    assert _sanitize_poll_payload({
        "question": "Почему авторитет Бытия важен для аргумента о творении?",
        "options": ["A", "B", "C", "D"],
        "correct": 0,
        "explanation": "bad",
    }, index=1, total=1) is None

    assert _sanitize_poll_payload({
        "question": "Почему авторитет Бытия важен для аргумента о творении?",
        "options": ["Текст Бытия задаёт норму", "Жанр отменяет историю", "Вопрос неясен", "Порядок не важен"],
        "correct": 0,
        "explanation": "bad",
    }, index=1, total=1) is None


def test_reflection_question_normalizer_repairs_markers_dedupes_and_filters():
    from services.telegraph_pages import _normalize_question_items

    out = _normalize_question_items([
        "Как этот аргумент проверяет мою веру",
        "🟢 Как этот аргумент проверяет мою веру?",  # duplicate after ? repair
        "🔵 Почему буквальное творение связано с авторитетом Писания?",
        "Коротко",
        "Это декларативное утверждение без вопроса.",
    ])
    assert out == [
        "🟢 Как этот аргумент проверяет мою веру?",
        "🔵 Почему буквальное творение связано с авторитетом Писания?",
    ]
