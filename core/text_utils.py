#!/usr/bin/env python3
"""
Текстовые утилиты — очистка, нормализация, title case.
Извлечено из bot.py строки 1587–1845.
"""
import re
from core.person_names import normalize_person_names
from core.source_titles import normalize_source_card_line
from core.core_utils import time_to_seconds  # разрыв цикла: ранее был lazy import из json_parser

BAD_META_PATTERNS = [
    # Cocoon / AI / meta-boilerplate — только явный мусор
    r"cocoon\s*ai\s*summary",
    r"\bcocoon\b",
    r"content\s*summary",
    r"ai\s+summary",
    r"summary\s+by",
    r"auto.?generated\s+summary",
    r"auto.?generated",
    r"^generated\s+by",
    r"^powered\s+by",
    r"^content\s+by",
    r"\bbyline\b",
    # Дата-подписи вида "March 15 at 18:55"
    r"[\s\u2022\-]*\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}",
    r"march\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}",
    # AUDIT R40: убран голый «Month D, YYYY» — он УДАЛЯЛ ВСЁ поле при легитимной
    # дате события («Конференция May 5, 2024 в Москве» → ''). Тайм-подписи
    # «… at H:MM» (публикационные) остаются выше.
    # Русские мета-фразы — только строгие якоря
    r"^статья\s+(содержит|описывает|рассматривает|анализирует|представляет)",
    r"^статья\s+представляет\s+собой",
    r"^this\s+(video|article|sermon|lecture)\s+",
    r"^в\s+этом\s+(видео|ролике)\s+",
    r"текст\s+подготовлен\s+с\s+помощью",
    r"подготовлен[оа]?\s+с\s+помощью\s+(gemini|ai|ии)",
]

# Паттерны для инлайн-замены (встречаются внутри строки, не только в начале)
_COMMON_TYPO_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # Live-run polish: frequent Gemini/ASR Russian typos seen in Telegraph pages.
    # Kept deliberately narrow: these are unambiguous spelling/case fixes.
    ("доктлиналь", "доктриналь"),
    ("Доктлиналь", "Доктриналь"),
    ("богологами", "богословами"),
    ("Богологами", "Богословами"),
    ("богологов", "богословов"),
    ("Богологов", "Богословов"),
    ("богологи", "богословы"),
    ("Богологи", "Богословы"),
    ("боголог", "богослов"),
    ("Боголог", "Богослов"),
    ("Божьего Слово", "Божьего Слова"),
    # AUDIT R40: снят ТОЛЬКО голый «Слово Божьего»→«Слова Божьего» — он был
    # контекст-слепым и ломал легитимный именительный («Слово Божьего пророка
    # звучало…» → «Слова…»). Якорный вариант ниже однозначен: после «авторитет»
    # требуется родительный, поэтому он сохранён.
    ("авторитет Слово Божьего", "авторитет Слова Божьего"),
    ("Стину Лоусону", "Стиву Лоусону"),
    ("Стин Лоусон", "Стив Лоусон"),
    ("душевпопечение", "душепопечение"),
    ("Душевпопечение", "Душепопечение"),
    ("МакАртора", "МакАртура"),
    ("Альберт Моллер", "Альберт Молер"),
    ("Моллер", "Молер"),
    ("Стив Лоусон", "Стивен Лоусон"),
    ("епифанита", "Епафродита"),
    ("Епифанита", "Епафродита"),
    # R49: Ис. 53:5 по Синодальному — «мучим за беззакония наши» (краткое
    # страдательное причастие). Gemini иногда цитирует «мучем за…» (в дословном
    # Конспекте — верно, а в Разборе/Размышлении промахивается). Якорим по
    # «мучем за», чтобы правка была однозначной и не задевала иных контекстов.
    ("мучем за", "мучим за"),
    ("Мучем за", "Мучим за"),
    # «Strange Fire» Джона МакАртура — официальный русский титул «Чуждый огонь»
    # (и синодальный термин, Лев 10:1). В этом корпусе «странный огонь» практически
    # всегда указывает на книгу/библейское понятие; коррекция намеренная и покрыта
    # тестами (page_audit, source_titles, patch21/22). R40: возврат после ошибочного
    # снятия — снимать её значило регрессировать проверенное поведение.
    ("Странный огонь", "Чуждый огонь"),
    ("странный огонь", "чуждый огонь"),
    ("Вопросы и Ответы", "Вопросы и ответы"),
    ("Свидания, Брак и Семейная Жизнь", "Свидания, брак и семейная жизнь"),
    ("Писание", "Писание"),
    ("священное Писание", "Священное Писание"),
    ("Библия", "Библия"),
    # Mixed Cyrillic/Greek letters in original-language terms.
    ("βασιлеία", "βασιλεία"),
    ("μορφύω", "μορφόω"),
    ("μεταμορφύω", "μεταμορφόω"),
    ("μεлеτάω", "μελετάω"),
    ("ὑπόκрисις", "ὑπόκρισις"),
    ("ἡμέра", "ἡμέρα"),
    ("κυριακὴ ἡμέра", "κυριακὴ ἡμέρα"),
    ("Saturdays-night", "Saturday-night"),
    ("day один", "день один"),
    ("day первый", "день первый"),
    ("Day один", "День один"),
    ("Day первый", "День первый"),
    ("День господень", "День Господень"),
    ("день господень", "день Господень"),
    ("матфей", "Матфей"),
    ("матфея", "Матфея"),
    ("матфею", "Матфею"),
    ("матфеем", "Матфеем"),
)


_GREEK_RE = re.compile(r"[Ͱ-Ͽἀ-῿]")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
# AUDIT R40: плоский матч токена (без lookahead) — O(N). Прежний lookahead-
# вариант давал O(R²) на длинном word-run без греческого (снейк-кейс/слаг —
# один run из-за «_») и вешал event loop на секунды для 64KB-страницы.
_WORD_TOKEN_RE = re.compile(r"[\wͰ-Ͽἀ-῿]+", re.UNICODE)


def find_mixed_greek_cyrillic_tokens(text: str) -> list[str]:
    """Return word-like tokens that mix Greek and Cyrillic letters.

    Page-level text often contains both scripts legitimately (Russian prose +
    Greek lemmas). The bug is a *single token* like ``μεлеτάω`` or
    ``ὑπόκрисις`` that contains letters from both scripts.
    """
    if not text:
        return []
    # AUDIT R40: дешёвый гейт — mixed-токен требует ОБА алфавита; нет одного из
    # них во всём тексте → выходим до посимвольного скана.
    if not (_GREEK_RE.search(text) and _CYRILLIC_RE.search(text)):
        return []
    seen: list[str] = []
    for m in _WORD_TOKEN_RE.finditer(text):
        token = m.group(0)
        if _GREEK_RE.search(token) and _CYRILLIC_RE.search(token) and token not in seen:
            seen.append(token)
    return seen


def has_mixed_greek_cyrillic(text: str) -> bool:
    """True if at least one token mixes Greek and Cyrillic letters."""
    return bool(find_mixed_greek_cyrillic_tokens(text))


def _cap_first(value: str) -> str:
    for i, ch in enumerate(value):
        if ch.isalpha():
            return value[:i] + ch.upper() + value[i + 1:]
    return value


_THIRD_PERSON_PREFIX_RE = re.compile(
    r"(^|[.!?…]\s+)(?:(?:Джон\s+)?МакАртур|(?:Алексей\s+)?Коломийцев|(?:Лигон\s+)?Данкан|МакАртур|Данкан|Молер|Девер|Лоусон|Бики|Пеннингтон|Бокам|Риккарди|автор|проповедник|спикер|лектор)\s+"
    r"(?:подробно\s+|последовательно\s+|прямо\s+|настойчиво\s+)?"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает|разворачивает|обращается|проводит|настаивает|связывает|разбирает)"
    r"[^.?!…]{0,180}?,\s*(?:говоря\s+о\s+том,\s*)?что\s+([а-яёa-z])",
    re.IGNORECASE,
)

_THIRD_PERSON_WHEN_RE = re.compile(
    r"(^|[.!?…]\s+)(?:(?:Джон\s+)?МакАртур|(?:Алексей\s+)?Коломийцев|(?:Лигон\s+)?Данкан|МакАртур|Данкан|Молер|Девер|Лоусон|Бики|Пеннингтон|Бокам|Риккарди|автор|проповедник|спикер|лектор)\s+"
    r"(?:подробно\s+|последовательно\s+|прямо\s+|настойчиво\s+)?"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает|разворачивает|обращается|проводит|настаивает|связывает|разбирает)"
    r"[^.?!…]{0,180}?,\s*когда\s+говорит\s+о\s+",
    re.IGNORECASE,
)

_THIRD_PERSON_HOW_RE = re.compile(
    r"(^|[.!?…]\s+)(?:(?:Джон\s+)?МакАртур|(?:Алексей\s+)?Коломийцев|(?:Лигон\s+)?Данкан|МакАртур|Данкан|Молер|Девер|Лоусон|Бики|Пеннингтон|Бокам|Риккарди|автор|проповедник|спикер|лектор)\s+"
    r"(?:подробно\s+|последовательно\s+|прямо\s+|настойчиво\s+)?"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает|разворачивает|обращается|проводит|настаивает|связывает|разбирает)"
    r"[^.?!…]{0,180}?,\s*как\s+([а-яёa-z])",
    re.IGNORECASE,
)

_THIRD_PERSON_THIS_LAYER_RE = re.compile(
    r"(^|[.!?…]\s+)(?:(?:Джон\s+)?МакАртур|(?:Алексей\s+)?Коломийцев|(?:Лигон\s+)?Данкан|МакАртур|Данкан|Молер|Девер|Лоусон|Бики|Пеннингтон|Бокам|Риккарди|автор|проповедник|спикер|лектор)\s+"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|вскрывает|раскрывает)"
    r"\s+этот\s+смысловой\s+пласт\s+",
    re.IGNORECASE,
)


def scrub_third_person_phrases(text: str) -> str:
    """Remove common third-person analytic wrappers from generated prose.

    This is deliberately conservative: it only strips wrappers that introduce
    the real content with ``что`` or ``когда говорит о``. It does not try to
    rewrite arbitrary sentences.
    """
    if not text:
        return text

    def repl_that(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()

    text = _THIRD_PERSON_PREFIX_RE.sub(repl_that, text)
    text = _THIRD_PERSON_HOW_RE.sub(repl_that, text)
    text = _THIRD_PERSON_THIS_LAYER_RE.sub(lambda m: m.group(1) + "Этот смысловой пласт ", text)
    text = _THIRD_PERSON_WHEN_RE.sub(lambda m: m.group(1) + "Речь идёт о ", text)
    return text


def normalize_source_map_text(line: str) -> str:
    """Normalize bibliography/source-map card headings.

    Delegates canonical author/title handling to core.source_titles while
    preserving the legacy public function used across postprocessors/tests.
    """
    return normalize_source_card_line(line, prefer_original=True)


def normalize_common_typos(text: str, *, source_map: bool = True) -> str:
    """Fix narrow, recurring Russian typos from Gemini/ASR output.

    This is intentionally not a general grammar corrector. It only patches
    unambiguous regressions observed in production pages, so it is safe for
    titles, TOC entries, captions and Telegraph body text.
    """
    if not text:
        return text
    for src, dst in _COMMON_TYPO_REPLACEMENTS:
        text = text.replace(src, dst)
    if source_map:
        # source-map normalizer сам сохранит оригинальные английские имена в
        # скобках (John MacArthur), а затем безопасно нормализует отображаемого
        # автора. При source_map=False не трогаем имена: это opt-out для audit.
        mapped = normalize_source_map_text(text)
        if mapped != text:
            return mapped
        # Уже каноническая markdown source-card строка («• **Title**, Автор
        # (Original, English Author)»). Не прогоняем normalize_person_names,
        # иначе verifier в скобках становится русским: (Strange Fire, Джон МакАртур).
        if (re.match(r"^\s*[•\-]\s+\*\*", text)
                and re.search(r"\([^)]*[A-Za-z]{3,}[^)]*\)", text)):
            return text
        return normalize_person_names(text)
    return text


_INLINE_SCRUB_PATTERNS = [
    # Только строки-маркеры которые встречаются внутри текста как вставки
    r"cocoon\s*ai\s*summary",
    r"\bcocoon\b",
    r"content\s*summary",
    r"ai\s+summary",
    r"summary by[^.]*",
    r"auto.?generated[^.]*",
    r"generated by[^.]*",
    r"powered by[^.]*",
    r"\bbyline\b",
    r"\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}",    # "March 7 at 5:11"
    r"текст\s+подготовлен\s+с\s+помощью[^.]*",
    r"подготовлен[оа]?\s+с\s+помощью\s+gemini\s*ai[^.]*",
]

# BUG-R3-01: git/tech английские слова, попадающие в РУССКИЙ текст через
# YouTube-субтитры или транскрипцию Whisper — «опубли commit вавшего».
# FIX AUDIT R4: скраб только в кириллическом контексте. Безусловный \b-паттерн
# вырезал обычные английские слова из цитат Писания: "Commit your way to the
# LORD" (Пс 36:5), "a righteous Branch" (Иер 23:5) — поля en_raw/quote портились.
_GIT_NOISE_RE = re.compile(
    r"(?:(?<=[а-яА-ЯёЁ])|^)[ \t]*"
    r"\b(?:commit|push|merge|branch|diff|rebase|checkout|stash|fetch|pull request)\b"
    r"[ \t]*(?=[а-яА-ЯёЁ]|$)",
    re.IGNORECASE,
)


def _scrub_inline(text: str) -> str:
    """Удаляет мусорные AI/meta-фразы встречающиеся внутри строки.
    НЕ трогает пунктуацию, края строки и смысловые пробелы —
    чтобы не ломать богословский текст, scripture, переводческие блоки."""
    if not text:
        return text
    for pat in _INLINE_SCRUB_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    # git-шум вычищаем только рядом с кириллицей; заменяем пробелом, чтобы
    # не склеивать соседние русские слова («опубликовал push серию»).
    text = _GIT_NOISE_RE.sub(" ", text)
    text = normalize_common_typos(text)
    text = scrub_third_person_phrases(text)
    # Схлопываем только множественные пробелы/табы внутри строки
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text

def _clean_meta_line(s: str) -> str:
    """Берёт первую осмысленную строку, отфильтровывая мусорные метки платформ."""
    s = (s or "").strip()
    if not s:
        return ""
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if any(re.search(p, low) for p in BAD_META_PATTERNS):
            continue
        return ln
    return ""

def _clean_field(s: str) -> str:
    """Очищает произвольное текстовое поле — возвращает пустую строку если мусор."""
    s = (s or "").strip()
    if not s:
        return ""
    if any(re.search(p, s.lower(), re.IGNORECASE) for p in BAD_META_PATTERNS):
        return ""
    if s[-1] not in '.!?…':
        # #82: не добавляем точку если строка заканчивается на .) — напр. «агнец (лат.)»
        # FIX: не добавляем точку если строка заканчивается на *, ), ], url-подобный текст
        if not re.search(r'(?:\.|\)|\*|\]|/[a-z])\s*$', s):
            s = s + '.'
    return s

def _clean_list(lst: list) -> list:
    """Фильтрует список строк, убирая мусорные элементы."""
    return [_clean_field(str(item)) for item in (lst or []) if _clean_field(str(item))]


def _strip_meta_lines(text: str) -> str:
    """Удаляет строки, соответствующие BAD_META_PATTERNS, возвращает чистый текст."""
    if not text:
        return ""
    clean_lines = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            clean_lines.append(ln)
            continue
        if any(re.search(p, stripped, re.IGNORECASE) for p in BAD_META_PATTERNS):
            continue
        clean_lines.append(ln)
    return "\n".join(clean_lines).strip()


def _has_dirty_meta(text: str) -> bool:
    """True если в тексте есть хотя бы одна мусорная строка."""
    for ln in (text or "").splitlines():
        if ln.strip() and any(re.search(p, ln.strip(), re.IGNORECASE) for p in BAD_META_PATTERNS):
            return True
    return False


def is_meta_garbage(s: str) -> bool:
    """True если строка целиком является мусором (summary/byline/cocoon и т.п.)."""
    s = (s or "").strip()
    if not s:
        return True
    return any(re.search(p, s, re.IGNORECASE) for p in BAD_META_PATTERNS)


def normalize_author_name(s: str) -> str:
    """Нормализует имя автора: убирает мусор, длинные тире → дефис, схлопывает пробелы."""
    s = _strip_meta_lines((s or "").strip())
    if not s or is_meta_garbage(s):
        return ""
    s = re.sub(r"[—–]", "-", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = s.strip("-").strip()
    s = normalize_common_typos(s)
    return s


def join_title_author(title: str, author: str) -> str:
    """Склеивает «{title} — {author}», НЕ дублируя имя автора.

    Видео-тайтлы часто уже содержат имя проповедника в конце
    («…Приводящий в Изумление Раб Иеговы【исаия 53】Джон МакАртур»), и наивное
    `f"{title} — {author}"` давало «…Джон МакАртур — Джон МакАртур».
    Если очищенный хвост заголовка уже совпадает с именем автора — возвращаем
    заголовок как есть.
    """
    title = (title or "").strip()
    author = (author or "").strip()
    if not author or author == "Автор не указан":
        return title
    if not title:
        return author

    def _norm(s: str) -> str:
        # регистронезависимо, без пунктуации/скобок/разделителей — только слова
        return re.sub(r"[\W_]+", " ", s, flags=re.UNICODE).strip().lower()

    nt, na = _norm(title), _norm(author)
    # Уже оканчивается именем автора (как отдельным словосочетанием) — не дублируем.
    if na and (nt == na or nt.endswith(" " + na)):
        return title
    return f"{title} — {author}"


_LAT2CYR_HOMOGLYPHS = str.maketrans("AaBCcEeHKMOoPpTXxy", "АаВСсЕеНКМОоРрТХху")


def _fix_latin_homoglyphs(s: str) -> str:
    """AUDIT R8: одиночные латинские буквы-гомоглифы внутри кириллических слов
    («Cемья» с латинской C) → кириллица. Слова с реальными латинскими
    последовательностями (QA, YouTube) не трогаем."""
    out = []
    for w in re.split(r"(\s+)", s or ""):
        if re.search(r"[А-Яа-яЁё]", w) and re.search(r"[A-Za-z]", w):
            if all(len(run) == 1 for run in re.findall(r"[A-Za-z]+", w)):
                w = w.translate(_LAT2CYR_HOMOGLYPHS)
        out.append(w)
    return "".join(out)


def normalize_title_text(s: str) -> str:
    """Нормализует заголовок: убирает ведущий номер серии, имя автора в скобках, лишние разделители."""
    s = _strip_meta_lines((s or "").strip())
    if not s or is_meta_garbage(s):
        return ""
    s = _fix_latin_homoglyphs(s)
    # R49: полноширинные CJK-скобки 【…】 из ютуб-тайтлов инородны в русском
    # заголовке («…Раб Иеговы【исаия 53】Джон МакАртур») — в обычные круглые.
    s = re.sub(r"\s*【\s*", " (", s)
    s = re.sub(r"\s*】\s*", ") ", s)

    s = re.sub(r"^\d+\s*[|:]\s*", "", s)
    s = re.sub(r"^№\d+\s*[|:\-]?\s*", "", s)
    s = re.sub(r"^[Ss]\d+[Ee]\d+\s*[|:\-]?\s*", "", s)
    s = re.sub(r"^[Ee]pisode\s+\d+\s*[|:\-]?\s*", "", s, flags=re.IGNORECASE)

    m = re.search(r"\(([^)]{2,50})\)\s*$", s)
    if m:
        candidate = m.group(1).strip()
        words = candidate.split()
        meaningful_words = [w for w in words if w]
        word_count = len(meaningful_words)
        if word_count in (2, 3, 4) and meaningful_words:
            _NOT_NAME_WORDS = {
                "часть", "серия", "том", "выпуск", "новый", "старый",
                "ветхий", "завет", "глава", "книга", "версия",
                "old", "new", "part", "vol", "series", "chapter",
                "version", "official", "audio", "video", "live",
                "full", "hd", "original", "remix", "edit",
            }
            looks_like_name = all(
                w[0].isupper()
                and not any(c.isdigit() for c in w)
                and w.lower() not in _NOT_NAME_WORDS
                for w in meaningful_words
            )
            if looks_like_name:
                s = re.sub(r"\s*\([^)]{2,50}\)\s*$", "", s)

    s = re.sub(r"[\s|]+$", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"(?<!\.)\.(?!\.)$", "", s).strip()
    s = normalize_common_typos(s)

    return s



# Слова/акронимы которые нельзя трогать при title case
_PRESERVE_CASE: frozenset = frozenset({
    # Версии Библии и богословские аббревиатуры
    "ESV", "KJV", "NASB", "NIV", "LSB", "NLT", "CSB", "NKJV", "RSV", "NET",
    "NRSV", "LEB", "ASV", "LBCF", "LBCF1689", "WCF", "TULIP",
    # Форматы
    "Q&A", "QA",
    # Технические/бренды
    "YouTube", "RuTube", "VK", "iPhone", "iPad",
    # Языки оригинала
    "NA28", "BHS", "LXX",
})


_RU_TITLE_PRESERVE_WORDS = {
    "Бог", "Бога", "Богу", "Богом", "Боге", "Слово", "Слова", "Слову", "Божий", "Божья", "Божье", "Божьего", "Божьей",
    "Господь", "Господа", "Господу", "Господень", "Господня", "Господню", "Господнем", "Христос", "Христа", "Христу", "Иисус", "Иисуса", "Иисусу",
    "Дух", "Духа", "Духу", "Святой", "Святого", "Троица", "Троицы",
    "Писание", "Писания", "Писанию", "Писанием", "Писании",
    "Библия", "Библии", "Библию", "Библией",
    "Евангелие", "Евангелия", "Псалом", "Псалма", "Исаия", "Исаии", "Матфея", "Марка", "Луки", "Иоанна", "Римлянам",
    "Оправдание", "Освящение", "Искупление", "Завет", "Благодать", "Праведность", "Покаяние",
    "Провидение", "Предопределение", "Возрождение", "Богодухновенность", "Библеистика", "Экзегеза", "Герменевтика",
}


def _is_cyrillic_dominant(text: str) -> bool:
    cyr = len(re.findall(r"[А-Яа-яЁё]", text or ""))
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    return cyr > 0 and cyr >= lat * 2


def sentence_case_russian_title(s: str, aggressive_title_case: bool = False) -> str:
    """Russian-friendly title casing.

    English Title Case looks unnatural in Russian: «Вопросы и Ответы»,
    «Как Проповедовать Пламенно». This helper lowercases ordinary Russian words
    while preserving first word, biblical/divine names, acronyms, Latin titles,
    digits and internal-cap proper names like «МакАртур».

    2026-06-11: aggressive_title_case=True (для заголовков) делает заглавной
    каждую букву каждого значимого слова, как того хочет пользователь.
    """
    if not s or not _is_cyrillic_dominant(s):
        return s

    def split_edges(token: str) -> tuple[str, str, str]:
        m = re.match(r"^([^А-Яа-яЁёA-Za-z0-9]*)(.*?)([^А-Яа-яЁёA-Za-z0-9]*)$", token)
        if not m:
            return "", token, ""
        return m.group(1), m.group(2), m.group(3)

    def cap_word(word: str) -> str:
        if not word:
            return word
        return word[0].upper() + word[1:]

    out: list[str] = []
    force_cap_next = True
    _PRESERVE_CANONICAL = {w.lower(): w for w in _RU_TITLE_PRESERVE_WORDS}
    
    # Служебные слова, которые не капитализируем в середине даже при агрессивном Title Case
    _LOWER_RU = {"и", "а", "но", "или", "да", "не", "ни", "же", "ли", "бы", "в", "на", "за", "из", "по", "к", "с", "о", "у", "до", "об", "от", "под", "над", "при", "про", "без", "для"}

    words = s.split()
    for idx, raw in enumerate(words):
        prefix, core, suffix = split_edges(raw)
        if not core:
            out.append(raw)
            continue
        
        preserve_key = core.lower()
        preserve = (
            core in _RU_TITLE_PRESERVE_WORDS
            or preserve_key in _PRESERVE_CANONICAL
            or bool(re.search(r"[A-Za-z0-9]", core))
            or re.fullmatch(r"[А-ЯA-Z]", core)
            or (len(core) > 1 and core.isupper())
            or bool(re.search(r"[а-яё][А-ЯЁ]", core))  # МакАртур
            or bool(re.search(r"-[А-ЯЁ]", core))       # Ллойд-Джонс
        )
        
        if preserve:
            new_core = _PRESERVE_CANONICAL.get(preserve_key, core)
        elif aggressive_title_case:
            # Агрессивный режим: капитализируем всё, кроме коротких союзов/предлогов в середине
            if idx == 0 or idx == len(words) - 1 or core.lower() not in _LOWER_RU:
                new_core = cap_word(core.lower())
            else:
                new_core = core.lower()
        elif force_cap_next:
            new_core = cap_word(core.lower())
        else:
            new_core = core.lower()
            
        out.append(prefix + new_core + suffix)
        force_cap_next = bool(re.search(r"[.!?]$", suffix))
        
    result = " ".join(out)
    return normalize_person_names(result)


def title_case_fragment(s: str) -> str:
    """
    Title Case для названий фрагментов (Shorts / Clips).
    Каждое слово с заглавной буквы, кроме коротких предлогов/союзов
    в середине фразы. Акронимы и слова из _PRESERVE_CASE не трогаются.
    """
    if not s:
        return s
        
    # ПРАВИЛО ПРОЕКТА (AGENTS.md, подтверждено оператором 2026-07-05):
    # русские названия материалов — Title Case: Каждое Значимое Слово
    # с Заглавной, кроме предлогов/союзов. Регрессия sentence-case откачена.
    if _is_cyrillic_dominant(s):
        return sentence_case_russian_title(s, aggressive_title_case=True)

    _LOWER_MID = {
        "в", "на", "за", "из", "по", "к", "с", "о", "у", "до", "об", "от",
        "под", "над", "при", "про", "без", "для", "через", "между",
        "и", "а", "но", "или", "да", "не", "ни", "же", "ли", "бы",
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
        "or", "but", "nor", "as", "by", "up",
    }

    def _capitalize_first_letter(word: str) -> str:
        """Капитализирует первую БУКВУ в слове, пропуская ведущую пунктуацию."""
        for i, ch in enumerate(word):
            if ch.isalpha():
                return word[:i] + ch.upper() + word[i + 1:]
        return word

    words = s.split()
    result = []
    for i, word in enumerate(words):
        if word in _PRESERVE_CASE or word.upper() in _PRESERVE_CASE:
            canonical = next((w for w in _PRESERVE_CASE if w.upper() == word.upper()), word)
            result.append(canonical)

        elif len(word) >= 2 and word.isupper() and word.isalpha():
            result.append(word)

        elif i == 0 or i == len(words) - 1:
            result.append(_capitalize_first_letter(word))

        elif word.lower() in _LOWER_MID:
            result.append(word.lower())

        else:
            result.append(_capitalize_first_letter(word))

    return " ".join(result)

def _filter_times_str(times_str: str, duration: int) -> str:
    """
    Фильтрует строку с таймкодами: оставляет только те, что не превышают duration.
    Автокоррекция: H:MM:SS → MM:SS для видео короче часа (типичная ошибка Gemini).
    """
    if not times_str or not duration:
        return times_str or ""
    times_str = re.sub(r"[•·]", ", ", times_str)
    tokens = [t.strip('.,;:!? ') for t in re.split(r"[,\s]+", times_str) if t.strip()]
    good: list[str] = []
    for token in tokens:
        secs = time_to_seconds(token)
        if secs is None:
            continue
        if duration < 3600 and secs > duration:
            parts = token.split(":")
            if len(parts) == 3:
                _mins = str(int(parts[1]))  # strip leading zero: '00' → '0'
                corrected = f"{_mins}:{parts[2]}"
                corrected_secs = time_to_seconds(corrected)
                if corrected_secs is not None and corrected_secs <= duration:
                    good.append(corrected)
                    continue
            continue
        if secs <= duration:
            good.append(token)
    return ", ".join(good)



_HASHTAG_CANONICAL = {
    "БиблейскоеСемейство": "БиблейскаяСемья",
    "Богомыслие": "Богословие",
    "СемейнаяЖизнь": "ХристианскаяСемья",
    "БракИСемья": "ХристианскийБрак",
}

def normalize_hashtag(tag: str) -> str:
    """Нормализует хэштег без потери уже валидного CamelCase.

    Единственная каноническая реализация (DRY) — ранее дублировалась
    в services/shorts_candidates.py и core/json_parser.py.

    Примеры:
      'реформированный баптист' → '#РеформированныйБаптист'
      'ПолВошер'                → '#ПолВошер'  (НЕ '#Полвошер')
      'НовоеТворение'           → '#НовоеТворение'
      'личная_встреча'          → '#ЛичнаяВстреча'
    """
    # AUDIT R40: strip ПЕРЕД lstrip('#') (иначе «  #  🎯 » → «##🎯»); и отвергаем
    # тег без словных символов (эмодзи/пунктуация-only → '', а не '#🔥'/'#...').
    tag = str(tag).strip().lstrip("#").strip()
    if not tag or not re.search(r"\w", tag):
        return ""
    words = [w for w in re.split(r"[\s_\-]+", tag) if w]
    if not words:
        return ""
    if len(words) == 1:
        w0 = words[0]
        tag_body = w0[0].upper() + w0[1:]
    else:
        tag_body = "".join((w[0].upper() + w[1:]) for w in words)
    tag_body = _HASHTAG_CANONICAL.get(tag_body, tag_body)
    return "#" + tag_body
