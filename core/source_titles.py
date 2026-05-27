#!/usr/bin/env python3
"""Canonical source-card authors and official title helpers.

This module is intentionally small and deterministic. It normalizes recurring
live-run source-card defects without trying to solve bibliography globally.
Default source-card policy: when an original English title is present, prefer it
because it is verifiable; known official Russian titles are kept as metadata and
used to correct wrong Russian variants such as «Странный огонь».
"""
from __future__ import annotations

import re

AUTHOR_CANONICAL: dict[str, str] = {
    "John MacArthur": "Джон МакАртур",
    "R.C. Sproul": "Р. Ч. Спрол",
    "R. C. Sproul": "Р. Ч. Спрол",
    "Paul Washer": "Пол Вошер",
    "Joel Beeke": "Джоэл Бики",
    "Greg Gilbert": "Грег Гилберт",
    "Kevin DeYoung": "Кевин ДеЯнг",
    "Sinclair Ferguson": "Синклер Фергюсон",
    "John Owen": "Джон Оуэн",
    "John Calvin": "Жан Кальвин",
    "Charles Spurgeon": "Чарльз Сперджен",
    "Andrew Fuller": "Эндрю Фуллер",
    "J.I. Packer": "Дж. И. Пакер",
    "J. I. Packer": "Дж. И. Пакер",
    "D.A. Carson": "Д. А. Карсон",
    "D. A. Carson": "Д. А. Карсон",
    "Voddie Baucham": "Водди Бокам",
    "Alec Motyer": "Алек Мотьер",
    "Edward J. Young": "Эдвард Янг",
    "Abner Chou": "Абнер Чау",
    "Charles Ryrie": "Чарльз Райри",
}

OFFICIAL_RU_TITLES: dict[tuple[str, str], str] = {
    ("John MacArthur", "Strange Fire"): "Чуждый огонь",
    ("John MacArthur", "Ashamed of the Gospel"): "Стыжусь ли я Евангелия?",
    ("John Owen", "The Death of Death in the Death of Christ"): "Смерть смерти в смерти Христа",
    ("John Owen", "Of the Mortification of Sin in Believers"): "Об умерщвлении греха в верующих",
    ("Andrew Fuller", "The Gospel Worthy of All Acceptation"): "Евангелие, достойное всякого принятия",
    ("John Calvin", "Commentary on Isaiah"): "Комментарии на Исаию",
    ("John Calvin", "Commentaries on Isaiah"): "Комментарии на Исаию",
}

RU_TITLE_CORRECTIONS: dict[str, str] = {
    "Странный огонь": "Чуждый огонь",
    "странный огонь": "чуждый огонь",
}

_SOURCE_RU_WITH_ORIGINAL_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<author>[А-ЯЁA-Z][^,\n]{1,100}),\s+"
    r"(?P<ru_title>[^()\n]{3,180}?)\s*"
    r"\(\s*(?P<en_author>[A-Z][A-Za-z .’'\-]{2,100}),\s*"
    r"(?P<en_title>[A-Za-z][^()]{2,220})\s*\)"
    r"(?P<tail>\.?\s*)$"
)

_EN_AUTHOR_WITH_TITLE_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<en_author>[A-Z][A-Za-z .’'\-]{2,100}),\s+"
    r"(?P<title>[A-Za-z][^\n]{2,220})$"
)


def canonical_author_name(value: str) -> str:
    value = str(value or "").strip()
    return AUTHOR_CANONICAL.get(value, value)


def official_ru_title(en_author: str, en_title: str) -> str:
    author = str(en_author or "").strip()
    title = str(en_title or "").strip().rstrip(".")
    return OFFICIAL_RU_TITLES.get((author, title), "")


def correct_known_ru_title(value: str) -> str:
    out = str(value or "")
    for src, dst in RU_TITLE_CORRECTIONS.items():
        out = out.replace(src, dst)
    return out


def dedupe_bilingual_authors(line: str) -> str:
    """Remove English author duplicates when the canonical RU name is present."""
    out = str(line or "")
    out = out.replace("Кевин ДеЯнг, Грег Гилберт, Greg Gilbert,", "Кевин ДеЯнг и Грег Гилберт,")
    out = out.replace("Kevin DeYoung, Greg Gilbert,", "Кевин ДеЯнг и Грег Гилберт,")
    for en, ru in AUTHOR_CANONICAL.items():
        if ru in out:
            out = out.replace(f", {en},", ",")
            out = out.replace(f", {en} —", " —")
            out = out.replace(f", {en} -", " -")
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r",\s*,", ",", out)
    return out.strip()


def normalize_source_card_line(line: str, *, prefer_original: bool = True) -> str:
    """Normalize one bibliography/source-card line.

    - ``RU title (English Author, English Title)`` -> canonical author + English title;
    - known English author at line start -> canonical Russian author;
    - duplicate bilingual authors are removed;
    - known wrong Russian titles are corrected.
    """
    if not line:
        return line
    out = correct_known_ru_title(str(line or ""))
    # Only source-card-like lines need aggressive whitespace/dedupe normalization.
    # Plain inline nodes such as "• " must keep their spacing.
    looks_like_source = bool(re.search(r"[A-Za-z].*,|\([^)]*[A-Za-z]{3,}[^)]*\)", out))
    if looks_like_source:
        out = dedupe_bilingual_authors(out)

    m = _SOURCE_RU_WITH_ORIGINAL_RE.match(out.strip())
    if m and re.search(r"[A-Za-z]", m.group("en_title")):
        bullet = m.group("bullet") or ""
        en_author = m.group("en_author").strip()
        en_title = m.group("en_title").strip().rstrip(".")
        author = canonical_author_name(en_author) or m.group("author").strip()
        if prefer_original:
            return f"{bullet}{author}, {en_title}."
        ru_official = official_ru_title(en_author, en_title)
        title = f"{en_title} / {ru_official}" if ru_official else en_title
        return f"{bullet}{author}, {title}."

    m2 = _EN_AUTHOR_WITH_TITLE_RE.match(out.strip())
    if m2:
        bullet = m2.group("bullet") or ""
        en_author = m2.group("en_author").strip()
        title = m2.group("title").strip().rstrip(".")
        author = canonical_author_name(en_author)
        if author != en_author:
            return f"{bullet}{author}, {title}."

    return out
