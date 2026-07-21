#!/usr/bin/env python3
"""Conservative runtime prompt compaction.

This module removes only repeated formatting boilerplate.  Synopsis prompts also
pass through a final fidelity guard which restores the project's original
contract: a full verbatim transcript, never a compressed synopsis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_DEDUPE_EXACT_LINES = {
    "Верни только чистый JSON, без ```json и без пояснений.",
    "Верни только чистый JSON — без ```json, без пояснений до или после.",
    "Верни только чистый JSON, без ```json и без текста до/после.",
    "JSON валиден: только ключи outline и sections?",
    "outline совпадает с sections (title + time, без content)?",
    "Нет section без title или с пустым content?",
}

_SYNOPSIS_MARKERS = (
    "СТРУКТУРИРОВАННУЮ ДОСЛОВНУЮ СТЕНОГРАММУ",
    "РЕЖИМ 100% ПОДРОБНОЙ ТРАНСКРИПЦИИ",
    "Ты создаёшь конспект сессии вопросов и ответов",
    "СТЕПЕНЬ ДОСЛОВНОСТИ КОНСПЕКТА",
)

_VERBATIM_CONTRACT_MARKER = "ФИНАЛЬНЫЙ КОНТРАКТ ПОЛНОЙ ДОСЛОВНОЙ СТЕНОГРАММЫ"

_VERBATIM_FINAL_CONTRACT = f"""\
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
{_VERBATIM_CONTRACT_MARKER}
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

ЭТО ПОСЛЕДНЯЯ И ПРИОРИТЕТНАЯ ИНСТРУКЦИЯ ДЛЯ SYNOPSIS.
Она отменяет любые более ранние слова о сжатии, уплотнении, пересказе,
объединении предложений, удалении повторов или редакторской очистке речи.

Требуется ПОЛНАЯ ДОСЛОВНАЯ СТЕНОГРАММА БЕЗ СОКРАЩЕНИЙ:
- сохраняй каждое произнесённое предложение и порядок слов настолько точно,
  насколько позволяет перевод на русский;
- сохраняй повторы, слова-паразиты, оговорки, самокоррекции, незавершённые заходы,
  риторические вопросы, длинные рассуждения, отступления, примеры и бытовые детали;
- не перефразируй, не объединяй предложения, не сокращай длинные фрагменты и не
  заменяй живую речь тезисами;
- при переводе сохраняй порядок предложений, повторяемость, синтаксический ритм,
  тон и степень категоричности автора;
- разрешены только пунктуация, разбиение на читаемые абзацы, навигационные
  заголовки sections, таймкоды и Markdown-выделения без изменения слов автора;
- удалить можно только НЕ-РЕЧЕВЫЕ технические артефакты: шум соединения,
  служебные метки субтитров, ошибочно продублированный overlap auto-captions и
  фрагмент, который объективно невозможно разобрать. Нельзя называть речью-мусором
  то, что автор реально произнёс.

Если полный текст не помещается в одну Telegraph-страницу, дели публикацию на
части. Никогда не сокращай стенограмму ради лимита страницы или красивого объёма.
"""

_SUBSTRING_REPLACEMENTS = (
    (
        "сжатая стенограмма",
        "полная дословная стенограмма без сокращений",
    ),
    (
        "сжатая авторская речь",
        "полная дословная авторская речь без сокращений",
    ),
    (
        "сжатый реальный ответ автора",
        "полный дословный реальный ответ автора без сокращений",
    ),
    (
        "сжатая речь автора",
        "полная дословная речь автора без сокращений",
    ),
    (
        "сжато проводишь читателя по самой речи автора",
        "дословно проводишь читателя по всей речи автора без сокращений",
    ),
    (
        "длинные рассуждения — их можно уплотнить, сохраняя лексику",
        "длинные рассуждения передавай полностью, сохраняя лексику, порядок и повторы",
    ),
    (
        "объясняет долго — сожми, но сохрани порядок",
        "объясняет долго — передай ответ полностью и сохрани порядок каждой фразы",
    ),
    (
        "очищенная от мусора устной речи",
        "без смысловых и речевых изъятий; допускается удаление только не-речевых технических артефактов",
    ),
    (
        "не механическая копия каждого слова",
        "максимально точная передача каждого произнесённого слова",
    ),
    (
        "уплотнение без превращения речи в собственное богословское эссе",
        "полное сохранение речи без превращения её в собственное богословское эссе",
    ),
)

_LINE_REPLACEMENTS = {
    "Можно убирать:": "НЕЛЬЗЯ УБИРАТЬ ПРОИЗНЕСЁННУЮ РЕЧЬ:",
    '- словесный мусор ("ну", "вот", "как бы"),': '- слова-паразиты ("ну", "вот", "как бы") сохраняй как часть дословной речи;',
    "- оговорки и самокоррекцию,": "- оговорки и самокоррекции сохраняй полностью;",
    "- случайные повторы подряд (не усиливающие),": "- повторы подряд сохраняй полностью, независимо от их редакторской ценности;",
    "- технические паузы и отвлечения,": "- речевые отвлечения сохраняй; пропускай только не-речевые технические помехи;",
    "- разговорные пустоты без смысловой нагрузки.": "- все реально произнесённые разговорные заполнители сохраняй.",
    "- словесный мусор,": "- слова-паразиты и устные заполнители сохраняй;",
    "- разговорные повторы без смысловой нагрузки,": "- разговорные повторы сохраняй полностью;",
    "- пустые заходы,": "- незавершённые и повторные заходы сохраняй;",
    "- оговорки.": "- оговорки и самокоррекции сохраняй.",
    "- Сократить длинноты (повторы, отступления, технические паузы)": "- Сохранять длинные рассуждения, повторы и речевые отступления; удалять только не-речевые технические помехи",
    "- Уплотнить формулировку, сохраняя смысл и лексику": "- Сохранять формулировку полностью, без перефразирования и уплотнения",
    "- Объединить 2 предложения в одно если они говорят одно и то же": "- Не объединять предложения: сохранять каждое произнесённое предложение и повтор",
}


@dataclass(frozen=True)
class PromptCompactionResult:
    text: str
    original_chars: int
    compacted_chars: int
    removed_lines: int

    @property
    def saved_chars(self) -> int:
        return max(0, self.original_chars - self.compacted_chars)


def _looks_like_synopsis_prompt(text: str) -> bool:
    return any(marker in text for marker in _SYNOPSIS_MARKERS)


def enforce_full_verbatim_synopsis_contract(prompt: str) -> str:
    """Remove compression conflicts and append the authoritative verbatim contract."""
    text = str(prompt or "")
    if not _looks_like_synopsis_prompt(text):
        return text

    for old, new in _SUBSTRING_REPLACEMENTS:
        text = text.replace(old, new)

    rewritten: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        replacement = _LINE_REPLACEMENTS.get(stripped)
        if replacement is not None:
            indent = line[: len(line) - len(line.lstrip())]
            line = indent + replacement
        elif "уплотнить формулировку" in stripped.lower():
            indent = line[: len(line) - len(line.lstrip())]
            line = indent + "- Сохранять формулировку полностью, без перефразирования и уплотнения"
        rewritten.append(line)

    text = "\n".join(rewritten).rstrip()
    if _VERBATIM_CONTRACT_MARKER not in text:
        text += "\n\n" + _VERBATIM_FINAL_CONTRACT.strip()
    return text


def compact_prompt_for_generation(prompt: str) -> PromptCompactionResult:
    """Conservatively compact a prompt before sending it to Gemini.

    Rules:
    - enforce full verbatim fidelity for Synopsis prompts;
    - collapse excessive blank lines;
    - dedupe exact repeated boilerplate lines from a small allowlist;
    - strip trailing spaces.

    No task-specific examples, source packs or user/audio metadata are removed.
    """
    raw_original = str(prompt or "")
    original = enforce_full_verbatim_synopsis_contract(raw_original)
    seen: set[str] = set()
    removed = 0
    out: list[str] = []
    blank_run = 0
    for raw_line in original.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                out.append("")
            else:
                removed += 1
            continue
        blank_run = 0
        normalized = line.strip()
        if normalized in _DEDUPE_EXACT_LINES:
            if normalized in seen:
                removed += 1
                continue
            seen.add(normalized)
        out.append(line)
    text = "\n".join(out).strip()
    text2 = re.sub(r"\n{4,}", "\n\n\n", text)
    if text2 != text:
        removed += text.count("\n") - text2.count("\n")
        text = text2
    return PromptCompactionResult(
        text=text,
        original_chars=len(raw_original),
        compacted_chars=len(text),
        removed_lines=max(0, removed),
    )
