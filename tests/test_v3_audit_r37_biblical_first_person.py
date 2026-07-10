#!/usr/bin/env python3
"""AUDIT R37 (живой дамп 2026-07-10): guard first_person_author_fixed срезал
«Иоанн» из Откр. 1:9 — конспект вышел с «Я брат ваш» вместо «Я, Иоанн, брат
ваш». Причина: в whitelist были только Господь/Бог/Христос/Иисус/Яхве, а
библейские авторы-люди (Иоанн, Павел, Пётр…) — нет.

Фикс: whitelist библейских говорящих от первого лица (по первому слову имени).
Современные галлюцинированные авторы («Я, Джон МакАртур, …») по-прежнему
срезаются.
"""
from core.content_audit import _scrub_mismatched_first_person_author as scrub


def test_biblical_authors_preserved_in_scripture():
    for name in ["Иоанн", "Павел", "Пётр", "Петр", "Иаков", "Иуда",
                 "Давид", "Соломон", "Даниил", "Исаия", "Моисей", "Иов"]:
        txt = f"«Я, {name}, свидетель сему»"
        out, issues = scrub(txt, expected_author="Джон МакАртур")
        assert out == txt and not issues, f"{name} не должен срезаться: {out!r}"


def test_god_multiword_preserved():
    txt = "Я, Господь Бог, творю новое."
    out, issues = scrub(txt, expected_author="Пол Вошер")
    assert out == txt and not issues


def test_modern_hallucinated_author_still_scrubbed():
    out, issues = scrub("Я, Джон МакАртур, считаю иначе.", expected_author="Пол Вошер")
    assert issues and "Джон МакАртур" not in out
    assert out.startswith("Я считаю")


def test_expected_author_first_person_kept():
    # Совпадение с ожидаемым автором — не трогаем (прежнее поведение).
    txt = "Я, Пол Вошер, говорю вам."
    out, issues = scrub(txt, expected_author="Пол Вошер")
    assert out == txt and not issues


def test_revelation_1_9_regression():
    # Точный кейс из живого дампа.
    txt = "мы читаем: «Я, Иоанн, брат ваш и соучастник в скорби»"
    out, issues = scrub(txt, expected_author="Джон МакАртур")
    assert "Я, Иоанн, брат ваш" in out and not issues
