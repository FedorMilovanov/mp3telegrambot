#!/usr/bin/env python3
"""Section-level deterministic audit between Gemini JSON and Telegraph rendering.

This is the professional layer above node-level Telegraph postprocess:

    Gemini JSON -> parse -> SECTION AUDIT -> markdown/nodes -> node audit -> publish

It mutates only narrow, deterministic classes of defects observed in live runs
and reports warnings for classes that should be inspected but not guessed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from core.structured_blocks import normalize_structured_block
from core.text_utils import (
    find_mixed_greek_cyrillic_tokens,
    normalize_common_typos,
    normalize_source_map_text,
    scrub_third_person_phrases,
)


@dataclass(frozen=True)
class ContentAuditIssue:
    code: str
    location: str
    message: str
    before: str = ""
    after: str = ""


_THIRD_PERSON_RE = re.compile(
    r"\b(?:(?:Джон\s+)?МакАртур|автор|проповедник|спикер|лектор)\s+"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает)\b",
    re.IGNORECASE,
)

_SOURCE_MAP_HEADING_RE = re.compile(r"карта\s+источников", re.IGNORECASE)
_TRANSLATION_FORKS_HEADING_RE = re.compile(r"переводческ\w*\s+развил", re.IGNORECASE)
_BULLET_LINE_RE = re.compile(r"^\s*[•\-]\s+(.+?)\s*$")

# AUDIT R45 (живой скриншот 2026-07-11): промт дважды прямым текстом запрещает
# пары ❌/✅ в Reflection ("этот формат только для Study Analysis"), но модель
# всё равно иногда воспроизводит фирменный Study-карточный формат ("❌
# **Название**" / "✅ **Ответ ортодоксальной церкви.**") внутри Reflection —
# инструкция ненадёжна, нужен детерминированный бэкстоп. Снимаем ТОЛЬКО
# эмодзи-маркер (с последующим пробелом), текст остаётся полностью — это
# визуальный формат-баг, а не смысловая правка.
_FORBIDDEN_PAIR_MARKER_RE = re.compile(r"[❌✅]\s*")


_FIRST_PERSON_AUTHOR_RE = re.compile(
    r"\b(?P<prefix>Я|Для меня),\s+(?P<name>[А-ЯЁ][А-ЯЁа-яё]+(?:\s+[А-ЯЁ][А-ЯЁа-яё]+){0,2}),\s*"
)

# AUDIT R37 (живой дамп 2026-07-10: guard срезал «Иоанн» из Откр. 1:9
# «Я, Иоанн, брат ваш» → «Я брат ваш» — испортил цитату Писания). Библейские
# говорящие от первого лица (авторы книг, пророки, патриархи, цари, Сам Бог)
# — легитимны в цитатах и пересказе, их НЕ трогаем. Проверяем по первому слову
# имени, чтобы покрыть «Господь Бог», «Иоанн Богослов» и т.п.
_BIBLICAL_FIRST_PERSON: frozenset[str] = frozenset({
    "господь", "бог", "христос", "иисус", "яхве", "дух",
    # НЗ
    "иоанн", "павел", "пётр", "петр", "иаков", "иуда", "лука", "матфей", "марк",
    # ВЗ: авторы, пророки, патриархи, цари
    "моисей", "давид", "соломон", "даниил", "исаия", "иеремия", "иезекииль",
    "осия", "иоиль", "амос", "авдий", "иона", "михей", "наум", "аввакум",
    "софония", "аггей", "захария", "малахия", "неемия", "ездра", "иов",
    "авраам", "иосиф", "самуил", "илия", "елисей", "агур", "екклесиаст",
    "богослов",  # эпитет: «Иоанн Богослов»
})


_DOUBLE_SLASH_RE = re.compile(r"(?<!:)\s+/\s*/\s+")
_GLUE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"([а-яё])([A-Z])"), r"\1 \2"),
    (re.compile(r"([а-яё])«"), r"\1 «"),
    (re.compile(r"(?<!\d)([.!?…])([А-ЯЁA-Z])"), r"\1 \2"),
    (re.compile(r"(:\d{1,3})\.([А-ЯЁA-Z])"), r"\1. \2"),
    (re.compile(r"(^|\n)-(?=[А-ЯЁA-Z])"), r"\1- "),
)


def normalize_generated_markdown_separators(text: str) -> str:
    """Preserve layout semantics before Telegraph node rendering.

    Gemini sometimes emits pseudo-separators like ``/ /`` between logical
    paragraphs.  Treat them as paragraph breaks instead of deleting them.  The
    glue fixes are deliberately narrow and do not touch URLs (``://`` is guarded)
    or markdown links.
    """
    out = str(text or "")
    out = _DOUBLE_SLASH_RE.sub("\n\n", out)
    for pattern, repl in _GLUE_FIXES:
        out = pattern.sub(repl, out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out



# FIX: триггеры сужены до однозначно "канальных" маркеров утечки промпта.
# Убрано одиночное "мы придерживаемся" — это нормальная речь проповедника,
# а не утечка системного контекста, и оно ложно срабатывало на авторском тексте.
_CHANNEL_TRIGGER = (
    r"(?:канал\s+занимает\s+позици|позици[яю]\s+канала|наш\s+канал|"
    r"редакторск\w+\s+позици\w*\s+канал\w*|конфессиональн\w+\s+рамк\w+\s+канал\w*)"
)
# FIX: концом предложения считаем .!?… только перед пробелом+Заглавной / концом
# строки / концом текста, а НЕ любую точку. Раньше [^.?!…]* рвался на точке внутри
# библейских сокращений ("Рим.", "Ис.", "Ин."), из-за чего соседнее легитимное
# предложение калечилось ("Как в Рим. 8:28 ..." -> "Как в Рим.").
_SENT_END = r"(?:[.!?…]+(?=\s+[А-ЯЁA-Z«]|\s*$|\n)|\n|$)"
_CHANNEL_POSITION_RE = re.compile(
    rf"(?:(?<=^)|(?<=\n)|(?<=[.!?…])\s+)[^\n.!?…]*?{_CHANNEL_TRIGGER}.*?{_SENT_END}",
    re.IGNORECASE,
)
_MATERIAL_STYLE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bДанный\s+академический\s+труд\b", re.IGNORECASE), "Академический труд"),
    (re.compile(r"\bДанный\s+труд\b", re.IGNORECASE), "Этот труд"),
    (re.compile(r"\bДанная\s+книга\b", re.IGNORECASE), "Эта книга"),
    (re.compile(r"\bВ\s+материале\s+этот\s+термин\s+рассматривается\b", re.IGNORECASE), "Этот термин работает"),
    # FIX AUDIT R6: рерайт в «Автор говорит/показывает…» сам создавал
    # запрещённый third-person wrapper, который следом вычищал другой
    # скраббер. Переписываем сразу в безличную форму.
    (re.compile(r"\bВ\s+материале\s+говорится\b", re.IGNORECASE), "Говорится"),
    (re.compile(r"\bВ\s+материале\s+", re.IGNORECASE), ""),
    (re.compile(r"\bМатериал\s+критикует\b", re.IGNORECASE), "Критикуется"),
    (re.compile(r"\bМатериал\s+показывает\b", re.IGNORECASE), "Показывается"),
    (re.compile(r"\bМатериал\s+подчеркивает\b", re.IGNORECASE), "Подчеркивается"),
    (re.compile(r"\bМатериал\s+разбирает\b", re.IGNORECASE), "Разбирается"),
    (re.compile(r"\bМатериал\s+указывает\b", re.IGNORECASE), "Указывается"),
    (re.compile(r"\bМатериал\s+связывает\b", re.IGNORECASE), "Связывается"),
    (re.compile(r"[^.?!…]*(?:конфессиональн\w+\s+рамк\w+\s+канал\w*|редакторск\w+\s+позици\w+\s+канал\w*)[^.?!…]*[.!?…]?\s*", re.IGNORECASE), ""),
)


def _scrub_prompt_context_leaks(text: str) -> tuple[str, bool]:
    original = str(text or "")
    # FIX: заменяем вырезанный фрагмент на пробел (а не пустую строку),
    # чтобы соседние предложения не склеивались ("приговор.Покаяние").
    out = _CHANNEL_POSITION_RE.sub(" ", original)
    for pattern, repl in _MATERIAL_STYLE_FIXES:
        out = pattern.sub(" ", out) if repl == "" else pattern.sub(repl, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    # FIX: восстанавливаем пробел после знака конца предложения, если из-за
    # удаления склеились "знак+Заглавная" ("(Ис. 40:6).Дальше" -> ". Дальше").
    out = re.sub(r"([.!?…»])([А-ЯЁA-Z])", r"\1 \2", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out).strip()
    return out, out != original


def _scrub_forbidden_pair_markers(text: str) -> tuple[str, bool]:
    """Removes leaked Study-only ❌/✅ pair-card markers from Reflection text.

    Text-preserving: only the emoji marker (+ trailing space) is removed, the
    substantive sentence stays intact — this is a format violation, not a
    content problem.
    """
    original = str(text or "")
    if "❌" not in original and "✅" not in original:
        return original, False
    out = _FORBIDDEN_PAIR_MARKER_RE.sub("", original)
    # Осиротевший маркер списка перед снятой эмодзи ("• Ответ...") — нормально,
    # но двойной пробел на стыке ("Название  Что это") подчищаем.
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out, out != original


BLOCK_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "paragraph": ("text",),
    "para": ("text",),
    "bullet": ("text",),
    "list_item": ("text",),
    "point": ("text",),
    "scripture": ("ref",),
    "scripture_quote": ("ref",),
    "source": ("author", "title_original"),
    "source_card": ("author", "title_original"),
    "bibliography": ("author", "title_original"),
    "lexicon": ("lemma", "role_in_argument"),
    "term": ("lemma", "role_in_argument"),
}


def _validate_block_required_fields(block: dict, *, location: str) -> list[ContentAuditIssue]:
    btype = str(block.get("type") or "paragraph").strip().lower()
    if btype in {"quote", "blockquote", "block_quote"}:
        missing = [] if (str(block.get("quote") or "").strip() or str(block.get("text") or "").strip()) else ["quote/text"]
    elif btype in {"scripture", "scripture_quote"}:
        missing = [] if (str(block.get("text") or "").strip() or str(block.get("ref") or "").strip()) else ["text/ref"]
    elif btype == "argument_spine":
        missing = [] if (block.get("steps") or str(block.get("text") or "").strip()) else ["steps/text"]
    elif btype == "pull_quote":
        missing = [] if str(block.get("quote") or block.get("text") or "").strip() else ["quote/text"]
    elif btype == "application":
        missing = [field for field in ("challenge", "concrete_step") if not str(block.get(field) or "").strip()]
    elif btype in {"source", "source_card", "bibliography"}:
        missing = [] if (str(block.get("author") or "").strip() and str(block.get("title_original") or block.get("title") or "").strip()) else ["author + title_original/title"]
    elif btype in {"lexicon", "term", "lexical_analysis"}:
        missing = [field for field in ("lemma", "role_in_argument") if not str(block.get(field) or "").strip()]
    elif btype in {"theological_line", "historical_line"}:
        missing = [] if (str(block.get("why_relevant") or block.get("text") or "").strip()) else ["why_relevant/text"]
    else:
        required = BLOCK_REQUIRED_FIELDS.get(btype, ("text",))
        missing = [field for field in required if not str(block.get(field) or "").strip()]
    if not missing:
        return []
    return [ContentAuditIssue(
        code="block_schema_warning",
        location=location,
        message=f"block type {btype!r} missing required fields: {', '.join(missing)}",
        before=_short(block),
    )]


def _block_has_substantial_text(value: str, *, min_len: int = 90) -> bool:
    text = re.sub(r"\*+|[«»\"'`]+", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return len(text) >= min_len


def _audit_structured_block_semantics(
    block: dict,
    *,
    label: str,
    section_title: str,
    location: str,
) -> list[ContentAuditIssue]:
    """Warn when a structurally valid block is still editorially too thin."""
    btype = str(block.get("type") or "paragraph").strip().lower()
    label_low = str(label or "").lower()
    title_low = str(section_title or "").lower()
    issues: list[ContentAuditIssue] = []

    if btype == "scripture" and ("study" in label_low or "ключевые текст" in title_low):
        role = str(block.get("role_in_argument") or block.get("why_relevant") or "")
        text = str(block.get("text") or "")
        if not (_block_has_substantial_text(role, min_len=90) or ("\n\n" in text and _block_has_substantial_text(text.split("\n\n", 1)[1], min_len=90))):
            issues.append(ContentAuditIssue(
                code="scripture_role_missing_warning",
                location=location,
                message="scripture block needs role_in_argument/explanation, not a bare verse card",
                before=_short(block),
            ))

    if btype == "source" and "study" in label_low:
        why = str(block.get("why_relevant") or "")
        if not _block_has_substantial_text(why, min_len=60):
            issues.append(ContentAuditIssue(
                code="source_relevance_missing_warning",
                location=location,
                message="source block needs why_relevant for this exact material",
                before=_short(block),
            ))

    if btype == "lexicon" and "study" in label_low:
        role = str(block.get("role_in_argument") or "")
        if not _block_has_substantial_text(role, min_len=80):
            issues.append(ContentAuditIssue(
                code="lexicon_role_thin_warning",
                location=location,
                message="lexicon block needs contextual role in the argument, not a dictionary card",
                before=_short(block),
            ))

    if btype == "application" and "reflection" in label_low:
        if not str(block.get("anchor_timestamp") or block.get("timestamp") or "").strip():
            issues.append(ContentAuditIssue(
                code="application_anchor_missing_warning",
                location=location,
                message="application block should be anchored to a concrete sermon timestamp",
                before=_short(block),
            ))
    return issues


def _scrub_mismatched_first_person_author(text: str, expected_author: str = "") -> tuple[str, list[ContentAuditIssue]]:
    """Remove hallucinated first-person name appositions when they mismatch expected author."""
    if not text or not expected_author:
        return text, []
    expected_norm = re.sub(r"\s+", " ", expected_author).strip().lower()
    issues: list[ContentAuditIssue] = []

    def repl(m: re.Match) -> str:
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        # AUDIT R37: не калечим цитаты/пересказ Писания вида «Я, Иоанн,...» /
        # «Я, Господь Бог,...» — библейские говорящие от первого лица легитимны.
        # Требуем, чтобы ВСЕ слова имени были библейскими: одиночное имя (Иоанн)
        # и «Господь Бог» сохраняются, а современное имя+фамилия («Пётр Иванов»,
        # «Джон МакАртур») по-прежнему срезается.
        if all(w.lower() in _BIBLICAL_FIRST_PERSON for w in name.split()):
            return m.group(0)
        if name.lower() in expected_norm or expected_norm in name.lower():
            return m.group(0)
        issues.append(ContentAuditIssue(
            code="first_person_author_fixed",
            location="",
            message=f"removed mismatched first-person author name: {name}",
            before=m.group(0),
            after=m.group("prefix") + " ",
        ))
        return m.group("prefix") + " "

    return _FIRST_PERSON_AUTHOR_RE.sub(repl, text), issues


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " / ").strip()
    return text[:limit]


def _audit_text(value: str, *, location: str, source_map: bool = False, expected_author: str = "", label: str = "") -> tuple[str, list[ContentAuditIssue]]:
    """Normalize one title/content string and return issues."""
    original = str(value or "")
    issues: list[ContentAuditIssue] = []

    text = normalize_common_typos(original, source_map=False)
    if text != original:
        _looks_source_fix = bool(re.search(r"[A-Za-z].*,|\([^)]*[A-Za-z]{3,}[^)]*\)", original))
        issues.append(ContentAuditIssue(
            code="source_card_fixed" if _looks_source_fix else "typo_fixed",
            location=location,
            message="source-card normalization applied" if _looks_source_fix else "common typo normalization applied",
            before=_short(original),
            after=_short(text),
        ))

    sep_before = text
    text = normalize_generated_markdown_separators(text)
    if text != sep_before:
        issues.append(ContentAuditIssue(
            code="separator_fixed",
            location=location,
            message=(
                f"removed_double_slash={len(_DOUBLE_SLASH_RE.findall(sep_before))}; "
                f"paragraphs_before={sep_before.count(chr(10)+chr(10))}; paragraphs_after={text.count(chr(10)+chr(10))}"
            ),
            before=_short(sep_before),
            after=_short(text),
        ))

    source_before = text
    if source_map:
        text = "\n".join(normalize_source_map_text(line) for line in text.splitlines())
    if text != source_before:
        issues.append(ContentAuditIssue(
            code="source_card_fixed",
            location=location,
            message="source-card normalization applied",
            before=_short(source_before),
            after=_short(text),
        ))

    leak_before = text
    text, leak_changed = _scrub_prompt_context_leaks(text)
    if leak_changed:
        issues.append(ContentAuditIssue(
            code="prompt_context_leak_fixed",
            location=location,
            message="removed channel/prompt-context or mechanical material wording",
            before=_short(leak_before),
            after=_short(text),
        ))

    third_before = text
    # ГЛУБОКИЙ ФИКС: Всегда чистим текст от "МакАртур подчеркивает", даже на Study/Reflection.
    text = scrub_third_person_phrases(text)
    if text != third_before:
        issues.append(ContentAuditIssue(
            code="third_person_fixed",
            location=location,
            message="third-person analytic wrapper removed",
            before=_short(third_before),
            after=_short(text),
        ))
    elif _THIRD_PERSON_RE.search(text):
        issues.append(ContentAuditIssue(
            code="third_person_warning",
            location=location,
            message="third-person wrapper still present after conservative scrub",
            before=_short(text),
        ))

    text, author_issues = _scrub_mismatched_first_person_author(text, expected_author)
    for issue in author_issues:
        issues.append(ContentAuditIssue(
            code=issue.code,
            location=location,
            message=issue.message,
            before=issue.before,
            after=issue.after,
        ))

    # AUDIT R45: Reflection-промт дважды прямым текстом запрещает Study-only
    # пары ❌/✅ — модель всё равно иногда их воспроизводит (живой пример).
    # Детерминированный бэкстоп ТОЛЬКО для Reflection: Study легитимно
    # использует этот формат (SECTION TYPE 6), там его не трогаем.
    if label == "ReflectionApplication":
        pair_before = text
        text, pair_changed = _scrub_forbidden_pair_markers(text)
        if pair_changed:
            issues.append(ContentAuditIssue(
                code="reflection_forbidden_marker_scrubbed",
                location=location,
                message="Study-only ❌/✅ pair-card marker removed from Reflection text",
                before=_short(pair_before),
                after=_short(text),
            ))

    mixed = find_mixed_greek_cyrillic_tokens(text)
    if mixed:
        issues.append(ContentAuditIssue(
            code="mixed_greek_cyrillic_warning",
            location=location,
            message="mixed Greek/Cyrillic token(s): " + ", ".join(mixed[:5]),
            before=_short(text),
        ))

    return text, issues


def _audit_translation_semantics(content: str, *, location: str) -> list[ContentAuditIssue]:
    """Small semantic sanity checks for common translation-fork drift."""
    low = (content or "").lower()
    issues: list[ContentAuditIssue] = []
    if "evil" in low and "лож" in low and "зл" not in low:
        issues.append(ContentAuditIssue(
            code="translation_semantic_warning",
            location=location,
            message="English 'evil' appears to be rendered as 'ложь' without 'зло'",
            before=_short(content),
        ))
    return issues


def _audit_translation_forks(content: str, *, location: str) -> list[ContentAuditIssue]:
    """Warn about bare translation-fork bullet lists without analysis."""
    if not content or not _TRANSLATION_FORKS_HEADING_RE.search(location):
        return []
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    bullet_idxs = [i for i, ln in enumerate(lines) if _BULLET_LINE_RE.match(ln)]
    if len(bullet_idxs) < 2:
        return []
    # If two or more bullet lines are short and adjacent/near-adjacent, likely bare headings.
    short_bullets = [i for i in bullet_idxs if len(lines[i]) < 130]
    if len(short_bullets) >= 2:
        # Require at least one substantial non-bullet analysis paragraph.
        has_analysis = any((not _BULLET_LINE_RE.match(ln)) and len(ln) >= 160 for ln in lines)
        if not has_analysis:
            return [ContentAuditIssue(
                code="bare_translation_forks_warning",
                location=location,
                message="translation-forks section may contain bare bullet headings without analysis",
                before=_short(" / ".join(lines[:5])),
            )]
    return []


def audit_expanded_sections(
    sections: list[dict],
    outline: list[dict] | None = None,
    *,
    label: str = "",
    expected_author: str = "",
) -> tuple[list[dict], list[dict], list[ContentAuditIssue]]:
    """Audit and normalize parsed Gemini ``sections``/``outline``.

    The function is intentionally schema-light: it preserves unknown keys and
    only normalizes ``title`` and ``content`` string fields. It returns copied
    lists so callers do not mutate parser-owned structures by accident.
    """
    issues: list[ContentAuditIssue] = []
    new_sections: list[dict] = []

    for idx, raw in enumerate(sections or []):
        if not isinstance(raw, dict):
            continue
        sec = dict(raw)
        base_loc = f"{label or 'expanded'}.sections[{idx}]"

        title = str(sec.get("title") or "")
        new_title, got = _audit_text(title, location=f"{base_loc}.title", expected_author=expected_author, label=label)
        issues.extend(got)
        sec["title"] = new_title

        content = str(sec.get("content") or "")
        source_map = bool(_SOURCE_MAP_HEADING_RE.search(new_title))
        new_content, got = _audit_text(content, location=f"{base_loc}.content", source_map=source_map, expected_author=expected_author, label=label)
        issues.extend(got)
        issues.extend(_audit_translation_forks(new_content, location=f"{base_loc}:{new_title}"))
        issues.extend(_audit_translation_semantics(new_content, location=f"{base_loc}:{new_title}"))
        sec["content"] = new_content

        blocks = sec.get("blocks")
        if isinstance(blocks, list):
            new_blocks: list[dict] = []
            for bidx, raw_block in enumerate(blocks):
                if not isinstance(raw_block, dict):
                    continue
                block = normalize_structured_block(raw_block) or dict(raw_block)
                block_loc = f"{base_loc}.blocks[{bidx}]"
                issues.extend(_validate_block_required_fields(block, location=block_loc))
                issues.extend(_audit_structured_block_semantics(
                    block,
                    label=label,
                    section_title=new_title,
                    location=block_loc,
                ))
                for field in ("text", "quote", "why_relevant", "role_in_argument", "challenge", "concrete_step"):
                    if field in block and isinstance(block.get(field), str):
                        block_text, got_block = _audit_text(
                            block.get(field, ""),
                            location=f"{base_loc}.blocks[{bidx}].{field}",
                            expected_author=expected_author,
                            label=label,
                        )
                        issues.extend(got_block)
                        block[field] = block_text
                for field in ("author", "title_original", "lemma", "ref", "timestamp", "type"):
                    if field in block and isinstance(block.get(field), str):
                        block[field] = str(block.get(field) or "").strip()
                new_blocks.append(block)
            sec["blocks"] = new_blocks

        new_sections.append(sec)

    # AUDIT R15 (скриншот 2026-07-09): density-retry Synopsis (schema
    # отключена для verbatim-режима) иногда возвращает ДВЕ секции подряд
    # с одинаковым title и time — TOC показывал «Решительный выбор в
    # библиотеке — 20:00» дважды. Ни здесь, ни в services/telegraph.py
    # дедупликации не было вообще. Убираем ПОДРЯД идущие дубли (одинаковый
    # нормализованный title + одинаковый time), оставляя более содержательную.
    def _dedup_key(sec: dict) -> tuple[str, str]:
        t = re.sub(r"[^\w\s]", "", str(sec.get("title") or "").lower())
        t = re.sub(r"\s+", " ", t).strip()
        return t, str(sec.get("time") or "").strip()

    def _section_richness(sec: dict) -> int:
        blocks_len = sum(len(str(b.get("text") or "")) for b in (sec.get("blocks") or []) if isinstance(b, dict))
        return len(str(sec.get("content") or "")) + blocks_len

    deduped_sections: list[dict] = []
    for idx, sec in enumerate(new_sections):
        key = _dedup_key(sec)
        if deduped_sections and key[0] and _dedup_key(deduped_sections[-1]) == key:
            if _section_richness(sec) > _section_richness(deduped_sections[-1]):
                deduped_sections[-1] = sec
            issues.append(ContentAuditIssue(
                code="duplicate_section_removed",
                location=f"{label or 'expanded'}.sections[{idx}]",
                message=f"consecutive duplicate section removed: title={key[0]!r} time={key[1]!r}",
            ))
            continue
        deduped_sections.append(sec)
    new_sections = deduped_sections

    new_outline: list[dict] = []
    for idx, raw in enumerate(outline or []):
        if not isinstance(raw, dict):
            continue
        oi = dict(raw)
        title = str(oi.get("title") or "")
        new_title, got = _audit_text(title, location=f"{label or 'expanded'}.outline[{idx}].title", expected_author=expected_author, label=label)
        issues.extend(got)
        oi["title"] = new_title
        new_outline.append(oi)

    return new_sections, new_outline, issues


_WARNING_CODES = {
    "mixed_greek_cyrillic_warning",
    "bare_translation_forks_warning",
    "translation_semantic_warning",
    "third_person_warning",
    "block_schema_warning",
    "scripture_role_missing_warning",
    "source_relevance_missing_warning",
    "lexicon_role_thin_warning",
    "application_anchor_missing_warning",
}


def has_content_audit_warnings(issues: list[ContentAuditIssue]) -> bool:
    """True when issues include unresolved warning-level problems, not just fixes."""
    return any(i.code in _WARNING_CODES for i in issues or [])


def format_content_audit_issues(issues: list[ContentAuditIssue], limit: int = 6) -> str:
    if not issues:
        return ""
    rendered: list[str] = []
    specific_locations = {
        i.location for i in issues or []
        if i.code in {"typo_fixed", "source_card_fixed", "separator_fixed", "whitespace_fixed"}
    }
    display_issues = [
        i for i in (issues or [])
        if not (i.code == "normalized_text" and i.location in specific_locations)
    ]
    for issue in display_issues[:limit]:
        detail = issue.message
        if issue.before:
            detail += f" | before={issue.before}"
        if issue.after:
            detail += f" | after={issue.after}"
        rendered.append(f"{issue.code}@{issue.location}: {detail}")
    if len(display_issues) > limit:
        rendered.append(f"... и ещё {len(display_issues) - limit}")
    return " || ".join(rendered)


_CRITICAL_CONTENT_CODES = {
    "mixed_greek_cyrillic_warning",
    "bare_translation_forks_warning",
}


def _strict_content_codes() -> set[str]:
    """Return issue codes that should abort publication in strict mode.

    CONTENT_AUDIT_STRICT_CODES defaults to the historically critical set. Set it
    to ``all`` for surgical manual review sessions where unresolved warnings
    (third-person wrappers, thin scripture/source/application blocks) should stop
    publication instead of merely logging a warning.
    """
    raw = (os.getenv("CONTENT_AUDIT_STRICT_CODES", "") or "").strip()
    if not raw:
        return set(_CRITICAL_CONTENT_CODES)
    if raw.lower() in {"all", "warnings", "warning"}:
        return set(_WARNING_CODES) | set(_CRITICAL_CONTENT_CODES)
    codes = {x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()}
    return codes | set(_CRITICAL_CONTENT_CODES)


def get_content_audit_mode() -> str:
    """Return CONTENT_AUDIT_MODE: off | warn | strict. Default: warn."""
    mode = (os.getenv("CONTENT_AUDIT_MODE", "warn") or "warn").strip().lower()
    return mode if mode in {"off", "warn", "strict"} else "warn"


def content_audit_block_publication_enabled() -> bool:
    """Explicit opt-in for blocking publication on audit findings.

    The operational policy is repair/log/history-first: bad pages must be found
    and improved, not silently withheld from the researcher. Blocking is reserved
    for rare CI/manual gate runs and requires an explicit flag in addition to
    CONTENT_AUDIT_MODE=strict.
    """
    raw = (os.getenv("CONTENT_AUDIT_BLOCK_PUBLICATION", "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def should_abort_for_content_audit(issues: list[ContentAuditIssue]) -> bool:
    """True only when strict mode and explicit block-publication flag are both enabled."""
    if get_content_audit_mode() != "strict" or not content_audit_block_publication_enabled():
        return False
    strict_codes = _strict_content_codes()
    return any(i.code in strict_codes for i in issues or [])
