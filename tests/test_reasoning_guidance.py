from core.reasoning_guidance import (
    build_reasoning_first_block,
    build_synopsis_reasoning_note,
)


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def test_reflection_guidance_places_truth_before_application():
    prompt = _flat(build_reasoning_first_block("reflection"))
    assert "ИСТИНА ПРЕЖДЕ ПРАКТИКИ" in prompt
    assert "истина → усвоение → сопротивление → мудрый ответ" in prompt
    assert "Практика без основания" in prompt
    assert "АДАПТАЦИЯ К ЖАНРУ" in prompt
    assert "Богословская проповедь может почти не" in prompt


def test_study_guidance_teaches_biblical_assimilation():
    prompt = _flat(build_reasoning_first_block("study"))
    assert "МОДЕЛЬ УСВОЕНИЯ ИСТИНЫ" in prompt
    assert "показать её основание в тексте" in prompt
    assert "показать, как читатель может сам проверить" in prompt
    assert "действие без истины не формирует зрелость" in prompt


def test_translation_guidance_is_selective_and_explanatory():
    prompt = _flat(build_reasoning_first_block("study"))
    assert "ПОЛОЖИТЕЛЬНЫЙ МЕТОД РАЗБОРА ПЕРЕВОДОВ И ОРИГИНАЛА" in prompt
    assert "Отсутствие содержательной развилки — нормальный результат" in prompt
    assert "ОБЩАЯ ОСНОВА" in prompt
    assert "ПОЧЕМУ ВОЗНИКЛИ ВАРИАНТЫ" in prompt
    assert "ВЕРДИКТ ПО СТЕПЕНИ" in prompt
    assert "не «хороший против плохого»" in prompt
    assert "не создавай эффект «в синодальном всё потеряно" in prompt.lower()


def test_translation_guidance_blocks_fake_aspect_claims_positively():
    prompt = _flat(build_reasoning_first_block("audio"))
    assert "какая именно форма стоит в оригинале" in prompt
    assert "чего сама по себе НЕ доказывает" in prompt
    assert "Вид действия, временная протяжённость и повторяемость" in prompt
    assert "Не делай этимологию богословием" in prompt


def test_synopsis_has_a_separate_full_verbatim_contract():
    prompt = _flat(build_synopsis_reasoning_note())
    assert "РЕЖИМ ПОЛНОЙ ДОСЛОВНОЙ СТЕНОГРАММЫ" in prompt
    assert "каждое произнесённое предложение" in prompt
    assert "повторы, слова-паразиты, оговорки, самокоррекции" in prompt
    assert "не перефразируй, не объединяй предложения, не уплотняй" in prompt.lower()
    assert "никогда не сокращай стенограмму" in prompt.lower()
    assert "сжатая стенограмма" not in prompt.lower()
    assert "уплотнение без" not in prompt.lower()
    assert "богословский редактор" not in prompt.lower()
    assert "АНТИБАНАЛЬНЫЙ ФИЛЬТР" not in prompt


def test_synopsis_reasoning_block_is_the_same_dedicated_contract():
    assert build_reasoning_first_block("synopsis") == build_synopsis_reasoning_note()
