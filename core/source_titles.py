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
from core.person_names import canonical_person_name, normalize_person_names

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
    ("R.C. Sproul", "The Holiness of God"): "Святость Бога",
    ("R. C. Sproul", "The Holiness of God"): "Святость Бога",
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
    r"(?P<en_author>[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z .’'\-]{2,100}),\s+"
    r"(?P<title>[A-Za-z][^\n]{2,220})$"
)

_DUPLICATE_PAREN_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<head_author>[А-ЯЁA-Z][^,\n]{1,100}),\s*"
    r"(?P<title>[A-Za-z][^()\n]{2,220}?)\.?\s*"
    r"\(\s*(?P=head_author)\s*,\s*(?P=title)\.?\s*\)\.?$"
)


_FLEX_DUPLICATE_PAREN_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<head_author>[^,\n]{2,120}),\s*"
    r"(?P<title>[^()\n]{2,220}?)\.?\s*"
    r"\(\s*(?P<paren_author>[^,\n]{2,120}),\s*"
    r"(?P<paren_title>[^()\n]{2,220}?)\.?\s*\)\.?$"
)


def _same_author(a: str, b: str) -> bool:
    aa = canonical_author_name(str(a or "").strip())
    bb = canonical_author_name(str(b or "").strip())
    return normalize_person_names(aa).casefold() == normalize_person_names(bb).casefold()


def _same_title(a: str, b: str) -> bool:
    def norm(x: str) -> str:
        return re.sub(r"\W+", "", str(x or "").strip().rstrip(".").casefold())
    return bool(norm(a)) and norm(a) == norm(b)


def canonical_author_name(value: str) -> str:
    value = str(value or "").strip()
    return canonical_person_name(AUTHOR_CANONICAL.get(value, value))


def original_author_name(value: str) -> str:
    """Best-effort original author label for parenthetical source cards."""
    raw = str(value or "").strip()
    if re.search(r"[A-Za-z]", raw):
        return raw
    canon = canonical_person_name(raw)
    for en, ru in AUTHOR_CANONICAL.items():
        if canonical_person_name(ru) == canon:
            return en
    return raw


def official_ru_title(en_author: str, en_title: str) -> str:
    author = str(en_author or "").strip()
    title = str(en_title or "").strip().rstrip(".")
    direct = OFFICIAL_RU_TITLES.get((author, title), "")
    if direct:
        return direct
    author_canon = canonical_person_name(author)
    for known_author, known_title in OFFICIAL_RU_TITLES:
        if known_title == title and canonical_person_name(AUTHOR_CANONICAL.get(known_author, known_author)) == author_canon:
            return OFFICIAL_RU_TITLES[(known_author, known_title)]
    return ""


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



def _format_canonical_source_card(bullet: str, author: str, en_author: str, en_title: str, *, fallback_ru_title: str = "") -> str:
    """Return title-first source-card display.

    Policy: title first for readability; Russian title when known, with original
    title + original author in parentheses. If no reliable Russian title is
    known, keep the original title and still show the canonical Russian author.
    """
    en_author = original_author_name(en_author)
    en_title = str(en_title or "").strip().rstrip(".")
    ru_title = official_ru_title(en_author, en_title) or correct_known_ru_title(fallback_ru_title).strip().rstrip(".")
    if ru_title and re.search(r"[A-Za-z]", ru_title) and not re.search(r"[А-Яа-яЁё]", ru_title):
        ru_title = ""
    display_title = ru_title if (ru_title and en_title and ru_title.casefold() != en_title.casefold()) else en_title
    original = ", ".join(x for x in (en_title, en_author) if x)
    if original and display_title != en_title:
        return f"{bullet}**{display_title}**, {author} ({original})."
    if en_author and canonical_author_name(en_author) != en_author:
        return f"{bullet}**{display_title}**, {author} ({en_author})."
    return f"{bullet}**{display_title}**, {author}."

def normalize_source_card_line(line: str, *, prefer_original: bool = True) -> str:
    """Normalize one bibliography/source-card line.

    - ``RU title (English Author, English Title)`` -> canonical author + English title;
    - known English author at line start -> canonical Russian author;
    - duplicate bilingual authors are removed;
    - known wrong Russian titles are corrected.
    """
    if not line:
        return line
    out = normalize_person_names(correct_known_ru_title(str(line or "")))
    # Only source-card-like lines need aggressive whitespace/dedupe normalization.
    # Plain inline nodes such as "• " must keep their spacing.
    looks_like_source = bool(re.search(r"[A-Za-z].*,|,\s*[A-Za-z][A-Za-z ]{2,}|\([^)]*[A-Za-z]{3,}[^)]*\)", out))
    if looks_like_source:
        out = dedupe_bilingual_authors(out)

    dup = _DUPLICATE_PAREN_RE.match(out.strip())
    if dup:
        bullet = dup.group("bullet") or ""
        author = normalize_person_names(dup.group("head_author").strip())
        title = dup.group("title").strip().rstrip(".")
        return f"{bullet}{author}, {title}."

    flex_dup = _FLEX_DUPLICATE_PAREN_RE.match(out.strip())
    if flex_dup and _same_author(flex_dup.group("head_author"), flex_dup.group("paren_author")) and _same_title(flex_dup.group("title"), flex_dup.group("paren_title")):
        bullet = flex_dup.group("bullet") or ""
        author = canonical_author_name(flex_dup.group("paren_author"))
        if author == flex_dup.group("paren_author"):
            author = normalize_person_names(flex_dup.group("head_author").strip())
        title = flex_dup.group("paren_title").strip().rstrip(".")
        return _format_canonical_source_card(
            bullet, author, flex_dup.group("paren_author"), title,
            fallback_ru_title=flex_dup.group("title"),
        )

    m = _SOURCE_RU_WITH_ORIGINAL_RE.match(out.strip())
    if m and re.search(r"[A-Za-z]", m.group("en_title")):
        bullet = m.group("bullet") or ""
        en_author = m.group("en_author").strip()
        en_title = m.group("en_title").strip().rstrip(".")
        author = canonical_author_name(en_author) or m.group("author").strip()
        return _format_canonical_source_card(
            bullet, author, en_author, en_title,
            fallback_ru_title=m.group("ru_title"),
        )

    m2 = _EN_AUTHOR_WITH_TITLE_RE.match(out.strip())
    if m2:
        bullet = m2.group("bullet") or ""
        en_author = m2.group("en_author").strip()
        title = m2.group("title").strip().rstrip(".")
        author = canonical_author_name(en_author)
        if author != en_author or re.search(r"[A-Za-z]", title):
            return _format_canonical_source_card(bullet, author, en_author, title)

    if looks_like_source and out.strip().startswith(("•", "-")) and not re.search(r"[.!?]\s*$", out):
        return out.rstrip() + "."
    return out
