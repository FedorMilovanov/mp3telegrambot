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
from dataclasses import dataclass

from core.person_names import canonical_person_name, normalize_person_names

AUTHOR_CANONICAL: dict[str, str] = {
    "John MacArthur": "Джон МакАртур",
    "R.C. Sproul": "Р. Ч. Спроул",
    "R. C. Sproul": "Р. Ч. Спроул",
    "Paul Washer": "Пол Вошер",
    "Joel Beeke": "Джоэль Бики",
    "Greg Gilbert": "Грег Гилберт",
    "Kevin DeYoung": "Кевин ДеЯнг",
    "Sinclair Ferguson": "Синклер Фергюсон",
    "John Owen": "Джон Оуэн",
    "John Bunyan": "Джон Баньян",
    "John G. Paton": "Джон Патон",
    "John Gibson Paton": "Джон Патон",
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
    "John Piper": "Джон Пайпер",
    "Alistair Begg": "Алистер Бегг",
    "Steven Lawson": "Стивен Лоусон",
    "Sinclair Ferguson": "Синклер Фергюсон",
    "Derek Prince": "Дерек Принс",
    "David Platt": "Дэвид Платт",
    "Francis Chan": "Фрэнсис Чан",
    "Matt Chandler": "Мэтт Чендлер",
    "Elisabeth Elliot": "Элизабет Эллиот",
    "Rosaria Butterfield": "Розария Баттерфилд",
    "Jackie Hill Perry": "Джеки Хилл Перри",
    "Martyn Lloyd-Jones": "Мартин Лойд-Джонс",
    "George Müller": "Георг Мюллер",
    "Thomas Watson": "Томас Уотсон",
    "Jonathan Edwards": "Джонатан Эдвардс",
    "Martyn Lloyd-Jones": "Мартин Лойд-Джонс",
    "Martyn Ллойд-Джонс": "Мартин Лойд-Джонс",

    "John Murray": "Джон Мюррей",
    "B.B. Warfield": "Б. Б. Уорфилд",
    "B. B. Warfield": "Б. Б. Уорфилд",
    "J.C. Ryle": "Дж. Ч. Райл",
    "J. C. Ryle": "Дж. Ч. Райл",
    "Louis Berkhof": "Луис Беркхоф",
    "Jerry Bridges": "Джерри Бриджес",
    "Thomas Brooks": "Томас Брукс",
    "Athanasius": "Афанасий Великий",
    "Augustine": "Августин",
    "Herman Bavinck": "Герман Бавинк",
    "Robert Letham": "Роберт Летэм",
    "Richard Gaffin": "Ричард Гаффин",
    "Anthony Hoekema": "Энтони Хокема",
    "George Eldon Ladd": "Джордж Элдон Лэдд",
    "O. Palmer Robertson": "О. Палмер Робертсон",
    "Michael Horton": "Майкл Хортон",
    "Edmund Clowney": "Эдмунд Клауни",
    "Bryan Chapell": "Брайан Чапелл",
    "Graeme Goldsworthy": "Грэм Голдсуорси",
    "Sidney Greidanus": "Сидни Грейданус",
    "Peter Masters": "Питер Мастерс",
    "J. Gresham Machen": "Дж. Грешем Мейчен",
    "Iain Murray": "Иэн Мюррей",
    "Arnold Dallimore": "Арнольд Даллимор",
    "Michael Haykin": "Майкл Хейкин",

    # Surname aliases used in source_packs.py. Keep these deterministic so
    # source-card rendering can canonicalize pack entries without asking Gemini
    # to invent Russian names.
    "MacArthur": "Джон МакАртур",
    "Sproul": "Р. Ч. Спроул",
    "Washer": "Пол Вошер",
    "Warfield": "Б. Б. Уорфилд",
    "Beeke": "Джоэль Бики",
    "Gilbert": "Грег Гилберт",
    "DeYoung": "Кевин ДеЯнг",
    "Ferguson": "Синклер Фергюсон",
    "Owen": "Джон Оуэн",
    "Bunyan": "Джон Баньян",
    "Calvin": "Жан Кальвин",
    "Spurgeon": "Чарльз Сперджен",
    "Fuller": "Эндрю Фуллер",
    "Packer": "Дж. И. Пакер",
    "Carson": "Д. А. Карсон",
    "Baucham": "Водди Бокам",
    "Motyer": "Алек Мотьер",
    "Young": "Эдвард Янг",
    "Ryle": "Дж. Ч. Райл",
    "Letham": "Роберт Летэм",
    "Edwards": "Джонатан Эдвардс",
    "Chou": "Абнер Чау",
    "Ryrie": "Чарльз Райри",
    "Piper": "Джон Пайпер",
    "Begg": "Алистер Бегг",
    "Lawson": "Стивен Лоусон",
    "Berkhof": "Луис Беркхоф",
    "Bavinck": "Герман Бавинк",
    "Hoekema": "Энтони Хокема",
    "Ladd": "Джордж Элдон Лэдд",
    "Clowney": "Эдмунд Клауни",
    "Horton": "Майкл Хортон",
    "Lloyd-Jones": "Мартин Лойд-Джонс",
    "Chapell": "Брайан Чапелл",
    "Goldsworthy": "Грэм Голдсуорси",
    "Greidanus": "Сидни Грейданус",
    "Masters": "Питер Мастерс",
    "Robertson": "О. Палмер Робертсон",
    "Machen": "Дж. Грешем Мейчен",
    "Dallimore": "Арнольд Даллимор",
    "Haykin": "Майкл Хейкин",
    "Watson": "Томас Уотсон",
    "Bridges": "Джерри Бриджес",
    "Brooks": "Томас Брукс",
    "Burroughs": "Джеремайя Берроуз",
    "Frame": "Джон Фрейм",
    "Gaffin": "Ричард Гаффин",
    "Grogan": "Джеффри Гроган",
    "Kaiser": "Уолтер Кайзер",
    "Luther": "Мартин Лютер",
    "Macleod": "Дональд Маклеод",
    "Oswalt": "Джон Освальт",
    "Whitney": "Дональд Уитни",
    "Witsius": "Герман Витсиус",
    "Dever": "Марк Девер",
    "Murray": "Джон Мюррей",
}
_ORIGINAL_AUTHOR_ALIASES: dict[str, str] = {
    "MacArthur": "John MacArthur",
    "Sproul": "R.C. Sproul",
    "Washer": "Paul Washer",
    "Warfield": "B.B. Warfield",
    "Beeke": "Joel Beeke",
    "Gilbert": "Greg Gilbert",
    "DeYoung": "Kevin DeYoung",
    "Ferguson": "Sinclair Ferguson",
    "Owen": "John Owen",
    "Bunyan": "John Bunyan",
    "Calvin": "John Calvin",
    "Spurgeon": "Charles Spurgeon",
    "Fuller": "Andrew Fuller",
    "Packer": "J.I. Packer",
    "Carson": "D.A. Carson",
    "Baucham": "Voddie Baucham",
    "Motyer": "Alec Motyer",
    "Young": "Edward J. Young",
    "Ryle": "J.C. Ryle",
    "Letham": "Robert Letham",
    "Edwards": "Jonathan Edwards",
    "Chou": "Abner Chou",
    "Ryrie": "Charles Ryrie",
    "Piper": "John Piper",
    "Begg": "Alistair Begg",
    "Lawson": "Steven Lawson",
    "Berkhof": "Louis Berkhof",
    "Bavinck": "Herman Bavinck",
    "Hoekema": "Anthony Hoekema",
    "Ladd": "George Eldon Ladd",
    "Clowney": "Edmund Clowney",
    "Horton": "Michael Horton",
    "Lloyd-Jones": "Martyn Lloyd-Jones",
    "Chapell": "Bryan Chapell",
    "Goldsworthy": "Graeme Goldsworthy",
    "Greidanus": "Sidney Greidanus",
    "Masters": "Peter Masters",
    "Robertson": "O. Palmer Robertson",
    "Machen": "J. Gresham Machen",
    "Dallimore": "Arnold Dallimore",
    "Haykin": "Michael Haykin",
    "Watson": "Thomas Watson",
    "Bridges": "Jerry Bridges",
    "Brooks": "Thomas Brooks",
    "Burroughs": "Jeremiah Burroughs",
    "Frame": "John Frame",
    "Gaffin": "Richard Gaffin",
    "Grogan": "Geoffrey Grogan",
    "Kaiser": "Walter Kaiser",
    "Luther": "Martin Luther",
    "Macleod": "Donald Macleod",
    "Oswalt": "John Oswalt",
    "Whitney": "Donald Whitney",
    "Witsius": "Herman Witsius",
    "Dever": "Mark Dever",
    "Murray": "John Murray",
}


OFFICIAL_RU_TITLES: dict[tuple[str, str], str] = {
    ("John MacArthur", "Strange Fire"): "Чуждый огонь",
    ("John MacArthur", "Ashamed of the Gospel"): "Стыжусь ли я Евангелия?",
    ("R.C. Sproul", "The Holiness of God"): "Святость Бога",
    ("R. C. Sproul", "The Holiness of God"): "Святость Бога",
    ("John Owen", "The Death of Death in the Death of Christ"): "Смерть смерти в смерти Христа",
    ("John Owen", "Of the Mortification of Sin in Believers"): "Об умерщвлении греха в верующих",
    ("John Bunyan", "The Pilgrim's Progress"): "Путешествие Пилигрима",
    ("Andrew Fuller", "The Gospel Worthy of All Acceptation"): "Евангелие, достойное всякого принятия",
    ("John Calvin", "Commentary on Isaiah"): "Комментарии на Исаию",
    ("John Calvin", "Commentaries on Isaiah"): "Комментарии на Исаию",
    ("John Calvin", "Institutes of the Christian Religion"): "Наставление в христианской вере",
    ("Jonathan Edwards", "Religious Affections"): "Религиозные чувства",

    ("John Murray", "Redemption Accomplished and Applied"): "Искупление совершённое и применённое",
    ("J.C. Ryle", "Holiness"): "Святость",
    ("J. C. Ryle", "Holiness"): "Святость",
    ("Jerry Bridges", "The Pursuit of Holiness"): "Стремление к святости",
    ("Thomas Watson", "The Doctrine of Repentance"): "Учение о покаянии",
}

RU_TITLE_CORRECTIONS: dict[str, str] = {
    "Странный огонь": "Чуждый огонь",
    "странный огонь": "чуждый огонь",
}

DISALLOWED_SOURCE_AUTHORS: set[str] = {
    "Tim Keller", "Timothy Keller", "Keller", "Тим Келлер", "Тимоти Келлер",
}

# FIX AUDIT R4: денилист по подстроке «Keller» ловил и других авторов
# (W. Phillip Keller, «A Shepherd Looks at Psalm 23» — легитимная карточка).
# Матчим полное имя с границами слов; русские формы — с падежными окончаниями.
_DISALLOWED_AUTHOR_RE = re.compile(
    r"\b(?:"
    r"tim(?:othy)?\s+(?:[a-z]\.\s*)*keller"
    r"|тим(?:оти)?[а-яё]*\s+(?:[а-яё]\.\s*)*келлер[а-яё]*"
    r")\b",
    re.IGNORECASE,
)


def is_disallowed_source_author(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    return bool(_DISALLOWED_AUTHOR_RE.search(raw))


@dataclass(frozen=True)
class SourceCard:
    """Typed source-card representation used by source normalization/rendering.

    ``title_ru`` is optional; when absent, ``title_original`` is displayed as the
    main title. ``author_original`` is retained for the parenthetical verifier.
    """
    author_ru: str
    title_original: str
    author_original: str = ""
    title_ru: str = ""
    bullet: str = ""
    why_relevant: str = ""


def render_source_card(card: SourceCard, *, trailing_period: bool = True) -> str:
    """Render source card in the project-wide title-first style.

    AUDIT R23 (user report: "• - убери ты это везде, надоедает" — the "•"
    bullet in Source Card lines): forced blank here, not just at the one
    call site that used to hardcode "• " — several repair/normalize code
    paths in this module (normalize_source_card_line) capture and REUSE
    whatever bullet character was already in raw model text via
    `bullet = <regex match>.group("bullet") or ""`. Blanking it centrally,
    at the single render choke point, guarantees no bullet resurfaces
    regardless of which caller or regex path produced the card."""
    bullet = ""
    title_original = str(card.title_original or "").strip().rstrip(".")
    title_ru = str(card.title_ru or "").strip().rstrip(".")
    author_ru = canonical_person_name(card.author_ru)
    author_original = original_author_name(card.author_original or card.author_ru)

    display_title = title_ru or title_original
    if not display_title:
        return ""

    original_bits: list[str] = []
    if title_ru and title_original and title_ru.casefold() != title_original.casefold():
        original_bits.append(title_original)
    # AUDIT R12 (дамп 2026-07-06): сравнение без учёта регистра — иначе
    # выходило «, Джон МакАртур (Джон МакАртур)»: дубль автора в скобках.
    if author_original and author_original.casefold() != author_ru.casefold():
        original_bits.append(author_original)

    rendered = f"{bullet}**{display_title}**, {author_ru}" if author_ru else f"{bullet}**{display_title}**"
    if original_bits:
        rendered += " (" + ", ".join(original_bits) + ")"
    if trailing_period and not re.search(r"[.!?]\s*$", rendered):
        rendered += "."
    return rendered


def build_source_card(
    *,
    author: str,
    title_original: str,
    original_author: str = "",
    fallback_ru_title: str = "",
    bullet: str = "",
    why_relevant: str = "",
) -> SourceCard:
    """Create a canonical SourceCard from raw model/legacy fields."""
    if is_disallowed_source_author(author) or is_disallowed_source_author(original_author):
        return SourceCard(author_ru="", title_original="", author_original="", title_ru="", bullet=bullet, why_relevant="")
    title_original = str(title_original or "").strip().rstrip(".")
    original_author = original_author_name(original_author or author)
    if is_disallowed_source_author(original_author):
        return SourceCard(author_ru="", title_original="", author_original="", title_ru="", bullet=bullet, why_relevant="")
    author_ru = canonical_author_name(author or original_author)
    title_ru = official_ru_title(original_author, title_original) or correct_known_ru_title(fallback_ru_title).strip().rstrip(".")
    if title_ru and re.search(r"[A-Za-z]", title_ru) and not re.search(r"[А-Яа-яЁё]", title_ru):
        title_ru = ""
    return SourceCard(
        author_ru=author_ru,
        title_original=title_original,
        author_original=original_author,
        title_ru=title_ru,
        bullet=bullet,
        why_relevant=str(why_relevant or "").strip(),
    )

_SOURCE_RU_WITH_ORIGINAL_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<author>[А-ЯЁA-Z][^,\n]{1,100}),\s+"
    r"(?P<ru_title>[^()\n]{3,180}?)\s*"
    r"\(\s*(?P<en_author>[A-Z][A-Za-z .’'\-]{2,100}),\s*"
    r"(?P<en_title>[A-Za-z][^()]{2,220})\s*\)"
    r"(?P<tail>\.?\s*)$"
)

_SOURCE_RU_TITLE_AUTHOR_WITH_ORIGINAL_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<ru_title>[А-ЯЁ][^,()\n]{2,140}),\s*"
    r"(?P<author>[А-ЯЁA-Z][^()\n]{2,100})\s*"
    r"\(\s*(?P<en_title>[A-Za-z][^,()]{2,220}),\s*"
    r"(?P<en_author>[A-Za-z][^)]{2,120})\s*\)"
    r"(?P<tail>\.?\s*)$"
)

_EN_AUTHOR_WITH_TITLE_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<en_author>[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z .’'\-]{2,100}),\s+"
    r"(?P<title>[A-Za-z][^\n]{2,220})$"
)

# «Похоже на имя автора»: минимум два слова с заглавной буквы; допускаются
# инициалы и соединители «и»/«and» для соавторов. Отсекает английскую прозу
# вида "Behold, I stand at the door" (en_author был бы одиночным словом).
_MULTIWORD_NAME_RE = re.compile(
    r"^[А-ЯЁA-Z][\w.’'\-]*(?:\s+(?:и|and|[А-ЯЁA-Z][\w.’'\-]*)){1,4}$"
)

_SOURCE_AUTHOR_WHY_TITLE_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<author>[A-Z][A-Za-z .’'\-]{2,100})\.\s*[—-]\s*"
    r"(?P<why>[^\n]{20,260}?),\s*"
    r"(?P<title>[A-Z][A-Za-z0-9 .:;’'\-]{3,220})\.?$"
)

_SOURCE_TITLE_AUTHOR_DUP_WHY_RE = re.compile(
    r"^(?P<bullet>\s*[•\-]\s*)?"
    r"(?P<title>[A-Z][A-Za-z0-9 .:;’'\-]{3,220}?),\s*"
    r"(?P<author>[A-Z][A-Za-z .’'\-]{2,100}),\s*"
    r"(?P=author)\.?\s*[—-]\s*"
    r"(?P<why>[^\n]{20,400})\.?$"
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


def _looks_like_source_card_line(s: str) -> bool:
    """True для строк, похожих на библиографическую карточку.

    Денилист авторов должен вырезать карточки, а не любую прозу,
    в которой упомянуто имя (например, разбор критики взглядов автора).
    """
    s = str(s or "").strip()
    if not s:
        return False
    if re.match(r"^\s*[•\-]\s+", s):
        return True
    return bool(
        _SOURCE_RU_WITH_ORIGINAL_RE.match(s)
        or _SOURCE_RU_TITLE_AUTHOR_WITH_ORIGINAL_RE.match(s)
        or _EN_AUTHOR_WITH_TITLE_RE.match(s)
        or _SOURCE_AUTHOR_WHY_TITLE_RE.match(s)
        or _SOURCE_TITLE_AUTHOR_DUP_WHY_RE.match(s)
        or _DUPLICATE_PAREN_RE.match(s)
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
    raw = raw.replace("Martyn Ллойд-Джонс", "Martyn Lloyd-Jones")
    if raw in _ORIGINAL_AUTHOR_ALIASES:
        return _ORIGINAL_AUTHOR_ALIASES[raw]
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
    """Backward-compatible wrapper around typed SourceCard rendering."""
    return render_source_card(build_source_card(
        author=author,
        title_original=en_title,
        original_author=en_author,
        fallback_ru_title=fallback_ru_title,
        bullet=bullet,
    ))

_SOURCE_BULLET_RE = re.compile(r'^(\s*[•\-]\s+)')


def _ensure_source_title_bold(line: str) -> str:
    """Ensure the title part of a source card is bold.

    Canonical format: • **Title**, Author (Original, Author).
    Only acts on source-card-like lines. Skips scripture references.
    """
    if not line:
        return line
    m = _SOURCE_BULLET_RE.match(line)
    if not m:
        return line
    bullet = m.group(1)
    rest = line[m.end():]
    if ',' not in rest:
        return line
    # Skip scripture references: lines containing guillemets «»
    if '\u00ab' in rest or '\u00bb' in rest:
        return line

    # If already has bold on first segment before comma — trust it
    first_seg = rest.split(',')[0].strip()
    if first_seg.startswith('**') and first_seg.endswith('**'):
        return line

    if '**' not in rest:
        # No bold at all — add to first part (title)
        title_part, _, author_part = rest.partition(',')
        title_part = title_part.strip()
        if len(title_part) >= 3:
            return f"{bullet}**{title_part}**,{author_part}"
        return line

    # Bold exists but NOT on first segment — misplaced
    clean = rest.replace('**', '')
    title_part, _, author_part = clean.partition(',')
    title_part = title_part.strip()
    if len(title_part) >= 3:
        return f"{bullet}**{title_part}**,{author_part}"
    return line


def normalize_source_card_line(line: str, *, prefer_original: bool = True) -> str:
    """Normalize one bibliography/source-card line.

    - ``RU title (English Author, English Title)`` -> canonical author + English title;
    - known English author at line start -> canonical Russian author;
    - duplicate bilingual authors are removed;
    - known wrong Russian titles are corrected.
    """
    if not line:
        return line
    # ВАЖНО: не нормализуем имена ДО разбора source-card. Иначе
    # parenthetical verifier `(John MacArthur, Strange Fire)` превращается в
    # `(Джон МакАртур, Strange Fire)`, и мы теряем оригинального автора.
    out = correct_known_ru_title(str(line or ""))
    # FIX AUDIT R4: правило AGENTS.md — «silently drop disallowed authors» —
    # относится к source-КАРТОЧКАМ. Раньше упоминание автора в любой прозе
    # (например, разбор лекции с критикой его взглядов) обнуляло всё поле
    # через _scrub_inline → normalize_source_map_text — тихая потеря контента.
    if is_disallowed_source_author(out) and _looks_like_source_card_line(out):
        return ""
    # Only source-card-like lines need aggressive whitespace/dedupe normalization.
    # Plain inline nodes such as "• " must keep their spacing.
    looks_like_source = bool(re.search(r"[A-Za-z].*,|,\s*[A-Za-z][A-Za-z ]{2,}|\([^)]*[A-Za-z]{3,}[^)]*\)", out))
    if looks_like_source:
        out = dedupe_bilingual_authors(out)

    # Уже отрендеренная canonical markdown source-card строка:
    # **Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur).
    # Не нормализуем person names внутри parenthetical verifier.
    # AUDIT R23: бывший "• " больше не рендерится (см. render_source_card) —
    # без этого без-bullet-варианта здесь guard молчал ломался: уже готовая
    # карточка повторно "нормализовалась" ниже и английское имя в скобках
    # (John MacArthur) переводилось на русский (Джон МакАртур), давая дубль.
    if (re.match(r"^\s*(?:[•\-]\s+)?\*\*", out)
            and re.search(r"\([^)]*[A-Za-z]{3,}[^)]*\)", out)):
        return out.rstrip() + ("" if re.search(r"[.!?]\s*$", out) else ".")

    weird_author = _SOURCE_AUTHOR_WHY_TITLE_RE.match(out.strip())
    if weird_author:
        bullet = weird_author.group("bullet") or ""
        author = weird_author.group("author").strip()
        title = weird_author.group("title").strip().rstrip(".")
        why = weird_author.group("why").strip().rstrip(".")
        card = _format_canonical_source_card(bullet, canonical_author_name(author), author, title)
        return f"{card} — {why}."

    weird_dup = _SOURCE_TITLE_AUTHOR_DUP_WHY_RE.match(out.strip())
    if weird_dup:
        bullet = weird_dup.group("bullet") or ""
        author = weird_dup.group("author").strip()
        title = weird_dup.group("title").strip().rstrip(".")
        why = weird_dup.group("why").strip().rstrip(".")
        card = _format_canonical_source_card(bullet, canonical_author_name(author), author, title)
        return f"{card} — {why}."

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

    title_author = _SOURCE_RU_TITLE_AUTHOR_WITH_ORIGINAL_RE.match(out.strip())
    if title_author and re.search(r"[A-Za-z]", title_author.group("en_title")):
        bullet = title_author.group("bullet") or ""
        en_author = original_author_name(title_author.group("en_author").strip())
        en_title = title_author.group("en_title").strip().rstrip(".")
        # Guard against the opposite parenthetical order: (English Author, English Title).
        # Example: "Джон МакАртур, Странный огонь (John MacArthur, Strange Fire)"
        # belongs to _SOURCE_RU_WITH_ORIGINAL_RE below, not this title-first shape.
        if canonical_author_name(en_title) == en_title:
            author = canonical_author_name(en_author) or canonical_author_name(title_author.group("author").strip())
            return _format_canonical_source_card(
                bullet, author, en_author, en_title,
                # Do not trust model-invented Russian titles in title-first source cards;
                # official_ru_title() will still supply known published Russian titles.
                fallback_ru_title="",
            )

    m = _SOURCE_RU_WITH_ORIGINAL_RE.match(out.strip())
    if m and re.search(r"[A-Za-z]", m.group("en_title")):
        bullet = m.group("bullet") or ""
        en_author = m.group("en_author").strip()
        en_title = m.group("en_title").strip().rstrip(".")
        author = canonical_author_name(en_author) or m.group("author").strip()
        return _format_canonical_source_card(
            bullet, author, en_author, en_title,
            # Do not trust model-invented Russian titles; official_ru_title()
            # still supplies known canonical Russian titles.
            fallback_ru_title="",
        )

    m2 = _EN_AUTHOR_WITH_TITLE_RE.match(out.strip())
    if m2:
        bullet = m2.group("bullet") or ""
        en_author = m2.group("en_author").strip()
        title = m2.group("title").strip().rstrip(".")
        author = canonical_author_name(en_author)
        # FIX AUDIT R4: карточку строим только для известного автора или
        # имени из ≥2 капитализированных слов. Ветка «любой Latin в title»
        # превращала обычные английские фразы в фейковые карточки:
        # "Behold, I stand at the door and knock." →
        # "**I stand at the door and knock**, Behold."
        if author != en_author or _MULTIWORD_NAME_RE.match(en_author):
            return _format_canonical_source_card(bullet, author, en_author, title)

    if looks_like_source and out.strip().startswith(("•", "-")) and not re.search(r"[.!?]\s*$", out):
        return normalize_person_names(out.rstrip()) + "."
    return normalize_person_names(out) if looks_like_source else out
