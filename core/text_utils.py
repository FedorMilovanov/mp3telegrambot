#!/usr/bin/env python3
"""
Текстовые утилиты — очистка, нормализация, title case.
Извлечено из bot.py строки 1587–1845.
"""
import re
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
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s*\d{4}",
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
    ("Слово Божьего", "Слова Божьего"),
    ("авторитет Слово Божьего", "авторитет Слова Божьего"),
    ("Стину Лоусону", "Стиву Лоусону"),
    ("Стин Лоусон", "Стив Лоусон"),
    ("душевпопечение", "душепопечение"),
    ("Душевпопечение", "Душепопечение"),
    ("МакАртора", "МакАртура"),
    ("епифанита", "Епафродита"),
    ("Епифанита", "Епафродита"),
    ("Странный огонь", "Чуждый огонь"),
    ("странный огонь", "чуждый огонь"),
    # Mixed Cyrillic/Greek letters in original-language terms.
    ("βασιлеία", "βασιλεία"),
    ("μορφύω", "μορφόω"),
    ("μεταμορφύω", "μεταμορφόω"),
    ("μεлеτάω", "μελετάω"),
    ("ὑπόκрисις", "ὑπόκρισις"),
)


_GREEK_RE = re.compile(r"[Ͱ-Ͽἀ-῿]")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_MIXED_GREEK_CYRILLIC_TOKEN_RE = re.compile(
    r"(?=[\wͰ-Ͽἀ-῿]*[Ͱ-Ͽἀ-῿])"
    r"(?=[\wͰ-Ͽἀ-῿]*[Ѐ-ӿ])"
    r"[\wͰ-Ͽἀ-῿]+",
    re.UNICODE,
)


def find_mixed_greek_cyrillic_tokens(text: str) -> list[str]:
    """Return word-like tokens that mix Greek and Cyrillic letters.

    Page-level text often contains both scripts legitimately (Russian prose +
    Greek lemmas). The bug is a *single token* like ``μεлеτάω`` or
    ``ὑπόκрисις`` that contains letters from both scripts.
    """
    if not text:
        return []
    seen: list[str] = []
    for m in _MIXED_GREEK_CYRILLIC_TOKEN_RE.finditer(text):
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
    r"(^|[.!?…]\s+)(?:(?:Джон\s+)?МакАртур|автор|проповедник|спикер|лектор)\s+"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает)"
    r"[^.?!…]{0,180}?,\s*(?:говоря\s+о\s+том,\s*)?что\s+([а-яёa-z])",
    re.IGNORECASE,
)

_THIRD_PERSON_WHEN_RE = re.compile(
    r"(^|[.!?…]\s+)(?:(?:Джон\s+)?МакАртур|автор|проповедник|спикер|лектор)\s+"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает)"
    r"[^.?!…]{0,180}?,\s*когда\s+говорит\s+о\s+",
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
    text = _THIRD_PERSON_WHEN_RE.sub(lambda m: m.group(1) + "Речь идёт о ", text)
    return text


_AUTHOR_DEDUPE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Кевин ДеЯнг, Грег Гилберт, Greg Gilbert,", "Кевин ДеЯнг и Грег Гилберт,"),
    ("Грег Гилберт, Greg Gilbert,", "Грег Гилберт,"),
    ("Джон МакАртур, John MacArthur,", "Джон МакАртур,"),
    ("Пол Вошер, Paul Washer,", "Пол Вошер,"),
    ("Джоэл Бики, Joel Beeke,", "Джоэл Бики,"),
)

_SOURCE_RU_WITH_ORIGINAL_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<author>[А-ЯЁA-Z][^,\n]{1,80}),\s+"
    r"(?P<ru_title>[^()\n]{3,140}?)\s*"
    r"\(\s*(?P<en_author>[A-Z][A-Za-z .’'\-]{2,80}),\s*"
    r"(?P<en_title>[A-Za-z][^()]{2,180})\s*\)"
    r"(?P<tail>\.?\s*)$"
)


def normalize_source_map_text(line: str) -> str:
    """Normalize bibliography/source-map card headings.

    Prefer verifiable original English titles over invented Russian titles, and
    dedupe bilingual author echoes. Safe for a single source-card line; for
    normal prose it is a no-op.
    """
    if not line:
        return line
    out = line
    for src, dst in _AUTHOR_DEDUPE_REPLACEMENTS:
        out = out.replace(src, dst)
    m = _SOURCE_RU_WITH_ORIGINAL_RE.match(out.strip())
    if m and re.search(r"[A-Za-z]", m.group("en_title")):
        bullet = m.group("bullet") or ""
        author = m.group("author").strip()
        en_title = m.group("en_title").strip().rstrip(".")
        out = f"{bullet}{author}, {en_title}."
    return out


def normalize_common_typos(text: str) -> str:
    """Fix narrow, recurring Russian typos from Gemini/ASR output.

    This is intentionally not a general grammar corrector. It only patches
    unambiguous regressions observed in production pages, so it is safe for
    titles, TOC entries, captions and Telegraph body text.
    """
    if not text:
        return text
    for src, dst in _COMMON_TYPO_REPLACEMENTS:
        text = text.replace(src, dst)
    text = normalize_source_map_text(text)
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
    # BUG-R3-01: git/tech английские слова попадающие в русский текст через
    # YouTube-субтитры или транскрипцию Whisper — «опубли commit вавшего»
    r"\b(commit|push|merge|branch|diff|rebase|checkout|stash|fetch|pull request)\b",
]


def _scrub_inline(text: str) -> str:
    """Удаляет мусорные AI/meta-фразы встречающиеся внутри строки.
    НЕ трогает пунктуацию, края строки и смысловые пробелы —
    чтобы не ломать богословский текст, scripture, переводческие блоки."""
    if not text:
        return text
    for pat in _INLINE_SCRUB_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
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


def normalize_title_text(s: str) -> str:
    """Нормализует заголовок: убирает ведущий номер серии, имя автора в скобках, лишние разделители."""
    s = _strip_meta_lines((s or "").strip())
    if not s or is_meta_garbage(s):
        return ""

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


def title_case_fragment(s: str) -> str:
    """
    Title Case для названий фрагментов (Shorts / Clips).
    Каждое слово с заглавной буквы, кроме коротких предлогов/союзов
    в середине фразы. Акронимы и слова из _PRESERVE_CASE не трогаются.
    """
    if not s:
        return s

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
                corrected = f"{parts[1]}:{parts[2]}"
                corrected_secs = time_to_seconds(corrected)
                if corrected_secs is not None and corrected_secs <= duration:
                    good.append(corrected)
                    continue
            continue
        if secs <= duration:
            good.append(token)
    return ", ".join(good)



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
    tag = str(tag).lstrip("#").strip()
    if not tag:
        return ""
    words = [w for w in re.split(r"[\s_\-]+", tag) if w]
    if not words:
        return ""
    if len(words) == 1:
        w0 = words[0]
        return "#" + (w0[0].upper() + w0[1:])
    return "#" + "".join((w[0].upper() + w[1:]) for w in words)
