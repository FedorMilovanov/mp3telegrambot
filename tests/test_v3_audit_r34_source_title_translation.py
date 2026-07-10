#!/usr/bin/env python3
"""AUDIT R34 (живой дамп 2026-07-10, жалоба оператора: «Holiness, Дж. К. Райл —
почему без русского перевода?»).

Два бага в подстановке официального русского названия книги:
  A) точное совпадение названия: модель дала «Of the Mortification of Sin»,
     а в реестре «…of Sin in Believers» → перевод не подставлялся;
  B) already-canonical карточка «**English Title**, RU Author (…)» уходила
     в pass-through мимо official_ru_title(), поэтому английское название
     оставалось непереведённым, даже когда перевод есть в реестре.

Защита должна быть точечной: переводим ТОЛЬКО книги с известным официальным
русским названием; чужие книги без перевода (Вошер «The Power and Message of
the Gospel») и не-карточки (ключевые понятия, стихи) не трогаем.
"""
from core.source_titles import normalize_source_card_line as N
from core.source_titles import official_ru_title


def test_owen_short_title_matches_full_registry_title():
    # Баг A: короткое название модели совпадает с полным из реестра.
    assert official_ru_title("John Owen", "Of the Mortification of Sin") == "Об умерщвлении греха в верующих"
    assert official_ru_title("Джон Оуэн", "Mortification of Sin") == "Об умерщвлении греха в верующих"


def test_russian_author_resolves_official_title():
    # Баг B (часть): русское имя автора тоже должно находить перевод.
    assert official_ru_title("Дж. К. Райл", "Holiness") == "Святость"
    assert official_ru_title("Дж. Ч. Райл", "Holiness") == "Святость"


def test_no_false_positive_same_title_other_author():
    # «Holiness» Райла НЕ должен подставляться другим авторам.
    assert official_ru_title("Jerry Bridges", "Holiness") == ""   # у него "The Pursuit of Holiness"
    assert official_ru_title("John MacArthur", "Holiness") == ""
    # Пустой автор — не угадываем книгу по одному названию.
    assert official_ru_title("", "Holiness") == ""


def test_canonical_card_english_title_upgraded_with_paren():
    line = "**Of the Mortification of Sin**, Джон Оуэн (John Owen). — Практическое руководство."
    out = N(line)
    assert out.startswith("**Об умерщвлении греха в верующих**")
    assert "Джон Оуэн" in out
    assert "— Практическое руководство." in out  # хвост «зачем» сохранён


def test_canonical_card_english_title_upgraded_without_paren():
    line = "**Holiness**, Дж. К. Райл. — Глубокий пасторский труд о святости."
    out = N(line)
    assert out.startswith("**Святость**")
    assert "Дж. Ч. Райл" in out                  # имя канонизировано (R23)
    assert "— Глубокий пасторский труд о святости." in out


def test_already_russian_card_untouched():
    line = "**Учение о покаянии**, Томас Уотсон (The Doctrine of Repentance, Thomas Watson). — Классика."
    assert N(line) == line


def test_unknown_book_stays_english():
    # Нет в реестре официального перевода — оставляем как есть, не выдумываем.
    line = "**The Power and Message of the Gospel**, Пол Вошер. — Современное изложение."
    assert N(line) == line


def test_key_concept_line_not_treated_as_book_card():
    line = "**Возрождение** (**Regeneration**) — сверхъестественный акт Святого Духа."
    assert N(line) == line
