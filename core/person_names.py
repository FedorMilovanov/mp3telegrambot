#!/usr/bin/env python3
"""Canonical person-name normalization for captions, titles and generated prose."""
from __future__ import annotations

import re

_PERSON_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Ар Си Спраул", "Р. Ч. Спроул"),
    ("Ар-Си Спраул", "Р. Ч. Спроул"),
    ("Р. Ч. Спрол", "Р. Ч. Спроул"),
    ("Р. Ч. Спроул", "Р. Ч. Спроул"),
    ("Р. К. Спрол", "Р. Ч. Спроул"),
    ("R.C. Sproul", "Р. Ч. Спроул"),
    ("R. C. Sproul", "Р. Ч. Спроул"),
    ("RC Sproul", "Р. Ч. Спроул"),
    ("Эс Льюиса Джонсона", "С. Льюиса Джонсона"),
    ("Эс Льюис Джонсон", "С. Льюис Джонсон"),
    ("S. Lewis Johnson", "С. Льюис Джонсон"),
    # AUDIT R22: выровнено с KNOWN_AUTHOR_RU ниже ("Мартин Лойд-Джонс",
    # одна "л") — раньше эта таблица независимо использовала "Ллойд-Джонс"
    # (две "л"), и один и тот же человек получал разное написание в
    # зависимости от того, какая из двух функций его обработала.
    ("Мартина Лойда Джонса", "Мартина Лойд-Джонса"),
    ("Мартина Ллойд Джонса", "Мартина Лойд-Джонса"),
    ("Мартин Лойд Джонс", "Мартин Лойд-Джонс"),
    ("Мартин Ллойд Джонс", "Мартин Лойд-Джонс"),
    ("Лойда Джонса", "Лойд-Джонса"),
    ("Ллойд Джонса", "Лойд-Джонса"),
    ("Lloyd-Jones", "Лойд-Джонс"),
    ("Martyn Lloyd-Jones", "Мартин Лойд-Джонс"),
    # AUDIT R22: already-hyphenated Cyrillic double-л form — Gemini can
    # still emit this directly (a third registry, core/source_titles.py's
    # AUTHOR_CANONICAL, used it as a prompt example until this same audit
    # fixed it); catch it here too so any residual/cached occurrence still
    # gets corrected to the single-л canonical spelling.
    ("Мартин Ллойд-Джонс", "Мартин Лойд-Джонс"),
    ("Мартина Ллойд-Джонса", "Мартина Лойд-Джонса"),
    ("Си Джей Махони", "Си Джей Мэхэни"),
    ("Си Джей Махани", "Си Джей Мэхэни"),
    ("C.J. Mahaney", "Си Джей Мэхэни"),
    ("C. J. Mahaney", "Си Джей Мэхэни"),
    ("Чарльз Сперджен", "Чарльз Сперджен"),
    ("Чарльз Спержен", "Чарльз Сперджен"),
    ("Чарльза Сперджена", "Чарльза Сперджена"),
    ("Чарльза Спержена", "Чарльза Сперджена"),
    ("Георг Мюллер", "Георг Мюллер"),
    ("Джордж Мюллер", "Георг Мюллер"),
    ("Водди Бокам", "Водди Бокам"),
    ("Водди Баукам", "Водди Бокам"),
    ("Вудди Бокам", "Водди Бокам"),
    ("Джон Пайпер", "Джон Пайпер"),
    ("Алистер Бегг", "Алистер Бегг"),
    ("Алистер Бег", "Алистер Бегг"),
    ("Стивен Лоусон", "Стивен Лоусон"),
    ("Стив Лоусон", "Стивен Лоусон"),
    ("Синклер Фергюсон", "Синклер Фергюсон"),
    ("Дерек Принс", "Дерек Принс"),
    ("Дэвид Платт", "Дэвид Платт"),
    ("Дэвид Плэтт", "Дэвид Платт"),
    ("Фрэнсис Чан", "Фрэнсис Чан"),
    ("Мэтт Чендлер", "Мэтт Чендлер"),
    ("Мэтт Чандлер", "Мэтт Чендлер"),
    ("Элизабет Эллиот", "Элизабет Эллиот"),
    ("Розария Баттерфилд", "Розария Баттерфилд"),
    ("Джеки Хилл Перри", "Джеки Хилл Перри"),
    ("Ричард Филлипс", "Ричард Филлипс"),
    ("Ричард Д. Филлипс", "Ричард Филлипс"),
    ("Richard Phillips", "Ричард Филлипс"),
    # AUDIT R22: выровнено с KNOWN_AUTHOR_RU ("Джоэль Бики", с мягким знаком) —
    # раньше здесь было "Джоэл" без мягкого знака, другое написание для
    # того же человека в зависимости от функции.
    ("Джоэл Бики", "Джоэль Бики"),
    ("Joel Beeke", "Джоэль Бики"),
    ("Джон МакАртур", "Джон МакАртур"),
    ("John MacArthur", "Джон МакАртур"),
    ("Пол Вошер", "Пол Вошер"),
    ("Paul Washer", "Пол Вошер"),
    ("Джон Мюллер", "Георг Мюллер"),
    ("George Mueller", "Георг Мюллер"),
)


def normalize_person_names(text: str) -> str:
    if not text:
        return text
    if not str(text).strip():
        return text
    out = str(text)
    changed = False
    for src, dst in _PERSON_REPLACEMENTS:
        # 2026-06-11: Делаем замену имен регистронезависимой.
        # Если в тексте "джоэл бики", он должен стать "Джоэл Бики".
        # Используем regex для точного совпадения границ слов.
        pattern = rf"(?i)\b{re.escape(src)}\b"
        new_out = re.sub(pattern, dst, out)
        if new_out != out:
            out = new_out
            changed = True
    # Initials spacing polish: "Р.Ч. Спроул" -> "Р. Ч. Спроул"
    out2 = re.sub(r"\b([А-ЯA-Z])\.\s*([А-ЯA-Z])\.\s*", lambda m: f"{m.group(1)}. {m.group(2)}. ", out)
    if out2 != out:
        changed = True
        out = out2
    out2 = re.sub(r" {2,}", " ", out)
    if out2 != out:
        changed = True
        out = out2
    return out.strip() if changed else out


def canonical_person_name(value: str) -> str:
    """Return one canonical display form for a person/source author name."""
    return normalize_person_names(str(value or "").strip())


# ── Known-author recognition (shared by parse_title and LiveDub captions) ──
#
# AUDIT R20 (лог 2026-07-09/10, реальные баги): раньше это жило ТОЛЬКО в
# pipelines/main_pipeline.py и использовалось лишь для LiveDub-подписи —
# core/utils.py::parse_title() принимал решение title/performer по наивному
# подсчёту слов, который ломался на реальных YouTube-заголовках вида:
#   "How to Pray: Prayer with R.C. Sproul"
#     -> performer="How to Pray", title="Prayer with R.C. Sproul" (перепутано)
#   "**MacArthur, Mohler, and Sproul**: Questions and Answers"
#     -> performer="Questions and Answers", title="**MacArthur...**" (перепутано)
# Вынесено сюда как общий реестр, чтобы обе точки использовали ОДИН и тот
# же сигнал распознавания имени, а не гадали по числу слов.

KNOWN_AUTHOR_RU: dict[str, str] = {
    "John MacArthur": "Джон МакАртур",
    "MacArthur": "Джон МакАртур",
    "Paul Washer": "Пол Вошер",
    "Washer": "Пол Вошер",
    "Abner Chou": "Абнер Чау",
    "Costi Hinn": "Кости Хинн",
    "R.C. Sproul": "Р. Ч. Спроул",
    "R. C. Sproul": "Р. Ч. Спроул",
    "Charles Spurgeon": "Чарльз Сперджен",
    "Spurgeon": "Чарльз Сперджен",
    "George Müller": "Георг Мюллер",
    "George Muller": "Георг Мюллер",
    "Voddie Baucham": "Водди Бокам",
    "John Piper": "Джон Пайпер",
    "Piper": "Джон Пайпер",
    "Alistair Begg": "Алистер Бегг",
    "Begg": "Алистер Бегг",
    "Steven Lawson": "Стивен Лоусон",
    "Steve Lawson": "Стивен Лоусон",
    "Sinclair Ferguson": "Синклер Фергюсон",
    "Derek Prince": "Дерек Принс",
    "Martyn Lloyd-Jones": "Мартин Лойд-Джонс",
    "Lloyd-Jones": "Мартин Лойд-Джонс",
    "David Platt": "Дэвид Платт",
    "Francis Chan": "Фрэнсис Чан",
    "Matt Chandler": "Мэтт Чендлер",
    "Elisabeth Elliot": "Элизабет Эллиот",
    "Rosaria Butterfield": "Розария Баттерфилд",
    "Jackie Hill Perry": "Джеки Хилл Перри",
    "Albert Mohler": "Альберт Молер",
    "Al Mohler": "Альберт Молер",
    "Mohler": "Альберт Молер",
    "Phil Johnson": "Фил Джонсон",
    "Tom Pennington": "Том Пеннингтон",
    "Pennington": "Том Пеннингтон",
    "Nathan Busenitz": "Натан Бузениц",
    "Busenitz": "Натан Бузениц",
    "Alex Montoya": "Алекс Монтойя",
    "Montoya": "Алекс Монтойя",
    "Lance Quinn": "Лэнс Куинн",
    "Joel Beeke": "Джоэль Бики",
    "Beeke": "Джоэль Бики",
    "Альберт Моллер": "Альберт Молер",
    "Стив Лоусон": "Стивен Лоусон",
    "Алексей Коломийцев": "Алексей Коломийцев",
    "Евгений Бахмутский": "Евгений Бахмутский",
    "Николай Скопич": "Николай Скопич",
    "Виктор Рягузов": "Виктор Рягузов",
    "Юрий Сипко": "Юрий Сипко",
    "Владислав Трескин": "Владислав Трескин",
    "Алексей Прокопенко": "Алексей Прокопенко",
    "Владимир Дубинский": "Владимир Дубинский",
    "Алексей Смирнов": "Алексей Смирнов",
    "Тимур Расулов": "Тимур Расулов",
    "Ярл Пейсти": "Ярл Пейсти",
}

KNOWN_CHANNEL_AUTHOR_RU: dict[str, str] = {
    "grace to you": "Джон МакАртур",
    "gty": "Джон МакАртур",
    "heartcry missionary society": "Пол Вошер",
    "heartcry": "Пол Вошер",
    "i'll be honest": "I'll Be Honest",
    "bibleq&a": "BibleQ&A",
    "слово благодати": "Алексей Коломийцев",
    "islovo": "Алексей Коломийцев",
    "русская библейская церковь": "Евгений Бахмутский",
    "рбц": "Евгений Бахмутский",
    "covenant baptist theological seminary": "CBTS",
    "cbts": "CBTS",
    "desiring god": "Джон Пайпер",
    "ligonier ministries": "Р. Ч. Спроул",
    "ligonier": "Р. Ч. Спроул",
    "bible qa": "Джон МакАртур",
    "mcarthur": "Джон МакАртур",
    "tms": "The Master's Seminary",
    "masters seminary": "The Master's Seminary",
}


def known_author_from_text(*parts: str) -> str:
    """Return the canonical RU name of the first recognized author mentioned
    anywhere within the given text fragments (channel name as last resort)."""
    combined = " | ".join(str(p or "") for p in parts)
    combined_low = combined.lower()
    for en, ru in KNOWN_AUTHOR_RU.items():
        if "." in en:
            # Для имён с точками (R.C. Sproul) \b вокруг точки ненадёжен —
            # только для НИХ используем substring, а не для всех имён.
            if en.lower() in combined_low:
                return ru
        # AUDIT R22 (найдено при аудите регрессий): голый substring-матч
        # без \b ловил фамилию внутри произвольного слова — "Beggar" matched
        # "Begg" -> "Алистер Бегг", "washer"/"bagpiper" аналогично. \b —
        # единственно верная проверка для обычных (без точек) имён.
        elif re.search(rf"(?i)\b{re.escape(en)}\b", combined):
            return ru
    low = combined.lower()
    for channel, ru in KNOWN_CHANNEL_AUTHOR_RU.items():
        if channel in low:
            return ru
    return ""


def known_ru_author_from_text(text: str) -> str:
    low = str(text or "").lower()
    for ru in set(KNOWN_AUTHOR_RU.values()) | set(KNOWN_CHANNEL_AUTHOR_RU.values()):
        if ru.lower() in low:
            return ru
    return ""


def looks_like_author_list(text: str) -> bool:
    text = str(text or "").strip()
    if not text or len(text) > 80:
        return False
    if known_author_from_text(text):
        return True
    if re.search(r"[?!:]{1}", text):
        return False
    words = [w for w in re.split(r"\s+|\s*&\s*|\s+and\s+", text) if w]
    if not (2 <= len(words) <= 8):
        return False
    meaningful = [w.strip(".,;:()[]{}") for w in words if w.strip(".,;:()[]{}")]
    caps = sum(1 for w in meaningful if re.match(r"^[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'-]+$", w))
    return caps >= max(2, len(meaningful) - 1)
