"""Regression tests for v3 patch 64 — Russian sentence-case titles."""

from core.text_utils import sentence_case_russian_title, title_case_fragment


def test_russian_title_case_is_operator_title_case():
    """ПРАВИЛО ПРОЕКТА (оператор, 2026-07-05): каждое значимое слово
    с Заглавной, предлоги/союзы — строчными. Sentence-case был регрессией."""
    assert title_case_fragment("Вопросы и Ответы") == "Вопросы и Ответы"
    assert title_case_fragment("Как проповедовать пламенно") == "Как Проповедовать Пламенно"
    assert title_case_fragment("Трус и лжец") == "Трус и Лжец"
    assert title_case_fragment("Трусливый лжец: история радикального обращения") == \
        "Трусливый Лжец: История Радикального Обращения"


def test_russian_title_case_preserves_divine_biblical_and_internal_caps():
    assert sentence_case_russian_title("Наш Бог — Огонь Поядающий") == "Наш Бог — огонь поядающий"
    assert sentence_case_russian_title("Христос Умер Для Бога") == "Христос умер для Бога"
    assert sentence_case_russian_title("Джон МакАртур и Р. Ч. Спроул") == "Джон МакАртур и Р. Ч. Спроул"
    assert sentence_case_russian_title("Достаточность Писания: Псалом 18") == "Достаточность Писания: Псалом 18"


def test_english_title_case_still_works_for_latin_titles():
    assert title_case_fragment("how to preach biblically") == "How to Preach Biblically"
