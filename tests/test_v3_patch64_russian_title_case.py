"""Regression tests for v3 patch 64 — Russian sentence-case titles."""

from core.text_utils import sentence_case_russian_title, title_case_fragment


def test_russian_title_case_uses_sentence_case_not_english_title_case():
    assert title_case_fragment("Вопросы и Ответы") == "Вопросы и ответы"
    assert title_case_fragment("Как Проповедовать Пламенно") == "Как проповедовать пламенно"
    assert title_case_fragment("Свидания, Брак и Семейная Жизнь") == "Свидания, брак и семейная жизнь"


def test_russian_title_case_preserves_divine_biblical_and_internal_caps():
    assert sentence_case_russian_title("Наш Бог — Огонь Поядающий") == "Наш Бог — огонь поядающий"
    assert sentence_case_russian_title("Христос Умер Для Бога") == "Христос умер для Бога"
    assert sentence_case_russian_title("Джон МакАртур и Р. Ч. Спроул") == "Джон МакАртур и Р. Ч. Спроул"
    assert sentence_case_russian_title("Достаточность Писания: Псалом 18") == "Достаточность Писания: Псалом 18"


def test_english_title_case_still_works_for_latin_titles():
    assert title_case_fragment("how to preach biblically") == "How to Preach Biblically"
