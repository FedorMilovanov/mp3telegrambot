#!/usr/bin/env python3
"""
Markdown → Telegraph nodes конвертер.
Извлечено из bot.py строки 2913–4213.
"""
from core.structured_blocks import normalize_structured_block
from core.text_utils import (
    _scrub_inline, _strip_meta_lines, _has_dirty_meta,
    normalize_author_name, normalize_common_typos, normalize_source_map_text,
    scrub_third_person_phrases, title_case_fragment,
)
from core.source_titles import build_source_card, is_disallowed_source_author, render_source_card
from core.url_utils import get_youtube_timestamp_url
from core.page_audit import audit_telegraph_page, format_audit_issues, should_abort_for_page_audit
# time_to_seconds и _fix_rtl_in_text перенесены в core_utils для разрыва циклических импортов
from core.core_utils import time_to_seconds, _fix_rtl_in_text, _md_parse_inline, _polish_timestamps_in_text, normalize_misbolded_bullet_lead, unescape_markdown_markers  # FIX: circular imports
from core.globals import TELEGRAPH_TOKEN        # FIX markdown

import asyncio    # FIX markdown
import json       # FIX markdown
import logging    # FIX markdown
import re
import requests   # FIX markdown
from typing import Any, Optional, List, Tuple

logger = logging.getLogger(__name__)  # FIX markdown

# ─── A02: Telegraph whitelist тегов ─────────────────────────────────────────
TELEGRAPH_HEADING_ALLOWED = {"h3", "h4"}
TELEGRAPH_ALL_ALLOWED = {
    "a", "aside", "b", "blockquote", "br", "code", "em", "figcaption",
    "figure", "h3", "h4", "hr", "i", "iframe", "img", "li", "ol", "p",
    "pre", "s", "strong", "u", "ul", "video"
}

# ─── A08: Pre-compiled паттерны для visible_length ───────────────────────────
_VL_TAG_RE      = re.compile(r'<[^>]+>')
_VL_NAMED_RE    = re.compile(r'&[a-zA-Z]+;')
_VL_DEC_RE      = re.compile(r'&#\d+;')
_VL_HEX_RE      = re.compile(r'&#x[0-9a-fA-F]+;')

# ─── Missing regex patterns (BUG FIX: were lost during refactoring) ──────────
# PATCH v2: убран re.DOTALL — заголовки должны быть однострочными
_HEADING_BOLD_STRIP_RE = re.compile(r'^\*\*(?!\*)(.*?)(?<!\*)\*\*$')

# FIXED #117: паттерны вынесены на уровень модуля — ранее компилировались
# при каждом вызове _ensure_trailing_period (сотни раз за прогон).
# FIX ReDoS: replaced \s*...\s*$ with non-overlapping pattern.
# Old pattern caused 9+ second hang on whitespace-only strings.
_ENSURE_TS_INLINE_RE = re.compile(
    r'[⏱📌]\s*\*{0,2}\d{1,2}:\d{2}(?::\d{2})?\*{0,2}\s*$'
)

# AUDIT R12: токен inline-таймкода для правил «точка после таймкода».
_TS_TOKEN_RE = r'⏱️?\s*\*{0,2}\d{1,2}:\d{2}(?::\d{2})?\*{0,2}'


def _fix_ts_period_order(text: str) -> str:
    """Точка ровно одна и только ПОСЛЕ ⏱-таймкода (правило AGENTS.md).

    AUDIT R12 (дампы 2026-07-06): применять надо на MARKDOWN-строке ДО
    линкификации — после неё таймкод уже разрезан на <a>-узел, и строковые
    правила его не видят. Q&A-страницы публиковались с «…бумаге. ⏱ 0:15.»
    (точка с обеих сторон), которое линкификация замуровывала навсегда.

    AUDIT R14 (дампы 2026-07-09): между точкой и таймкодом может стоять
    закрывающий markdown-маркер — application-блок Reflection рендерит
    «**…истории.** ⏱ 20:17» (точка ВНУТРИ жирного, перед `**`). Разрешаем
    опциональные */** между точкой и ⏱ и сохраняем их (`\1`), перенося
    только точку за таймкод: «**…истории** ⏱ 20:17.».
    """
    text = text.replace('⏱️', '⏱')
    _B = r'(\*{0,3})'  # опциональный закрывающий bold/italic-маркер, сохраняем
    # «слово.** ⏱ 0:15.» → «слово** ⏱ 0:15.» (двойная точка, где угодно)
    text = re.sub(r'\.' + _B + r'(\s*' + _TS_TOKEN_RE + r')\s*\.', r'\1\2.', text)
    # «слово.** ⏱ 0:15» в конце строки/абзаца → точка после таймкода
    text = re.sub(r'\.' + _B + r'(\s*' + _TS_TOKEN_RE + r')(?=\s*$)', r'\1\2.', text, flags=re.MULTILINE)
    # «слово.** ⏱ 0:15 Дальше…» перед новым предложением
    text = re.sub(r'\.' + _B + r'(\s*' + _TS_TOKEN_RE + r')(?=\s+[А-ЯЁA-Z«])', r'\1\2.', text)
    return text


def _strip_card_bullets(content: str, *, long_bold: bool = True) -> str:
    """R8+R11+R12: карточки с жирной шапкой теряют маркер «•».

    Теряют: «• **Термин** — описание», «• **Длинная жирная шапка.** Текст».
    Сохраняют: scripture («• **Мф 7:21:** …» — коротко, кончается «:»),
    короткие пункты, карточки источников («• **Название**, Автор» — после
    жирного идёт запятая).
    """
    content = re.sub(
        r"(?m)^•\s+(?=\*\*[^*\n]+\*\*(?:\s*\(\*\*[^*\n]+\*\*\))?\s+—)",
        "", content,
    )
    if long_bold:
        content = re.sub(
            r"(?m)^•\s+(?=\*\*[^*\n]{18,}[^:*\s]\*\*(?!,))",
            "", content,
        )
    return content


# ─── Scripture-reference паттерны (модульный уровень) ────────────────────────
# A07 FIX / Bug-fix #N: ранее эти паттерны были только локально внутри функций;
# при попытке вынести их на module-level определения были утеряны, что вызывало
# NameError: name '_SCRIPTURE_REF_RE' is not defined в _section_to_nodes_v2.
# Восстановлено по эталонному коду pre-refactor (старая монолитная bot.py).
_SCRIPTURE_BOOK_RE = (
    r'(?:Быт|Исх|Лев|Чис|Втор|Нав|Суд|Руфь|1\s*Цар|2\s*Цар|3\s*Цар|4\s*Цар|'
    r'1\s*Пар|2\s*Пар|Ездр|Неем|Есф|Иов|Пс|Притч|Еккл|Песн|Ис|Иер|Плач|'
    r'Иез|Дан|Ос|Иоил|Ам|Авд|Ион|Мих|Наум|Авв|Соф|Агг|Зах|Мал|'
    r'Мф|Мк|Лк|Ин|Деян|Рим|1\s*Кор|2\s*Кор|Гал|Еф|Фил|Кол|'
    r'1\s*Фес|2\s*Фес|1\s*Тим|2\s*Тим|Тит|Флм|Евр|Иак|'
    r'1\s*Пет|2\s*Пет|1\s*Ин|2\s*Ин|3\s*Ин|Иуд|Откр)'
)

# FIX AUDIT R4: ПОЛНЫЕ русские названия книг — guard линкификации mid-text
# таймкодов знал только аббревиатуры, и «в Иоанна 3:16» / «Римлянам 8:28»
# превращались в ссылки на минуту видео на каждой странице конспекта.
_SCRIPTURE_FULL_BOOK_RE = (
    r'(?:Бытие|Исход|Левит|Числа|Второзаконие|Иисус\s*Навин|Судьи|Руфь|'
    r'1\s*(?:Царств|Пар)|2\s*(?:Царств|Пар)|3\s*Царств|4\s*Царств|'
    r'Ездра|Неемия|Есфирь|Иов|Псалом|Псалмы|Псалма|Псалме|Притчи|Екклесиаст|Песнь|'
    r'Исаия|Иеремия|Плач|Иезекииль|Иезекиль|Езекииль|Езекиль|Даниил|Осия|Иоиль|Амос|Авдий|Иона|'
    r'Михей|Наум|Аввакум|Софония|Аггей|Захария|Малахия|'
    r'Матфея|Марка|Луки|Иоанна|Деяния|Римлянам|'
    r'1\s*Коринфянам|2\s*Коринфянам|Галатам|Ефесянам|Филиппийцам|Колоссянам|'
    r'1\s*Фессалоникийцам|2\s*Фессалоникийцам|1\s*Тимофею|2\s*Тимофею|'
    r'Титу|Филимону|Евреям|Иакова|1\s*Петра|2\s*Петра|'
    r'1\s*Иоанна|2\s*Иоанна|3\s*Иоанна|Иуды|Откровение)'
)

# Ссылки в скобках: (Рим. 8:28), (Рим. 8:28, 29), (Рим. 8:28; 1 Кор. 15:14), (1 Кор. 15:1–4)
_SCRIPTURE_REF_RE = re.compile(
    rf'(?<!\*)\('
    rf'(?:'
    rf'{_SCRIPTURE_BOOK_RE}\.?\s*\d+:\d+(?:[-–]\d+)?'
    rf'(?:\s*,\s*\d+(?:[-–]\d+)?)?'
    rf'(?:\s*;\s*{_SCRIPTURE_BOOK_RE}\.?\s*\d+:\d+(?:[-–]\d+)?(?:\s*,\s*\d+(?:[-–]\d+)?)?)*'
    rf')'
    rf'\)(?!\*)'
)

# Цепочки через стрелку →: «Ис. 40:6 → Рим. 8:28 → Лк. 24:44» (без скобок)
_SCRIPTURE_SINGLE_RE = (
    rf'{_SCRIPTURE_BOOK_RE}\.?\s*\d+:\d+(?:[-–]\d+)?(?:\s*,\s*\d+(?:[-–]\d+)?)?'
)
_SCRIPTURE_CHAIN_RE = re.compile(
    rf'(?<!\*)(?:{_SCRIPTURE_SINGLE_RE})(?:\s*\u2192\s*(?:{_SCRIPTURE_SINGLE_RE}))+(?!\*)'
)

# _fix_rtl_in_text перенесена в core_utils.py (разрыв цикла markdown ↔ caption).
# Импортируется выше: from core.core_utils import ..., _fix_rtl_in_text, ...


def _fix_rtl_in_nodes(nodes: list) -> list:
    """A03: Рекурсивно применяет _fix_rtl_in_text ко всему дереву nodes.
    _fix_rtl_in_text работает только на плоских строках; bidi-символы
    внутри bold/italic/blockquote нод оставались нетронутыми и рендерились
    как видимые артефакты на iOS/Android."""
    def _walk(node):
        if isinstance(node, str):
            return _fix_rtl_in_text(node)
        if isinstance(node, dict) and "children" in node:
            node = dict(node)
            node["children"] = [_walk(c) for c in node["children"]]
        return node
    return [_walk(n) for n in nodes]


def _dedup_consecutive_timestamps(content: str) -> str:
    """FIX BUG-4: when two consecutive paragraphs start with the same timestamp,
    merge them by removing the duplicate timestamp from the second paragraph."""
    if not content:
        return content
    _TS_START = re.compile(r'^(\d{1,2}:\d{2}(?::\d{2})?)(?:\s|$)')
    lines = content.split('\n')
    result = []
    prev_ts = None
    for line in lines:
        m = _TS_START.match(line.strip())
        if m:
            cur_ts = m.group(1)
            if cur_ts == prev_ts and result:
                # Same timestamp as previous paragraph — remove timestamp from this line
                line = line.strip()[len(cur_ts):].lstrip()
            prev_ts = cur_ts
        elif line.strip():
            prev_ts = None  # reset on non-timestamp line
        result.append(line)
    return '\n'.join(result)


def _clamp_content_timestamps(content: str, duration: int) -> str:
    """Удаляет или корректирует таймкоды в тексте, превышающие duration видео.
    Работает только с маркированными таймкодами (⏱, 🔗, 📌, ⏳, 📍)."""
    if not duration or not content:
        return content

    def _replace(m: re.Match) -> str:
        _ = m.group(1) or ''
        _ = m.group(2) or ''
        time_str = m.group(3)
        _ = m.group(4) or ''
        secs = time_to_seconds(time_str)
        if secs is None:
            return m.group(0)
        if secs > duration + 30:
            return ''
        # AUDIT-V2-CLAMP: для коротких видео (<1ч): если таймкод > duration,
        # это баг Gemini (добавил лишние часы). Берём MM:SS напрямую из строки,
        # не через secs%60 (который терял минуты: '1:00'→0, '1:30'→30 без контекста).
        if duration < 3600 and secs > duration:
            parts_ts = time_str.split(":")
            if len(parts_ts) == 3:
                corrected = f"{parts_ts[1]}:{parts_ts[2]}"
            elif len(parts_ts) == 2:
                # MM:SS уже правильный формат, но > duration — просто удаляем
                return ''
            else:
                return ''
            corrected_secs = time_to_seconds(corrected)
            if corrected_secs is not None and 0 < corrected_secs <= duration:
                original = m.group(0)
                return original.replace(m.group(3), corrected)
            return ''
        return m.group(0)

    pattern = re.compile(
        r'([⏱🔗📌⏳📍]\s*)'
        r'(\*\*)?'
        r'(\d{1,2}:\d{2}(?::\d{2})?)'
        r'(\*\*)?'
    )
    return pattern.sub(_replace, content)


def _fix_orphaned_bold_markers(content: str) -> str:
    """Чинит непарные ** и *** маркеры которые рендерятся как сырой текст.

    PATCH V2 FIX: убран неверный global early exit.
    Старый код: if global_count % 2 == 0: return content — давал false-negative
    когда строка 5 незакрыта а строка 6 открывает новый **, и сумма чётная.
    Теперь всегда работаем построчно.
    """
    if '**' not in content:
        return content

    lines = content.split('\n')
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue

        # Считаем *** и ** отдельно
        triple_count = stripped.count('***')
        test_no_triple = stripped.replace('***', '')
        double_count = test_no_triple.count('**')

        # Сначала *** (bold+italic)
        if triple_count % 2 == 1:
            if stripped.startswith('***') and triple_count == 1:
                result.append(line.rstrip() + '***')
                continue
            elif stripped.endswith('***') and triple_count == 1:
                result.append(re.sub(r'\*{3}\s*$', '', line).rstrip())
                continue

        # Затем ** (bold)
        if double_count % 2 == 1:
            if stripped.startswith('**') and not stripped.startswith('***'):
                result.append(line.rstrip() + '**')
                continue
            elif stripped.endswith('**') and not stripped.endswith('***'):
                result.append(re.sub(r'\*{2}\s*$', '', line).rstrip())
                continue
            else:
                idx = line.rfind('**')
                if idx >= 0:
                    result.append(line[:idx] + line[idx + 2:])
                    continue

        # Одиночные * (курсив)
        test_single = stripped.replace('***', '').replace('**', '')
        single_count = test_single.count('*')
        if single_count % 2 == 1:
            chars = list(line)
            for idx_c in range(len(chars) - 1, -1, -1):
                if chars[idx_c] == '*':
                    before = chars[idx_c - 1] if idx_c > 0 else ''
                    after  = chars[idx_c + 1] if idx_c < len(chars) - 1 else ''
                    if before != '*' and after != '*':
                        chars.pop(idx_c)
                        break
            line_fixed = ''.join(chars)
            line_fixed = re.sub(r'\s+([.,;:!?])', r'\1', line_fixed)
            line_fixed = re.sub(r' +', ' ', line_fixed)
            result.append(line_fixed)
            continue

        result.append(line)

    return '\n'.join(result)

def _ensure_trailing_period(text: str) -> str:
    """Если последний непустой абзац content не заканчивается на .!?»"…
    — ставим точку.
    Если строка заканчивается на inline-таймкод — точка ставится ПОСЛЕ него.
    Строки-лейблы (**...:), строки на ':', заголовки, hr — пропускаем."""
    if not text.strip():
        return text
    lines = text.rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].rstrip()
        if not s:
            continue
        # Пропускаем заголовки, blockquote, hr, dividers
        if re.match(r'^(#{1,4} |>|---|___|\*\*\*\s*$)', s):
            continue
        # Пропускаем лейблы и строки-заголовки с ':' в конце (с возможной пунктуацией/** после)
        if re.match(r'^\*\*.*:\*?\*?\s*[.!]?\s*$', s) or re.search(r':[*\s.!]*$', s):
            continue
        # Уже есть завершающий знак в конце — всё хорошо
        if re.search(r'(?:[.!?\u2026][»"\)\]]*|[»"]\s*[.!?\u2026])\s*$', s):
            break
        # Строка заканчивается на italic Scripture ref: *(Рим. 8:28)* → не добавлять точку
        if re.search(r'\)\*\s*$', s):
            break
        # Строка кончается inline-таймкодом — точка ПОСЛЕ таймкода
        m = _ENSURE_TS_INLINE_RE.search(s)
        if m:
            lines[i] = s + '.'
            break
        # Ставим точку: перед ** если незакрытый (нечётное число), после если закрытый
        # PATCH V2 FIX: строки-заголовки заканчивающиеся на ":" — не добавляем точку
        _check_colon = re.sub(r'\*{2,3}\s*$', '', s.rstrip())
        if re.search(r':\s*$', _check_colon):
            break
        if s.endswith('**'):
            s.replace('***', '\x00\x00\x00')  # strip *** before counting **
            # Проверяем есть ли точка прямо перед закрывающим ** или *** (с возможным пробелом)
            # Пример: "**Текст.**" или "***Текст.***" — точка уже есть, не дублируем
            _before_stars = re.sub(r'\*{2,3}\s*$', '', s.rstrip())
            if re.search(r'[.!?»"\u2026]\s*$', _before_stars):
                break  # точка уже есть перед ** — ничего не добавляем
            # FIX 2026-05-21 P2: точка ВНУТРЬ закрывающего ** (как для нечётного **),
            # а не снаружи. Снаружи получается '**Жирный**.' — точка вне жирного.
            # Правильно: '**Жирный.**' — точка внутри (как принято в типографике).
            if s.endswith('***'):
                lines[i] = s[:-3].rstrip() + '.***'
            else:
                lines[i] = s[:-2].rstrip() + '.**'
        else:
            lines[i] = s + '.'
        break
    return '\n'.join(lines)


def _ensure_all_paragraphs_period(text: str) -> str:
    """Применяет _ensure_trailing_period к каждому визуальному абзацу.
    Telegraph превращает каждую строку (даже через одинарный \n) в отдельный <p>,
    поэтому обрабатываем каждую строку отдельно, а не только последнюю в блоке."""
    # Разбиваем сохраняя разделители — \n\n+ остаются между частями
    _paras = re.split(r'(\n{2,})', text)
    parts = []
    for p in _paras:
        if not p.strip():
            # Это разделитель (\n\n+) или пустота — не трогаем
            parts.append(p)
            continue
        # Внутри абзаца строки через одинарный \n — каждая идёт в свой <p> в Telegraph
        sub_lines = p.split('\n')
        parts.append('\n'.join(
            _ensure_trailing_period(line) if line.strip() else line
            for line in sub_lines
        ))
    result = ''.join(parts)
    # Убираем ровно две точки подряд (не трогаем многоточие ...)
    result = re.sub(r'(?<!\.)\.\.(?!\.)', '.', result)
    return result


def _linkify_inline_timestamps(nodes: list, yt_url: str, duration: int = 0) -> list:
    """Делает кликабельными inline-таймкоды вида **22:15** внутри content.
    Поддерживает:
    1) таймкод внутри italic-ноды;
    2) text + следующая bold-нода с таймкодом;
    3) stray-мусор вокруг таймкодов;
    4) fallback: plain bold timestamp без маркера (только в обычных абзацах).
    """
    if not yt_url:
        return nodes

    TS_RE = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
    TS_MARKERS = ('⏱', '🔗', '📌', '⏳', '📍', '\u00a0⏱', '\u00a0🔗')
    STRAY_RE = re.compile(r'^\s*(?:\\?\*+|_\.?|\*{1,2}\.?)\s*$')

    def _make_ts_link(marker: str, bold_text: str) -> dict | None:
        secs = time_to_seconds(bold_text)
        if secs is None:
            return None
        if duration and secs > duration + 30:
            return None
        link = get_youtube_timestamp_url(yt_url, secs)
        return {
            "tag": "a",
            "attrs": {"href": link},
            "children": [marker + "\u00a0" + bold_text] if marker else [bold_text],
        }

    def _is_plain_bold_timestamp(node) -> str | None:
        if not isinstance(node, dict):
            return None
        if node.get("tag") != "b":
            return None
        children = node.get("children", [])
        if len(children) != 1 or not isinstance(children[0], str):
            return None
        # rstrip("."): _ensure_trailing_period может поместить точку внутрь bold-ноды
        txt = children[0].strip().rstrip(".")
        return txt if TS_RE.match(txt) else None

    def _process_children(children: list, allow_plain_bold_fallback: bool) -> list:
        new_children = []
        i = 0

        while i < len(children):
            child = children[i]

            # ── Случай 1: italic-нода, внутри которой может быть таймкод ──────────
            if isinstance(child, dict) and child.get("tag") == "i":
                ic = list(child.get("children", []))
                rebuilt_ic: list = []
                extracted_links: list = []
                j = 0

                while j < len(ic):
                    ic_child = ic[j]
                    if (
                        isinstance(ic_child, str)
                        and any(m in ic_child for m in TS_MARKERS)
                        and j + 1 < len(ic)
                    ):
                        ic_nxt = ic[j + 1]
                        if isinstance(ic_nxt, dict) and ic_nxt.get("tag") in ("b", "i"):
                            bc = ic_nxt.get("children", [])
                            # rstrip("."): _ensure_trailing_period может добавить "." внутрь
                            # незакрытого bold-чанка → "18:40." ломает TS_RE.match
                            bold_text = bc[0].strip().rstrip(".") if bc and isinstance(bc[0], str) else ""
                            if TS_RE.match(bold_text):
                                marker = next((_m for _m in TS_MARKERS if _m in ic_child), "")
                                lnk = _make_ts_link(marker, bold_text)
                                if lnk is not None:
                                    _marker_pos = ic_child.rfind(marker) if marker else -1
                                    text_b4 = ic_child[:_marker_pos].rstrip() if _marker_pos >= 0 else ic_child
                                    # Текст ПОСЛЕ маркера внутри той же ic_child-строки (Bug #3)
                                    text_after_marker = (
                                        ic_child[_marker_pos + len(marker):].strip()
                                        if (marker and _marker_pos >= 0) else ""
                                    )
                                    if text_b4:
                                        rebuilt_ic.append(text_b4)
                                    extracted_links.append(lnk)
                                    if text_after_marker:
                                        sep = "" if text_after_marker[0] in '.!?,;:)»"' else " "
                                        extracted_links.append(sep + text_after_marker)
                                    j += 2
                                    continue
                    rebuilt_ic.append(ic_child)
                    j += 1

                if extracted_links:
                    while rebuilt_ic and isinstance(rebuilt_ic[-1], str) and STRAY_RE.match(rebuilt_ic[-1]):
                        rebuilt_ic.pop()
                    if rebuilt_ic and isinstance(rebuilt_ic[-1], str):
                        rebuilt_ic[-1] = re.sub(r'[\*_]+\s*$', '', rebuilt_ic[-1])
                        if not rebuilt_ic[-1].strip():
                            rebuilt_ic.pop()
                    if rebuilt_ic:
                        new_children.append({**child, "children": rebuilt_ic})
                    if new_children and not (
                        isinstance(new_children[-1], str) and new_children[-1].endswith(" ")
                    ):
                        new_children.append(" ")
                    new_children.extend(extracted_links)
                else:
                    new_children.append(child)

                i += 1
                continue

            # ── Случай 2: text-нода с маркером + следующая bold-нода с таймкодом ──
            if (
                isinstance(child, str)
                and any(m in child for m in TS_MARKERS)
            ):
                # ── Случай 2а: маркер + таймкод в одной строке (незакрытый ** или без **)
                # Например: "текст ⏱ **11:45" или "текст ⏱ 11:45"
                _inline_ts_re = re.compile(r'(⏱|🔗)\s*\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}')
                _m = _inline_ts_re.search(child)
                if _m:
                    marker = _m.group(1)
                    bold_text = _m.group(2)
                    lnk = _make_ts_link(marker, bold_text)
                    if lnk is not None:
                        text_before = child[:_m.start()].rstrip()
                        text_after = child[_m.end():].lstrip("* ")
                        if text_before:
                            new_children.append(text_before + " ")
                        elif new_children and isinstance(new_children[-1], dict):
                            new_children.append(" ")
                        new_children.append(lnk)
                        if text_after:
                            # Пробел перед text_after только если он не начинается с пунктуации
                            if text_after[0] in '.!?,;:)»"\u00bb':
                                new_children.append(text_after)
                            else:
                                new_children.append(" " + text_after)
                        else:
                            # таймкод в конце — добавляем точку если её нет
                            prev_text = text_before.rstrip()
                            if prev_text and prev_text[-1] not in '.!?':
                                new_children.append(".")
                        i += 1
                        continue

                # ── Случай 2б: маркер в этой строке + следующая bold/italic-нода с таймкодом
                if i + 1 < len(children):
                    nxt = children[i + 1]
                    if isinstance(nxt, dict) and nxt.get("tag") in ("b", "i"):
                        bc = nxt.get("children", [])
                        # rstrip("."): защита от точки, добавленной _ensure_trailing_period
                        bold_text = bc[0].strip().rstrip(".") if bc and isinstance(bc[0], str) else ""
                        if TS_RE.match(bold_text):
                            marker = next((_m for _m in TS_MARKERS if _m in child), "")
                            lnk = _make_ts_link(marker, bold_text)
                            if lnk is not None:
                                _mp = child.rfind(marker) if marker else -1
                                text_before = child[:_mp].rstrip() if _mp >= 0 else child
                                # Текст в child ПОСЛЕ маркера (до конца строки) — Новый баг А
                                text_after_marker_2b = (
                                    child[_mp + len(marker):].strip()
                                    if (marker and _mp >= 0) else ""
                                )
                                if text_before:
                                    new_children.append(text_before + " ")
                                elif new_children and isinstance(new_children[-1], dict):
                                    new_children.append(" ")
                                new_children.append(lnk)
                                if text_after_marker_2b:
                                    sep2b = "" if text_after_marker_2b[0] in '.!?,;:)»"' else " "
                                    new_children.append(sep2b + text_after_marker_2b)
                                i += 2
                                if (
                                    i < len(children)
                                    and isinstance(children[i], str)
                                    and STRAY_RE.match(children[i])
                                ):
                                    i += 1
                                continue

            # ── Случай 2в: bare timestamp в начале стенограммы ───────────────────
            # Verbatim Synopsis генерирует строки вида `0:07 Мы откроем...`.
            # Это должен быть кликабельный YouTube-якорь, а не просто текст.
            if allow_plain_bold_fallback and isinstance(child, str):
                _bare = re.match(r"^(\s*)(\d{1,2}:\d{2}(?::\d{2})?)(\s+)(.+)$", child, re.S)
                if _bare:
                    lnk = _make_ts_link("", _bare.group(2))
                    if lnk is not None:
                        if _bare.group(1):
                            new_children.append(_bare.group(1))
                        new_children.append(lnk)
                        new_children.append(_bare.group(3) + _bare.group(4))
                        i += 1
                        continue

            # ── Случай 2г: mid-text bare timestamp ───────────────────────────────
            # Updated prompt allows timestamps mid-paragraph: `...фраза. 15:07 Новая мысль...`
            # Split text around timestamps, linkify each one.
            # GUARD: skip if preceded by a Scripture book abbreviation (Рим. 8:28)
            if allow_plain_bold_fallback and isinstance(child, str):
                _MID_TS = re.compile(r'(?<=[\s.!?;,])(\d{1,2}:\d{2}(?::\d{2})?)(?=\s)')
                # FIX AUDIT R4: guard знает и полные названия книг («Иоанна 3:16»),
                # не только аббревиатуры («Ин. 3:16»).
                _SCRIPTURE_BEFORE = re.compile(
                    rf'(?:^|\s)(?:{_SCRIPTURE_BOOK_RE}\.?|{_SCRIPTURE_FULL_BOOK_RE})\s*$'
                )
                _mid_matches = list(_MID_TS.finditer(child))
                if _mid_matches:
                    parts = []
                    last_end = 0
                    any_linked = False
                    for _mm in _mid_matches:
                        ts_text = _mm.group(1)
                        # Skip if preceded by "Книга. " pattern (Scripture ref)
                        before = child[:_mm.start()]
                        if _SCRIPTURE_BEFORE.search(before):
                            continue  # likely Scripture ref, not a timestamp
                        lnk = _make_ts_link("", ts_text)
                        if lnk is not None:
                            if _mm.start() > last_end:
                                parts.append(child[last_end:_mm.start()])
                            parts.append(lnk)
                            last_end = _mm.end()
                            any_linked = True
                    if any_linked:
                        if last_end < len(child):
                            parts.append(child[last_end:])
                        new_children.extend(parts)
                        i += 1
                        continue

            # ── Случай 3: fallback — plain bold timestamp без маркера ─────────────
            if allow_plain_bold_fallback:
                plain_ts = _is_plain_bold_timestamp(child)
                if plain_ts:
                    lnk = _make_ts_link("", plain_ts)
                    if lnk is not None:
                        new_children.append(lnk)
                        i += 1
                        continue

            # ── Случай 4: stray-нода (* / \* / _. / **.) — выбрасываем ───────────
            if isinstance(child, str) and STRAY_RE.match(child):
                stripped = child.strip()
                if stripped in ('*', r'\*', '_.', '**.', '**', '_'):
                    i += 1
                    continue

            new_children.append(child)
            i += 1

        # A06 FIX: удалить stray-символы (* / \* / _. / '') которые остаются
        # после извлечения ссылок-таймкодов из rebuilt_ic
        new_children = [
            c for c in new_children
            if not (isinstance(c, str) and c.strip() in ('*', r'\*', '_.', '', '**', '_'))
        ]
        return new_children

    result = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("tag") not in ("p", "blockquote"):
            result.append(node)
            continue

        children = list(node.get("children", []))
        allow_plain_bold_fallback = node.get("tag") == "p"
        new_children = _process_children(children, allow_plain_bold_fallback=allow_plain_bold_fallback)
        result.append({**node, "children": new_children})

    return result



# ─── FIX 2026-05-24: subsection + scripture normalization ──────────────────

_SUBSECTION_EMOJIS = ('\u26a0\ufe0f', '\u2705', '\u274c', '\U0001f7e2',
                       '\U0001f535', '\U0001f511', '\U0001f4d6',
                       '\U0001f4dc', '\U0001f5fa\ufe0f', '\U0001f3db\ufe0f',
                       '\u2696\ufe0f', '\U0001f4da', '\U0001f9e9',
                       '\U0001f30d', '\U0001f524')

def _patch_subsection_headers(content: str) -> str:
    """Normalize subsection headers from Gemini."""
    lines = content.split('\n')
    result = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m1 = re.match(r'^\*\*(.{3,120}?)\*\*\.?\s*$', s)
        if m1:
            prev_empty = (i == 0) or (not lines[i-1].strip())
            next_s = lines[i+1].strip() if i+1 < len(lines) else ''
            if prev_empty and next_s and not next_s.startswith(('*', '#', '>')):
                text = m1.group(1).strip().rstrip('.')
                result.append(f'- **{text}.**')
                i += 1
                continue
        if s in _SUBSECTION_EMOJIS and i+1 < len(lines):
            next_s = lines[i+1].strip()
            m2 = re.match(r'^\*\*(.{3,120}?)\*\*\.?\s*$', next_s)
            if m2:
                text = m2.group(1).strip().rstrip('.')
                result.append(f'{s} **{text}.**')
                i += 2
                continue
            m2b = re.match(r'^([\u0410-\u042f\u0401A-Z\u00ab\u201c"][^\n]{5,100}[\u2026.\u2026)])\s*$', next_s)
            if m2b:
                text = m2b.group(1).strip().rstrip('.')
                result.append(f'{s} **{text}.**')
                i += 2
                continue
        for _emoji in _SUBSECTION_EMOJIS:
            if s.startswith(_emoji) and len(s) > len(_emoji) + 2:
                _rest = s[len(_emoji):].strip()
                if _rest and len(_rest) > 3 and not _rest.startswith('**'):
                    _title = _rest.rstrip('.')
                    result.append(f'{_emoji} **{_title}.**')
                    i += 1
                    break
        else:
            m4 = re.match(r'^[-\u2022]\s+([\u0410-\u042f\u0401A-Z\u00ab\u201c"][^\n]{5,100}[\u2026.\u2026)])\s*$', s)
            if m4 and i+1 < len(lines):
                next_s = lines[i+1].strip()
                if (next_s and
                    not next_s.startswith(('-', '\u2022', '*', '#', '>', '\u26a0\ufe0f', '\u2705', '\u274c')) and
                    '**' not in m4.group(1)):
                    title = m4.group(1).strip().rstrip('.')
                    result.append(f'- **{title}.**')
                    i += 1
                    continue
            result.append(lines[i])
            i += 1
    return '\n'.join(result)

def _patch_scripture_format(content: str) -> str:
    """Normalize scripture references: merge ref + quote into one line.

    Handles both plain and already-bolded references from Gemini:
      '• Матфея 7:21 – 23.\n*«quote»*'       → '• **Матфея 7:21–23:** *«quote»*'
      '• **Матфея 7:21 – 23.**\n*«quote»*'    → '• **Матфея 7:21–23:** *«quote»*'

    Also normalizes dash spacing: "7:21 – 23" → "7:21–23"
    """
    _BOOKS_RU = (
        'Бытие|Исход|Левит|Числа|Второзаконие|Иисус Навин|Судьи|Руфь|'
        '1\\s*(?:Царств|Пар)|2\\s*(?:Царств|Пар)|3\\s*Царств|4\\s*Царств|'
        'Ездра|Неемия|Есфирь|Иов|Псалом|Псалмы|Притчи|Екклесиаст|Песнь|'
        'Исаия|Иеремия|Плач|Иезекииль|Иезекиль|Даниил|Осия|Иоиль|Амос|Авдий|Иона|'
        'Михей|Наум|Аввакум|Софония|Аггей|Захария|Малахия|'
        'Матфея|Марка|Луки|Луки|Иоанна|Деяния|Римлянам|'
        '1\\s*Коринфянам|2\\s*Коринфянам|Галатам|Ефесянам|Филиппийцам|Колоссянам|'
        '1\\s*Фессалоникийцам|2\\s*Фессалоникийцам|1\\s*Тимофею|2\\s*Тимофею|'
        'Титу|Филимону|Евреям|Иакова|1\\s*Петра|2\\s*Петра|'
        '1\\s*Иоанна|2\\s*Иоанна|3\\s*Иоанна|Иуды|Откровение|'
        'Езекииль|Езекиль'
    )

    # Pattern A: plain   "• Матфея 7:21 – 23."
    _RE_PLAIN = re.compile(
        rf'^(•\s+)({_BOOKS_RU})\s+(\d[\d\s:,;.–—-]*?)\s*\.?\s*$'
    )
    # Pattern B: bolded  "• **Матфея 7:21 – 23.**" or "• **Матфея 7:21 – 23**"
    _RE_BOLD = re.compile(
        rf'^(•\s+)\*\*({_BOOKS_RU})\s+(\d[\d\s:,;.–—-]*?)\s*\.?\*\*\.?\s*$'
    )

    def _norm_verses(v):
        """'7:21 – 23' → '7:21–23'"""
        v = v.strip().rstrip('.')
        return re.sub(r'(\d)\s*[–—-]\s*(\d)', r'\1–\2', v)

    def _is_quote_line(s):
        """Check if line is an italic quote: *«...»* or _«...»_ etc."""
        s = s.strip()
        # *«text»*  or  _«text»_
        if re.match(r'^[*_]?[«"„].*[»"""][*_]?\.?\s*$', s):
            return True
        # «text» without italic markers
        if re.match(r'^[«"„].*[»"""]\.?\s*$', s):
            return True
        return False

    def _extract_quote(s):
        """Extract clean quote text from a quote line."""
        s = s.strip()
        # Remove leading/trailing * or _
        s = re.sub(r'^[*_]+', '', s)
        s = re.sub(r'[*_]+\.?\s*$', '', s)
        # Remove trailing .
        s = s.rstrip('.').rstrip()
        # Strip ONLY outermost quotes (not inner)
        if s.startswith('«') and '»' in s:
            last_rq = s.rfind('»')
            s = s[1:last_rq]
        elif s.startswith('“') and '”' in s:
            last_rq = s.rfind('”')
            s = s[1:last_rq]
        return s.strip()

    lines = content.split('\n')
    result = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()

        # Try Pattern A (plain) then Pattern B (bolded)
        m = _RE_PLAIN.match(s)
        if not m:
            m = _RE_BOLD.match(s)

        if m:
            _ = m.group(1)  # "• "
            book = m.group(2).strip()
            verses = _norm_verses(m.group(3))
            ref = f'{book} {verses}'

            # Check next non-empty line for quote (skip blank lines)
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _is_quote_line(lines[j]):
                quote = _extract_quote(lines[j])
                result.append(f'• **{ref}:** *«{quote}»*')
                i = j + 1
                continue
            else:
                # No quote on next line — just bold the reference with colon
                result.append(f'• **{ref}.**')
                i += 1
                continue

        result.append(lines[i])
        i += 1
    return '\n'.join(result)



def _structured_blocks_to_nodes_v2(
    blocks: list,
    *,
    yt_url: str = "",
    duration: int = 0,
    plain_scripture: bool = False,
) -> list:
    """Render structured Gemini blocks without reverse-engineering flat content.

    This is a forward-compatible path for schemas that return section.blocks.
    The old ``content`` string remains a fallback, but when blocks are present we
    preserve paragraph/source/scripture/bullet boundaries explicitly instead of
    relying on slash/bullet regex repairs.
    """
    if not isinstance(blocks, list):
        return []
    from services.telegraph import _md_to_telegraph_nodes as _md_to_nodes_fn  # lazy: telegraph→markdown cycle

    chunks: list[str] = []
    for raw in blocks:
        raw = normalize_structured_block(raw)
        if not isinstance(raw, dict):
            continue
        btype = str(raw.get("type") or "paragraph").strip().lower()
        text = _scrub_inline(str(raw.get("text") or "").strip())
        if btype == "thesis":
            if text:
                chunks.append("**Главная мысль:** " + text)
        elif btype == "argument_spine":
            steps = raw.get("steps") or []
            if isinstance(steps, list) and steps:
                chunks.append("**Ход мысли автора:**\n" + "\n".join(f"- {str(step).strip()}" for step in steps if str(step).strip()))
            elif text:
                chunks.append("**Ход мысли автора:** " + text)
        elif btype == "application":
            challenge = _scrub_inline(str(raw.get("challenge") or text or "").strip())
            anchor = _scrub_inline(str(raw.get("anchor_timestamp") or raw.get("timestamp") or "").strip())
            step = _scrub_inline(str(raw.get("concrete_step") or "").strip())
            # R12: карточки с жирной шапкой — без «•» (запрос оператора)
            line = f"**{challenge}**" if challenge else ""
            if anchor:
                line += f" ⏱ {anchor}"
            if step:
                line += f"\n\nШаг: {step}"
            if line:
                chunks.append(line)
        elif btype in {"paragraph", "para"}:
            if text:
                chunks.append(text)
        elif btype in {"bullet", "list_item", "point"}:
            if text:
                chunks.append("• " + text.lstrip("•- ").strip())
        elif btype in {"scripture", "scripture_quote"}:
            ref = _scrub_inline(str(raw.get("ref") or "").strip())
            quote = _scrub_inline(str(raw.get("quote") or "").strip())
            explanation = _scrub_inline(str(raw.get("role_in_argument") or raw.get("why_relevant") or text or "").strip())
            ts = _scrub_inline(str(raw.get("timestamp") or "").strip())
            line = ""
            clean_quote = quote[1:-1] if quote.startswith('«') and quote.endswith('»') and len(quote) >= 2 else quote
            if ref and quote:
                line = f"• **{ref}:** *«{clean_quote}»*"
            elif ref:
                line = f"• **{ref}.**"
            elif quote:
                line = f"• *«{clean_quote}»*"
            elif text:
                line = "• " + text.lstrip("•- ").strip()
            if line and ts:
                line += f" ⏱ {ts}"
            if line and explanation:
                line += "\n\n" + explanation
            if line:
                chunks.append(line)
        elif btype in {"pull_quote"}:
            quote = _scrub_inline(str(raw.get("quote") or text or "").strip())
            ts = _scrub_inline(str(raw.get("timestamp") or "").strip())
            if quote:
                clean_quote = quote[1:-1] if quote.startswith('«') and quote.endswith('»') and len(quote) >= 2 else quote
                chunks.append(f"> **{clean_quote}**" + (f" ⏱ {ts}" if ts else ""))
        # NOTE: after normalize_structured_block(), raw aliases like "quote",
        # "blockquote", "theological_line", "lexical_analysis" are already
        # canonical (pull_quote, bullet, lexicon).  The handlers below are
        # kept as a defensive fallback in case a future code path bypasses
        # normalize_structured_block().
        elif btype in {"quote", "blockquote", "block_quote"}:
            quote = _scrub_inline(str(raw.get("quote") or text or "").strip())
            if quote:
                clean_quote = quote[1:-1] if quote.startswith('«') and quote.endswith('»') and len(quote) >= 2 else quote
                chunks.append(f"> *«{clean_quote}»*")
        elif btype in {"source", "source_card", "bibliography"}:
            author_raw = str(raw.get("author") or "").strip()
            author = _scrub_inline(author_raw)
            title_original = _scrub_inline(str(raw.get("title_original") or raw.get("title") or "").strip())
            why = _scrub_inline(str(raw.get("why_relevant") or text or "").strip())
            if is_disallowed_source_author(author_raw) or is_disallowed_source_author(author):
                continue
            # PATCH-FIX: use canonical SourceCard rendering for consistent
            # title-first style with known-author corrections and ru titles.
            card = build_source_card(
                author=author,
                title_original=title_original,
                original_author=author_raw,
                fallback_ru_title="",
                why_relevant=why,
            )
            rendered = render_source_card(card, trailing_period=False)
            if rendered:
                if why and card.why_relevant:
                    chunks.append(f"{rendered}. — {why}")
                else:
                    chunks.append(f"{rendered}.")
            elif why:
                chunks.append(why)
        elif btype in {"theological_line", "historical_line"}:
            heading = _scrub_inline(str(raw.get("title_original") or raw.get("text") or "").strip())
            why = _scrub_inline(str(raw.get("why_relevant") or raw.get("role_in_argument") or "").strip())
            if heading and why:
                chunks.append(f"**{heading}** — {why}")  # R12: карточка без «•»
            elif why:
                chunks.append("• " + why)
        elif btype in {"lexicon", "term", "lexical_analysis"}:
            lemma = _scrub_inline(str(raw.get("lemma") or "").strip())
            role = _scrub_inline(str(raw.get("role_in_argument") or text or "").strip())
            if lemma and role:
                chunks.append(f"**{lemma}** — {role}")  # R12: карточка без «•»
            elif role:
                chunks.append("• " + role)
        elif btype in {"heading", "subheading"}:
            if text:
                chunks.append(f"**{text.rstrip(':')}:**")
        else:
            if text:
                chunks.append(text)

    markdown = "\n\n".join(c for c in chunks if c.strip()).strip()
    if not markdown:
        return []
    # AUDIT R12 (дампы 2026-07-06): blocks-путь шёл МИМО зачисток content-пути —
    # кружки у карточек-определений/лексики и «. ⏱ X:XX.» уходили в Telegraph.
    # Литеральный «\n» из перекодированного JSON превращаем в НАСТОЯЩИЙ перенос
    # (он стоит на месте разрыва абзаца после жирного вопроса — заменять
    # пробелом значило бы склеить шапку с телом).
    markdown = markdown.replace('\\n', '\n')
    markdown = _strip_card_bullets(markdown)
    markdown = _fix_ts_period_order(markdown)
    if duration:
        markdown = _clamp_content_timestamps(markdown, duration)
    if plain_scripture:
        markdown = re.sub(r'\*((?:\([^)]*\d+:\d+[^)]*\)))\*', r'\1', markdown)
    nodes = _md_to_nodes_fn(markdown)
    if yt_url:
        nodes = _linkify_inline_timestamps(nodes, yt_url, duration=duration)
    return nodes

def _section_to_nodes_v2(
    section: dict,
    yt_url: str = "",
    rutube_url: str = "",
    vk_url: str = "",
    page_title: str = "",
    duration: int = 0,
    plain_scripture: bool = False,  # FIXED #127: REFLECTION требует plain text для Scripture refs
) -> list:
    """
    Конвертирует один section в Telegraph nodes.

    Стабилизированная версия:
    - меньше агрессивных regex-склеек;
    - лучше сохраняет numbered/bullet структуры;
    - убирает stray '*' возле таймкодов;
    - не тянет жирный через длинные куски;
    - не рендерит лишние divider lines.
    """
    nodes = []

    title = (section.get("title") or "").strip()
    time_str = (section.get("time") or "").strip()

    # Если Gemini вернул диапазон "0:03:50•0:12:40" или "0:03:50-0:12:40" — берём только начало
    if time_str:
        time_str = re.split(r"[•\-–—]", time_str)[0].strip()

    # Для аналитических страниц без yt_url "0:00" бессмысленно
    if time_str in ("0:00", "00:00", "0:00:00", "00:00:00") and not yt_url:
        time_str = ""

    if time_str and duration:
        secs = time_to_seconds(time_str)
        if secs is not None and secs > duration + 30:
            time_str = ""

    # Убираем тех. пометки частей
    title = re.sub(r'\s*\(ч\.\s*\d+(?:/\d+)?\)\s*', '', title).strip()
    title = re.sub(r'\s*\(часть\s*\d+(?:/\d+)?\)\s*', '', title, flags=re.IGNORECASE).strip()
    # AUDIT R11: заголовки секций не начинаются с маркеров списка —
    # h3/h4 сами дают визуальную иерархию, «•» тут лишний шум.
    title = re.sub(r'^\s*[•▪◦‣·]\s*', '', title)

    content = (section.get("content") or "").strip()
    # AUDIT R12: литеральный «\n» → настоящий перенос (см. blocks-путь).
    content = content.replace('\\n', '\n')
    content = _strip_meta_lines(content)
    # Gemini иногда возвращает literal escaped markdown: \*\*жирный\*\*.
    # Дальше весь renderer ожидает обычные **, иначе Telegraph показывает сырьё.
    content = unescape_markdown_markers(content)
    # AUDIT R17 (дамп 2026-07-09, самостоятельная проверка дампа Razbor):
    # в TYPE 6 («Заблуждения и ответ ортодоксии») Gemini иногда не ставит
    # \n\n перед "✅ **Ответ ортодоксальной церкви.**" — маркер приклеивается
    # одним пробелом к концу предыдущего ❌-абзаца. Промпт (core/prompts.py)
    # требует отдельный абзац на пару ❌+✅; чиним склейку детерминированно.
    content = re.sub(r'(?<=[.!?])[ \t]+(?=[❌✅]\s*\*\*)', '\n\n', content)
    content = re.sub(r"(?m)^(\s*[•\-]\s*)\*\*\s*[•\-]\s*", r"\1**", content)
    content = normalize_misbolded_bullet_lead(content)
    # AUDIT R8+R11 (визуал, запрос оператора): карточки с жирной шапкой теряют
    # маркер «•» — жирный сам держит структуру, лес кружочков перегружает.
    # «Карта источников» защищена дополнительно по названию секции: там жирный
    # позже снимается целиком, и • — единственный маркер карточки.
    content = _strip_card_bullets(
        content,
        long_bold=not re.search(r'карта\s+источников', title or "", flags=re.IGNORECASE),
    )
    # AUDIT R12: правило «точка после таймкода» — ДО линкификации (после неё
    # таймкод разрезан на <a>-узел и строковые правила бессильны).
    content = _fix_ts_period_order(content)

    # ------------------------------------------------------------------
    # 1. БАЗОВАЯ НОРМАЛИЗАЦИЯ ТЕКСТА
    # ------------------------------------------------------------------

    # Убираем явные markdown-артефакты вокруг скобок/заголовков
    content = re.sub(r'\*\*\*\[([^\]]+)\]\*\*\*', r'***\1***', content)
    content = re.sub(r'\*\*\[([^\]]+)\]\*\*', r'**\1**', content)
    content = re.sub(r'\[\*\*([^\]]+)\*\*\]', r'**\1**', content)
    content = re.sub(r'\*\[([^\]]+)\]\*', r'*\1*', content)

    # Слипание с жирным/цитатой
    content = re.sub(r'([а-яА-ЯёЁa-zA-Z])\*\*«', r'\1 **«', content)
    content = re.sub(r'»\*\*([а-яА-ЯёЁa-zA-Z])', r'»** \1', content)



    # Пустые / двойные blockquote markers
    content = re.sub(r'^>\s*>\s*', '> ', content, flags=re.MULTILINE)
    content = re.sub(r'^>\s*$', '', content, flags=re.MULTILINE)
    # FIX 2026-05-24: subsection + scripture normalization

    content = _patch_subsection_headers(content)

    content = _patch_scripture_format(content)


    # Слишком много звёздочек
    content = re.sub(r'\*{4,}', '**', content)

    # Висячий bullet-маркер "*" -> bullet (включая "* **Bold**")
    content = re.sub(r'(?m)^\*\s+(\*\*|[^\*\n])', r'• \1', content)

    # Авто-жирный для заголовков scripture-блоков: "• Книга Глава:Стих." -> "• **Книга Глава:Стих.**"
    # Gemini иногда забывает поставить ** вокруг ссылки-заголовка блока
    _scripture_full_book_re = (
        r'(?:Бытие|Исход|Левит|Числа|Второзаконие|Иисус Навин|Судьи|Руфь|'
        r'1\s*(?:Царств|Пар)|2\s*(?:Царств|Пар)|3\s*Царств|4\s*Царств|'
        r'Ездра|Неемия|Есфирь|Иов|Псалом|Притчи|Екклесиаст|Песнь|'
        r'Исаия|Иеремия|Плач|Иезекиль|Даниил|Осия|Иоиль|Амос|Авдий|Иона|'
        r'Михей|Наум|Аввакум|Софония|Аггей|Захария|Малахия|'
        r'Матфея|Марка|Луки|Иоанна|Деяния|Римлянам|'
        r'1\s*Коринфянам|2\s*Коринфянам|Галатам|Ефесянам|Филиппийцам|Колоссянам|'
        r'1\s*Фессалоникийцам|2\s*Фессалоникийцам|1\s*Тимофею|2\s*Тимофею|'
        r'Титу|Филимону|Евреям|Иакова|1\s*Петра|2\s*Петра|'
        r'1\s*Иоанна|2\s*Иоанна|3\s*Иоанна|Иуды|Откровение)'
    )
    content = re.sub(
        rf'(?m)^(•\s+)(?!\*\*)({_scripture_full_book_re}\s+\d[\d\s:,;.–-]*)(\s*\([^)]*\))?(\s*\.)',
        lambda m: m.group(1) + '**' + m.group(2).rstrip() + ('**' + m.group(3) if m.group(3) else '**') + m.group(4),
        content
    )

    # Нормализуем ссылки на Писание в скобках -> курсив
    # поддерживает: (Рим. 8:28), (Рим. 8:28, 29), (Рим. 8:28; 1 Кор. 15:14), (1 Кор. 15:1–4)
    # A07 FIX: используем _SCRIPTURE_REF_RE и _SCRIPTURE_CHAIN_RE с уровня модуля
    # PATCH V2 FIX: оборачиваем Scripture refs в курсив только если ещё не обёрнуто
    def _wrap_scripture_safe(m_sc: re.Match) -> str:
        start_sc = m_sc.start()
        if start_sc > 0 and content[start_sc - 1] == '*':
            return m_sc.group(0)
        end_sc = m_sc.end()
        if end_sc < len(content) and content[end_sc] == '*':
            return m_sc.group(0)
        return f'*{m_sc.group(0)}*'
    content = _SCRIPTURE_REF_RE.sub(_wrap_scripture_safe, content)

    # #69: Scripture chain refs с → (без скобок): Ис. 40:6 → Рим. 8:28 → Лк. 24:44
    content = _SCRIPTURE_CHAIN_RE.sub(lambda m: f'*{m.group(0)}*', content)

    # ------------------------------------------------------------------
    # 2. ЧИСТКА НЕЗАКРЫТЫХ BOLD И ТАЙМКОДНЫХ АРТЕФАКТОВ
    # ------------------------------------------------------------------

    # **Заголовок: без закрывающего ** в конце строки → закрываем
    # Например: "• **2 Коринфянам 5:17" или "**В материале:"
    content = re.sub(r'(?m)^(\s*[•\-]?\s*)\*\*([^*\n]+?)\s*$', r'\1**\2**', content)

    # **Метка:\nТекст... - разбиваем на bold-заголовок + обычный параграф
    # Например: "**В материале:\nВошер..." -> "**В материале:**\nВошер..."
    content = re.sub(r'\*\*([^*\n]{1,60}:)\n', r'**\1**\n', content)
    content = re.sub(r'(?m)^\s*\*\*(В материале:?)\s*$', r'**\1**', content)

    # Gemini оборачивает текст до таймкода в bold: **текст...icon** 16:15
    # Убираем лишний bold, оставляем только таймкод
    content = re.sub(r'\*\*(.{10,}?)(\u23F1|\U0001F550|\U0001F517)\*\*(\s*)(\d{1,2}:\d{2}(?::\d{2})?)', r'\1\2 **\4**', content)
    # Незакрытый ** внутри строки перед иконкой таймкода: **текст ⏱ 31:30 -> текст ⏱ **31:30**
    content = re.sub(r'\*\*([^*\n]{5,}?)(\s*)(\u23F1|\U0001F550|\U0001F517)(\s*)(\d{1,2}:\d{2}(?::\d{2})?)\s*$', r'\1\2\3 **\5**', content, flags=re.MULTILINE)

    content = re.sub(r'(⏱|🔗)?\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)\*\*', lambda m: (m.group(1) + ' ' if m.group(1) else '') + f'**{m.group(2)}**', content)
    content = re.sub(r'(⏱|🔗)?\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)(?!\*)(?=\s*$)', lambda m: (m.group(1) + ' ' if m.group(1) else '') + f'**{m.group(2)}**', content, flags=re.MULTILINE)
    content = re.sub(r'(⏱|🔗)?\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)(?!\*)(?!:\d)', lambda m: (m.group(1) + ' ' if m.group(1) else '') + f'**{m.group(2)}**', content)
    content = re.sub(r'\*\*(⏱|🔗)', r'** \1', content)

    # **Текст ⏱** 26:00 → Текст ⏱ 26:00  (AI оборачивает весь блок в bold с иконкой внутри)
    content = re.sub(r'\*\*(.+?)\s*⏱\*\*\s*(\d{1,2}:\d{2}(?::\d{2})?)(\s*)', r'\1 ⏱ \2\3', content)
    # Остаток **. после таймкода (только если пробел перед **, иначе это закрывающий bold)
    content = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+\*\*\s*\.?\s*$', r'\1', content, flags=re.MULTILINE)
    # Абзац из одиночного знака пунктуации (AI-артефакт вида "\n\n.\n\n")
    content = re.sub(r'\n{2,}[.,:;]\n{2,}', '\n\n', content)
    content = re.sub(r'(⏱|🔗)?\s*_\s*(\d{1,2}:\d{2}(?::\d{2})?)',
                     lambda m: (m.group(1) + ' ' if m.group(1) else '') + f'**{m.group(2)}**',
                     content)

    content = re.sub(r'\s*\\\*\s*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'(?<!\*)(\d{1,2}:\d{2}(?::\d{2})?)\s*\*+\s*$', r'\1', content, flags=re.MULTILINE)
    content = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+\*(?!\*)', r'\1 ', content)
    content = re.sub(r'(?<!\*)(\d{1,2}:\d{2}(?::\d{2})?)\s*\*{2}\.?\s*$', r'\1', content, flags=re.MULTILINE)
    content = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s*_\.?', r'\1', content)
    content = re.sub(r'(?<!\*)\*(?!\*)(\s*\d{1,2}:\d{2}(?::\d{2})?)', r'\1', content)

    content = re.sub(r'([\wа-яА-ЯёЁ])([⏱🔗])', r'\1 \2', content)
    content = re.sub(r'  +(\d{1,2}:\d{2}(?::\d{2})?)', r' \1', content)

    # ------------------------------------------------------------------
    # 3. ЧИСТКА СКЛЕЕК И ПРОБЕЛОВ
    # ------------------------------------------------------------------

    # Пробел после точки/восклиц./вопроса/двоеточия, если дальше буква
    content = re.sub(r'(?<!\d)([.!?…:])([А-ЯЁA-Z])', r'\1 \2', content)
    content = re.sub(r'(:\d{1,3})\.([А-ЯЁA-Z])', r'\1. \2', content)
    # FIX 2026-05-21 P0: добавляем пробел между .!?…: и **bold** только если
    # за ** идёт непустой символ (т.е. ** — открывающий маркер). Иначе закрывающий
    # ** после ':' получает разделение и вся пара '**...**' сдвигается → ломается весь абзац.
    content = re.sub(r'([.!?…:])(\*\*)([^\s\*])', r'\1 \2\3', content)

    # Пробел вокруг тире
    content = re.sub(r'([\wа-яА-ЯёЁ\*\)])\s*—\s*([\wа-яА-ЯёЁ\*\(])', r'\1 — \2', content)
    content = re.sub(r'([\wа-яА-ЯёЁ\*\)])\s*–\s*([\wа-яА-ЯёЁ\*\(])', r'\1 – \2', content)

    # "слово(**" / ")(буква"
    content = re.sub(r'([а-яА-ЯёЁa-zA-Z\*])\(', r'\1 (', content)
    content = re.sub(r'\)([а-яА-ЯёЁa-zA-Z])', r') \1', content)

    # Стык кириллицы/латиницы
    content = re.sub(r'([а-яА-ЯёЁ])([a-zA-Z])', r'\1 \2', content)
    content = re.sub(r'([a-zA-Z])([а-яА-ЯёЁ])', r'\1 \2', content)

    # Двойные пробелы
    content = re.sub(r'[ \t]{2,}', ' ', content)

    # ------------------------------------------------------------------
    # 4. СТАБИЛИЗАЦИЯ NUMBERED / BULLET / MICRO-BLOCK СТРУКТУРЫ
    # ------------------------------------------------------------------

    lines = content.split("\n")
    rebuilt_lines = []
    j = 0

    while j < len(lines):
        raw = lines[j]
        s = raw.strip()

        # пропускаем мусорные divider-повторы — hr потом поставит markdown-конвертер
        if re.match(r'^[-=_*═]{3,}$', s):
            # не копим по два divider подряд
            if rebuilt_lines and re.match(r'^\s*[-=_*═]{3,}\s*$', rebuilt_lines[-1].strip()):
                j += 1
                continue
            rebuilt_lines.append('---')
            j += 1
            continue

        # heading markdown внутри content -> bold line
        if s.startswith("#### "):
            rebuilt_lines.append(f"**{s[5:].strip()}**")
            # PATCH V2 FIX: пустая строка после → отдельный <p> в Telegraph
            if j + 1 < len(lines) and lines[j + 1].strip():
                rebuilt_lines.append("")
            j += 1
            continue
        if s.startswith("### "):
            rebuilt_lines.append(f"**{s[4:].strip()}**")
            if j + 1 < len(lines) and lines[j + 1].strip():
                rebuilt_lines.append("")
            j += 1
            continue
        if s.startswith("## "):
            rebuilt_lines.append(f"**{s[3:].strip()}**")
            if j + 1 < len(lines) and lines[j + 1].strip():
                rebuilt_lines.append("")
            j += 1
            continue
        if s.startswith("# "):
            rebuilt_lines.append(f"**{s[2:].strip()}**")
            if j + 1 < len(lines) and lines[j + 1].strip():
                rebuilt_lines.append("")
            j += 1
            continue

        # Если строка оканчивается на ":" и следующая — bullet/numbered/bold-заголовок,
        # вставляем paragraph boundary. Обычное продолжение текста — не трогаем.
        if s.endswith(":") and j + 1 < len(lines):
            nxt = lines[j + 1].strip()
            if nxt and re.match(r'^([•\-]|\d+\.|\*\*[^*])', nxt):
                rebuilt_lines.append(s)
                rebuilt_lines.append("")
                j += 1
                continue

        # number-only line: "1." -> подтягиваем следующую строку
        if re.match(r'^\d+\.$', s) and j + 1 < len(lines):
            nxt = lines[j + 1].strip()
            if nxt:
                rebuilt_lines.append(f"{s} {nxt}")
                j += 2
                continue

        # "-Текст" -> "- Текст"
        if re.match(r'^-[^\s]', s):
            s = re.sub(r'^-([^\s])', r'- \1', s)

        rebuilt_lines.append(s if s else "")
        j += 1

    content = "\n".join(rebuilt_lines)

    # ------------------------------------------------------------------
    # 5. АККУРАТНАЯ РАБОТА С ЖИРНЫМ
    # ------------------------------------------------------------------

    # Плохо: "**Тезис.Длинное тело без пробела..."
    content = re.sub(
        r'(\*\*[^*\n]{3,120}[.!?»"]\*\*)([А-ЯЁA-Z])',
        r'\1\n\n\2',
        content
    )

    # numbered title + bold title должны оставаться на одной строке
    content = re.sub(
        r'(?m)^(\d+\.)\s*\n+\s*(\*\*[^*\n]+\*\*)',
        r'\1 \2',
        content
    )

    # Слишком длинный bold-абзац -> если он реально длинный, не держим всё жирным
    def _shrink_overbold_line(m):
        inner = m.group(1).strip()
        # Сохраняем таймкод до его удаления (Bug #4)
        _ts_save = re.search(r'(([⏱🔗]\s*)?\*{0,2}\d{1,2}:\d{2}(?::\d{2})?\*{0,2})\s*$', inner)
        ts_suffix = ""
        if _ts_save:
            _raw = _ts_save.group(1).strip()
            _tm = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', _raw)
            _mk = re.search(r'([⏱🔗])', _raw)
            if _tm:
                ts_suffix = ((_mk.group(1) + " ") if _mk else "") + f"**{_tm.group(1)}**"
        inner = re.sub(r'\s*[⏱🔗]?\s*\**\s*\d{1,2}:\d{2}(?::\d{2})?\s*\**\s*$', '', inner).strip()
        inner = re.sub(r'\s+\d{1,2}:\d{2}(?::\d{2})?\s*$', '', inner).strip()
        _suffix = (" " + ts_suffix) if ts_suffix else ""
        if len(inner) > 120:
            # PATCH V2 FIX: расширяем split — также по » " … перед заглавной
            split_m = re.search(r'(?<=[.!?\u00bb"\u2026])\s+(?=[А-ЯЁA-Z\u00ab"])', inner)
            if split_m:
                head = inner[:split_m.start()].strip()
                tail = inner[split_m.end():].strip()
                return f"**{head}**\n\n{tail}{_suffix}"
        return f"**{inner}**{_suffix}"

    content = re.sub(
        r'(?m)^\*\*([^\n*]{120,})\*\*$',
        _shrink_overbold_line,
        content
    )

    # Ложный курсив-блок (Gemini обернул весь абзац в *...*) — убираем обёртку,
    # оставляем внутренний **bold** нетронутым.
    def _strip_italic_wrap(m):
        inner = m.group(1)
        # #53/#75: не снимаем курсив со Scripture-цитат в «ёлочках» и Scripture refs в скобках
        stripped_inner = inner.lstrip()
        if stripped_inner.startswith('«'):
            return m.group(0)
        if stripped_inner.startswith('('):
            if plain_scripture:
                return inner      # FIXED #127: REFLECTION — снять italic с Scripture refs
            return m.group(0)     # остальное: сохранить (#53/#75)
        # Латинские названия книг: *Religious Affections*, *Institutes of the Christian Religion*
        # Признаки: только латиница, начинается с заглавной, без точки/! в конце (не предложение)
        if (re.match(r'^[A-Z]', stripped_inner)
                and not re.search(r'[а-яёА-ЯЁ]', stripped_inner)
                and not re.search(r'[.!?]\s*$', stripped_inner)):
            return m.group(0)
        return inner

    # Строки 80+ символов
    content = re.sub(
        r'(?m)^\*([^\n*]{80,})\*\s*$',
        _strip_italic_wrap,
        content
    )
    # Строки 20–79 символов (короткие описательные абзацы)
    content = re.sub(
        r'(?m)^\*([А-ЯЁA-Zа-яёa-z«][^\n]{20,}[^\n*])\*$',
        _strip_italic_wrap,
        content
    )

    content = _fix_orphaned_bold_markers(content)

    # FIX BUG-14: ensure space before inline timestamps glued to quotes/punctuation
    # Gemini sometimes writes: »**17:28** Text  →  should be: ». 17:28 Text
    content = re.sub(r'([»"\')\]\.!?])(\*{0,2})(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}', r'\1 \3', content)

    # FIX BUG-4: deduplicate consecutive paragraphs starting with the same timestamp
    content = _dedup_consecutive_timestamps(content)

    if duration:
        content = _clamp_content_timestamps(content, duration)

    # ------------------------------------------------------------------
    # 6. ФИНАЛЬНАЯ НОРМАЛИЗАЦИЯ АБЗАЦЕВ
    # ------------------------------------------------------------------

    # Не даём схлопнуться служебным заголовкам аудиторий/разделов с первым подпунктом
    # v10b FIX: исключаем случай «Иврит: **תְּשׁוּבָה**» — ** здесь не начало списка,
    # а inline bold с ивритом/греческим/арабским. Без этого "Иврит:" отрывается в отдельный абзац.
    content = re.sub(
        r'(?m)^(?!\d+\.)\s*([^\n:]{5,120}(?<!\d):)\s+(\*\*(?![\u0590-\u05FF\u0600-\u06FF\u0370-\u03FF\w]))',
        r'\1\n\n\2',
        content
    )
    content = re.sub(
        r'(?m)^(?!\d+\.)\s*([^\n:]{5,120}(?<!\d):)\s+([•\-]|\d+\.)',
        r'\1\n\n\2',
        content
    )

    # Нумерованные prayer blocks: если "1. Текст" и потом сразу тело в той же строке
    # допускаем, это лучше чем разваленный номер на отдельной строке
    content = re.sub(r'\n{3,}', '\n\n', content).strip()
    content = _ensure_all_paragraphs_period(content)
    # Убираем 2+ точек в конце строки (AI поставил точку + наш код добавил ещё одну).
    # (?=\s*$) гарантирует что трогаем только конец строки, не .. внутри текста.
    content = re.sub(r'\.{2,}(?=\s*$)', '.', content, flags=re.MULTILINE)
    content = re.sub(r'\?\.\s*\.', '?.', content)

    # ------------------------------------------------------------------
    # 7. H3 + TIMESTAMP LINKS В ЗАГОЛОВКЕ SECTION
    # ------------------------------------------------------------------

    def _strip_emoji_and_prefix(s: str) -> str:
        """Убирает эмодзи, пунктуацию и служебные префиксы для fuzzy-сравнения заголовков."""
        # Убираем эмодзи и спец-символы
        s = re.sub(r'[\U0001F300-\U0001FFFE\U00002700-\U000027BF'
                    r'\u2600-\u26FF\u2700-\u27BF\u23F0-\u23FF\u2B50]', '', s)
        # Убираем префиксы вида "Разбор материала: " / "📖 Разбор материала: "
        s = re.sub(r'^[\w\s]+:\s*', '', s.strip())
        # Убираем суффиксы вида " (часть 1/2)"
        s = re.sub(r'\s*\(\s*часть\s*\d+/\d+\s*\)\s*$', '', s)
        return s.strip().lower()

    _norm_title = _strip_emoji_and_prefix(title or "")
    _norm_page  = _strip_emoji_and_prefix(page_title or "")
    _title_is_page_title = (
        page_title and title and
        # Точное совпадение ИЛИ section_title является частью page_title
        (_norm_title == _norm_page or
         (_norm_title and _norm_page and
          (_norm_title in _norm_page or _norm_page in _norm_title)))
    )

    if title and not _title_is_page_title:
        _clean_title = _HEADING_BOLD_STRIP_RE.sub(r'\1', title)
        # FIX: убираем trailing эмодзи из h3 заголовков секций (на мобильных выглядят нелепо)
        # \uFE00-\uFE0F — variation selectors (e.g. ⚖️ = U+2696 + U+FE0F)
        _clean_title = re.sub(
            r'[\U0001F300-\U0001FFFE\U00002700-\U000027BF'
            r'\u2600-\u26FF\u2700-\u27BF\u23F0-\u23FF\u2B50'
            r'\U0001FA00-\U0001FA9F\U0001F900-\U0001F9FF'
            r'\uFE00-\uFE0F]+\s*$',
            '', _clean_title
        ).rstrip()
        nodes.append({"tag": "h3", "children": [_clean_title]})

        if time_str:
            secs = time_to_seconds(time_str) if time_str else None
            ts_parts = []

            if yt_url and secs is not None:
                yt_link = get_youtube_timestamp_url(yt_url, secs)
                ts_parts.append("⏱\u00a0")
                ts_parts.append({
                    "tag": "a",
                    "attrs": {"href": yt_link},
                    "children": [f"YouTube {time_str}"]
                })
            elif yt_url:
                ts_parts.append(f"⏱\u00a0YouTube {time_str}")

            if rutube_url and secs is not None:
                rt_base = rutube_url.rstrip("/")
                rt_link = rt_base + (("&" if "?" in rt_base else "?") + f"t={secs}")
                if ts_parts:
                    ts_parts.append(" · ")
                ts_parts.append("⏱\u00a0")
                ts_parts.append({
                    "tag": "a",
                    "attrs": {"href": rt_link},
                    "children": [f"RuTube {time_str}"]
                })

            if vk_url and secs is not None:
                vk_link = vk_url + (("&" if "?" in vk_url else "?") + f"t={secs}s")
                if ts_parts:
                    ts_parts.append(" · ")
                ts_parts.append("⏱\u00a0")
                ts_parts.append({
                    "tag": "a",
                    "attrs": {"href": vk_link},
                    "children": [f"VK {time_str}"]
                })

            if ts_parts:
                nodes.append({"tag": "p", "children": [
                    {"tag": "i", "children": ts_parts}
                ]})
            elif time_str:
                nodes.append({"tag": "p", "children": [
                    {"tag": "i", "children": [f"⏱\u00a0{time_str}"]}
                ]})

    # ------------------------------------------------------------------
    # 8. CONTENT -> TELEGRAPH NODES
    # ------------------------------------------------------------------

    blocks = section.get("blocks")
    if isinstance(blocks, list) and blocks:
        block_nodes = _structured_blocks_to_nodes_v2(
            blocks, yt_url=yt_url, duration=duration, plain_scripture=plain_scripture
        )
        if block_nodes:
            nodes.extend(block_nodes)
            return nodes

    if content:
        # PATCH-FIX: apply prompt-context-leak scrubbing on legacy content
        # path (blocks path gets this via audit_expanded_sections, but plain
        # content string does not).  Removes "в материале", "канал занимает
        # позицию" etc. before Markdown → Telegraph node conversion.
        from core.content_audit import _scrub_prompt_context_leaks
        content, _leak_changed = _scrub_prompt_context_leaks(content)
        from services.telegraph import _md_to_telegraph_nodes as _md_to_nodes_fn  # lazy: telegraph→markdown cycle
        content_nodes = _md_to_nodes_fn(content)

        # PATCH V2 FIX RTL: dir="ltr" на <p>/<blockquote>/<li> содержащие иврит/арабский
        # Без этого браузер переключает выравнивание всей строки вправо (BiDi).
        _RTL_DETECT_RE = re.compile(r'[\u0590-\u05FF\u0600-\u06FF]')

        def _has_rtl_deep(node) -> bool:
            if isinstance(node, str):
                return bool(_RTL_DETECT_RE.search(node))
            if isinstance(node, dict):
                return any(_has_rtl_deep(c) for c in node.get("children", []))
            return False

        def _apply_ltr_attr(node):
            if not isinstance(node, dict):
                return node
            tag = node.get("tag", "")
            if tag in ("p", "blockquote", "li"):
                children = node.get("children", [])
                if any(_has_rtl_deep(c) for c in children):
                    node = dict(node)
                    node["attrs"] = {**node.get("attrs", {}), "dir": "ltr"}
                    # v10b RTL FIX: если первый child — dict(b/i) с ивритом,
                    # браузер BiDi «улетает» вправо. LTR Mark перед ним — якорь.
                    if (children
                            and isinstance(children[0], dict)
                            and children[0].get("tag") in ("b", "i")
                            and _has_rtl_deep(children[0])):
                        node["children"] = ["\u200e"] + list(children)
            return node

        content_nodes = [_apply_ltr_attr(n) for n in content_nodes]

        def _unwrap_source_map_bold(node):
            """Keep «Карта источников» source cards visually consistent.

            Gemini sometimes emits one card as `• **Чикагское заявление...**`
            while neighbouring cards are plain. Source-card headings should not
            be bold; book titles inside them may stay italic.

            Also protects against invented Russian book titles in source cards:
            `Автор, _«Выдуманный русский»_ (_Author, Original Title_)` becomes
            `Автор, _Original Title_`. This is safer for bibliography because an
            original title is verifiable while Russian titles are often guessed.
            """
            def _flat_local(n):
                if isinstance(n, str):
                    return n
                if isinstance(n, dict):
                    return "".join(_flat_local(c) for c in n.get("children", []))
                return ""

            if not isinstance(node, dict):
                return node
            nn = dict(node)
            if "children" in nn:
                nn["children"] = [_unwrap_source_map_bold(c) for c in nn.get("children", [])]
            if nn.get("tag") != "p":
                return nn
            ch = list(nn.get("children", []))
            if len(ch) >= 2 and isinstance(ch[0], str) and ch[0].strip().startswith("•"):
                first_inline = ch[1]
                if isinstance(first_inline, dict) and first_inline.get("tag") in ("b", "strong"):
                    ch = [ch[0]] + list(first_inline.get("children", [])) + ch[2:]

                # Prefer original English title over possibly invented Russian translation.
                # Pattern: • Author, <i>«Русский»</i> ( <i>Author, Original</i> ).
                for i, item in enumerate(ch):
                    if not (isinstance(item, dict) and item.get("tag") in ("i", "em")):
                        continue
                    ru_title = _flat_local(item).strip()
                    if not (ru_title.startswith("«") and ru_title.endswith("»")):
                        continue
                    # Find following italic original title, usually after a text node with '('.
                    for j in range(i + 1, min(len(ch), i + 4)):
                        nxt = ch[j]
                        if isinstance(nxt, dict) and nxt.get("tag") in ("i", "em"):
                            en_title = _flat_local(nxt).strip()
                            if not re.search(r'[A-Za-z]', en_title):
                                continue
                            if "," in en_title:
                                en_title = en_title.split(",", 1)[1].strip()
                            prefix = ch[:i]
                            # Drop glue nodes like ' ( ' between Russian and English titles.
                            suffix = ch[j + 1:]
                            if suffix and isinstance(suffix[0], str):
                                suffix[0] = re.sub(r'^\s*\)\.?\s*', '.', suffix[0])
                            ch = prefix + [{"tag": "i", "children": [en_title]}] + suffix
                            break
                    break

                nn["children"] = ch
            return nn

        if re.search(r'карта\s+источников', title or "", flags=re.IGNORECASE):
            content_nodes = [_unwrap_source_map_bold(n) for n in content_nodes]

        if yt_url:
            content_nodes = _linkify_inline_timestamps(content_nodes, yt_url, duration=duration)
        nodes.extend(content_nodes)

    return nodes


def _estimate_nodes_v2(sections: list, yt_url: str = "",
                       rutube_url: str = "", vk_url: str = "",
                       duration: int = 0) -> int:
    """Оценивает кол-во Telegraph nodes для списка разделов."""
    return sum(
        len(_section_to_nodes_v2(s, yt_url=yt_url,
                                  rutube_url=rutube_url, vk_url=vk_url,
                                  duration=duration))
        for s in sections
    )


def _split_sections_smart(sections: list, node_limit: int = 180, yt_url: str = "") -> list:
    """
    Делит sections на части для Telegraph по границам разделов.
    Никогда не разрывает section посередине.
    Если последняя часть < 25% лимита — объединяет с предыдущей (если итог ≤ лимита).
    Возвращает список групп (list of list of section).
    """
    if not sections:
        return [[]]

    parts: list[list] = []
    current: list     = []
    current_count: int = 0

    for sec in sections:
        sec_size = len(_section_to_nodes_v2(sec, yt_url))
        if current and current_count + sec_size > node_limit:
            parts.append(current)
            current = [sec]
            current_count = sec_size
        else:
            current.append(sec)
            current_count += sec_size

    if current:
        parts.append(current)

    # Последняя часть < 25% лимита — объединяем с предыдущей только если влезает
    if len(parts) >= 2:
        last_size = _estimate_nodes_v2(parts[-1], yt_url)
        prev_size = _estimate_nodes_v2(parts[-2], yt_url)
        if last_size < node_limit * 0.25 and prev_size + last_size <= node_limit:
            parts = parts[:-2] + [parts[-2] + parts[-1]]

    return parts or [[]]


def _split_sections_recursive(sections: list, node_limit: int, yt_url: str = "") -> list:
    """
    Рекурсивно делит sections пока каждая часть не влезает в node_limit.
    Единственный способ нарушить — section из одного элемента > node_limit
    (тогда публикуем как есть, Telegraph примет или вернёт ошибку).
    """
    if not sections:
        return [[]]

    total_nodes = _estimate_nodes_v2(sections, yt_url)
    if total_nodes <= node_limit:
        return [sections]

    # Нельзя делить одиночный section
    if len(sections) == 1:
        return [sections]

    mid = max(1, len(sections) // 2)
    left  = sections[:mid]
    right = sections[mid:]

    left_result  = _split_sections_recursive(left, node_limit, yt_url)
    right_result = _split_sections_recursive(right, node_limit, yt_url)
    return left_result + right_result


def _build_toc_nodes_v2(outline: list, yt_url: str = "", parts: list = None, duration: int = 0) -> list:  # noqa: ARG001 (parts reserved for future multi-part TOC links)
    """Строит оглавление «Структура материала» с кликабельными таймкодами."""
    nodes = []
    nodes.append({"tag": "p", "children": [
        {"tag": "b", "children": ["Структура материала"]}
    ]})

    for i, item in enumerate(outline, 1):
        title = (item.get("title") or "").strip()
        time_str = (item.get("time") or "").strip()

        if time_str in ("0:00", "00:00", "0:00:00", "00:00:00") and not yt_url:
            time_str = ""

        if time_str and duration:
            secs = time_to_seconds(time_str)
            if secs is not None and secs > duration + 30:
                time_str = ""

        title = re.sub(r'\s*\(ч\.\s*\d+(?:/\d+)?\)\s*', '', title).strip()
        title = re.sub(r'\s*\(часть\s*\d+(?:/\d+)?\)\s*', '', title, flags=re.IGNORECASE).strip()

        children = [{"tag": "b", "children": [f"{i}.\u00a0"]}]
        # #84: title может содержать **bold** — прогоняем через _md_parse_inline
        # (убран лишний "\u00a0" — давал двойной пробел "1.  Название")
        # _md_parse_inline импортирована из core_utils в начале модуля — lazy import не нужен
        children.extend(_md_parse_inline(title) if title else [])

        if time_str and yt_url:
            secs = time_to_seconds(time_str)
            if secs is not None:
                yt_link = get_youtube_timestamp_url(yt_url, secs)
                children.append(" — ⏱\u00a0")
                children.append({
                    "tag": "a",
                    "attrs": {"href": yt_link},
                    "children": [time_str],
                })
            else:
                children.append(f" — ⏱\u00a0{time_str}")
        elif time_str:
            children.append(f" — ⏱\u00a0{time_str}")

        nodes.append({"tag": "p", "children": children})

    nodes.append({"tag": "hr"})
    return nodes


def _build_nav_nodes_v2(part_idx: int, total_parts: int, parts_urls: list, leading_hr: bool = True) -> list:
    """
    Строит навигационный блок между частями в формате [N/total].
    Пример: ⬅ Назад: [1/3]     ➡ Дальше: [3/3]
    leading_hr: если False — не добавляет <hr> перед навигацией (для верхнего nav блока).
    """
    if total_parts <= 1:
        return []

    nodes = [{"tag": "hr"}] if leading_hr else []
    nav_children = []

    if part_idx > 0:
        prev_url = parts_urls[part_idx - 1] if part_idx - 1 < len(parts_urls) else None
        if prev_url:
            nav_children.append({
                "tag": "a",
                "attrs": {"href": prev_url},
                "children": [f"⬅ Назад: [{part_idx}/{total_parts}]"],
            })

    if part_idx < total_parts - 1:
        next_url = parts_urls[part_idx + 1] if part_idx + 1 < len(parts_urls) else None
        if next_url:
            if nav_children:
                nav_children.append("     ")
            nav_children.append({
                "tag": "a",
                "attrs": {"href": next_url},
                "children": [f"➡ Дальше: [{part_idx + 2}/{total_parts}]"],
            })

    if nav_children:
        nodes.append({"tag": "p", "children": nav_children})

    return nodes


def _build_related_materials_nodes(records: list[dict[str, Any]]) -> list:
    """Build 'Read Next' nodes for the bottom of the Telegraph page."""
    if not records:
        return []
    nodes = [
        {"tag": "hr"},
        {"tag": "p", "children": [{"tag": "b", "children": ["📂 Читать также по теме:"]}]}
    ]
    for r in records:
        title = r.get("title") or "Без названия"
        author = r.get("author") or "Автор не указан"
        url = r.get("synopsis_url") or r.get("study_url") or r.get("reflection_url")
        if not url:
            continue
        nodes.append({"tag": "p", "children": [
            "• ",
            {"tag": "a", "attrs": {"href": url}, "children": [f"{title} — {author}"]}
        ]})
    return nodes


def _final_telegraph_polish(nodes: list) -> list:
    """A05: Финальный слой очистки перед отправкой в Telegraph API.
    Запускать ДО любого createPage/editPage.
    Гарантирует: нет orphaned **, нет пустых nodes, нет h1/h2/h5/h6, нет bidi-мусора."""
    _ORPHAN_BOLD_RE = re.compile(r'(?<!\*)\*{2,3}(?!\*)')
    _BIDI_RE = re.compile(r'[\u2066-\u2069\u202a-\u202e\u200f]')
    # FIX BUG-10: stray English words in Russian text (code-switching from Gemini)
    # Only replace when word is adjacent to Cyrillic (not inside English titles/quotes)
    _STRAY_EN = {
        "especially": "особенно", "because": "потому что",
        "however": "однако", "actually": "на самом деле", "basically": "по сути",
    }
    _STRAY_EN_RE = re.compile(
        r'(?<=[а-яёА-ЯЁ,.:;!?\s])(' + '|'.join(re.escape(k) for k in _STRAY_EN) + r')(?=[\s,.:;!?]*[а-яёА-ЯЁ])',
        re.IGNORECASE,
    )

    def _polish_node(node):
        if isinstance(node, str):
            node = _ORPHAN_BOLD_RE.sub('', node)
            node = _BIDI_RE.sub('', node)
            # AUDIT R9 (иврит): LTR-mark в пробелах рисует дыру «** \u200e —».
            # Финальный чокпоинт: сюда попадают и строки после bold-split.
            node = re.sub(r'[ \t\u00a0]+\u200e', '\u200e', node)
            node = re.sub(r'\u200e{2,}', '\u200e', node)
            # AUDIT R14 (\u0434\u0430\u043c\u043f 2026-07-09): \u043b\u0438\u0448\u043d\u0438\u0439 LTR-mark \u0412 \u041d\u0410\u0427\u0410\u041b\u0415 \u0443\u0437\u043b\u0430
            # (\u00ab\u200e**enosh\u2026\u00bb) \u0431\u0435\u0441\u043f\u043e\u043b\u0435\u0437\u0435\u043d (\u0432\u043f\u0435\u0440\u0435\u0434\u0438 \u043b\u0430\u0442\u0438\u043d\u0438\u0446\u0430, \u0437\u0435\u0440\u043a\u0430\u043b\u0438\u0442\u044c
            # \u043d\u0435\u0447\u0435\u0433\u043e) \u0438 \u0434\u0430\u0451\u0442 \u043c\u0438\u043a\u0440\u043e-\u043e\u0442\u0441\u0442\u0443\u043f \u0441\u0442\u0440\u043e\u043a\u0438. \u041c\u0435\u0442\u043a\u0443 \u041f\u041e\u0421\u041b\u0415 \u0438\u0432\u0440\u0438\u0442\u0430 \u043d\u0435 \u0442\u0440\u043e\u0433\u0430\u0435\u043c.
            node = re.sub(r'^\u200e+', '', node)
            # FIX BUG-10: replace stray English words in Russian context
            node = _STRAY_EN_RE.sub(lambda m: _STRAY_EN.get(m.group(0).lower(), m.group(0)), node)
            # AUDIT R12: «Имя (Имя)» — дубль в скобках (карточки источников:
            # «Джон МакАртур (Джон МакАртур)») схлопывается до одного имени.
            node = re.sub(
                r'([A-ZА-ЯЁ][\w.Ѐ-ӿ-]*(?:\s+[A-ZА-ЯЁ][\w.Ѐ-ӿ-]*){0,3})\s*\(\s*\1\s*\)',
                r'\1', node,
            )
            return node
        if isinstance(node, dict):
            tag = node.get("tag", "").lower()
            if tag in ("h1", "h2", "h5", "h6"):
                node = dict(node)
                node["tag"] = "h3"
            elif tag and tag not in TELEGRAPH_ALL_ALLOWED:
                node = dict(node)
                node["tag"] = "p"
            # FIX 2026-05-21 #6: Telegraph API сохраняет attrs только {href, src}.
            # dir="ltr" в JSON просто игнорируется на их стороне (no-op). Чистим явно,
            # чтобы payload не раздувался и логи были чище.
            if "attrs" in node and isinstance(node["attrs"], dict):
                _kept = {k: v for k, v in node["attrs"].items() if k in ("href", "src")}
                node = dict(node)
                if _kept:
                    node["attrs"] = _kept
                else:
                    node.pop("attrs", None)
            if "children" in node:
                # VISUAL FIX 2026-06-10: пробельная строка МЕЖДУ inline-нодами —
                # значимый разделитель (b("Иоан 4:34:") + " " + i("«цитата»")).
                # Раньше дропалась целиком → жирный текст слипался с курсивом.
                _orig = node["children"]
                new_ch = []
                for _ci, c in enumerate(_orig):
                    if isinstance(c, str) and not c.strip():
                        if 0 < _ci < len(_orig) - 1:
                            new_ch.append(" ")  # схлопываем до 1 пробела, но сохраняем
                        continue
                    new_ch.append(_polish_node(c))
                node = dict(node)
                node["children"] = [c for c in new_ch if c is not None and c != ""]
        return node

    # FIX: пустые контейнерные ноды (например {"tag":"p","children":[]} после
    # удаления orphaned-маркеров) рендерятся в Telegraph как лишний пустой абзац
    # с вертикальным отступом. Самозакрывающиеся теги (hr/br/img) — НЕ пустые.
    _SELF_CLOSING = {"hr", "br", "img"}

    def _is_empty_container(n) -> bool:
        if not isinstance(n, dict):
            return False
        tag = str(n.get("tag", "")).lower()
        if tag in _SELF_CLOSING:
            return False
        if "src" in (n.get("attrs") or {}):
            return False
        return not n.get("children")

    # VISUAL FIX 2026-06-10 (RTL): telegra.ph отбрасывает attrs.dir, а если
    # первый «сильный» символ абзаца — иврит/арабица, браузер рендерит всю
    # строку справа-налево («.Божьего завета» с точкой в начале — скриншот
    # study_p2). Универсальный якорь: LRM (\u200e) в самое начало абзаца.
    _RTL_CHAR_RE = re.compile(r'[\u0590-\u05FF\u0600-\u06FF]')
    _LTR_STRONG_RE = re.compile(r'[A-Za-zА-Яа-яЁё0-9]')

    def _flat_text(n) -> str:
        if isinstance(n, str):
            return n
        if isinstance(n, dict):
            return ''.join(_flat_text(c) for c in n.get("children", []))
        return ''

    def _first_strong_is_rtl(n) -> bool:
        for ch in _flat_text(n):
            if _RTL_CHAR_RE.match(ch):
                return True
            if _LTR_STRONG_RE.match(ch):
                return False
        return False

    def _anchor_ltr(n):
        if (isinstance(n, dict)
                and n.get("tag") in ("p", "li", "blockquote", "h3", "h4")
                and _first_strong_is_rtl(n)):
            ch = list(n.get("children", []))
            if ch and isinstance(ch[0], str):
                if not ch[0].startswith("\u200e"):
                    ch[0] = "\u200e" + ch[0]
            else:
                ch.insert(0, "\u200e")
            n = dict(n)
            n["children"] = ch
        return n

    result = [_polish_node(n) for n in nodes]
    result = [_anchor_ltr(n) for n in result]
    return [n for n in result if n and not _is_empty_container(n)]


async def _create_telegraph_page_single(title: str, author: str,
                                         nodes: list, loop, author_url: str = "") -> tuple:
    """Публикует одну Telegraph-страницу. Возвращает (url, error).
    НЕ усекает контент — CONTENT_TOO_BIG возвращается как ошибка
    для обработки выше по стеку через дробление sections.
    Retry: до 3 попыток при сетевых ошибках; FLOOD_WAIT_N — sleep(N)+retry.
    """
    token = TELEGRAPH_TOKEN
    if not token:
        return None, "no_token"
    from services.telegraph import _clean_telegraph_nodes as _cln_nodes_fn  # lazy: telegraph→markdown cycle
    nodes = _cln_nodes_fn(nodes)
    nodes = _postprocess_telegraph_nodes(nodes)  # CONSPECT QUALITY PATCH
    # AUDIT R11: полировка (orphan **, bidi, code-switching) раньше жила только
    # в пути Synopsis — Study/Reflection публиковались с сырыми «**».
    nodes = _final_telegraph_polish(nodes)
    _audit = audit_telegraph_page(title, nodes, page_type="create")
    if _audit:
        _audit_summary = format_audit_issues(_audit)
        logger.warning("Telegraph createPage audit: %s", _audit_summary)
        if should_abort_for_page_audit(_audit):
            return None, "page_audit_strict: " + _audit_summary[:300]

    # ConnectionError / TimeoutError / OSError handled by broad except below
    last_err = "unknown"

    for attempt in range(3):
        try:
            resp = await loop.run_in_executor(None, lambda: requests.post(
                "https://api.telegra.ph/createPage",
                json={
                    "access_token": token,
                    "title": title,
                    "author_name": (author or "")[:128],  # FIX 2026-05-21 P1: Telegraph API limit
                    "author_url": (author_url or "")[:512],  # AUDIT M21
                    "content": nodes,
                    "return_content": False,
                },
                timeout=30,  # BP-16: 15→30s для большого Study Analysis
            ))
            data = resp.json()
            if data.get("ok"):
                return data["result"]["url"], ""
            err = data.get("error", "unknown")
            # FLOOD_WAIT_N — Telegraph rate-limit: подождать N секунд и повторить
            flood_m = re.match(r"FLOOD_WAIT_(\d+)", err)
            if flood_m:
                wait_sec = min(int(flood_m.group(1)), 30)
                logger.warning(
                    f"Telegraph createPage: FLOOD_WAIT_{wait_sec}s "
                    f"(attempt {attempt+1}/3) — sleeping..."
                )
                await asyncio.sleep(wait_sec)
                last_err = err
                continue
            return None, err
        except Exception as e:
            last_err = str(e)
            logger.warning(f"Telegraph createPage (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s
    return None, last_err


async def _edit_telegraph_page(page_url: str, title: str, author: str,
                                nodes: list, loop, author_url: str = "") -> bool:
    """Обновляет существующую Telegraph-страницу навигационными ссылками."""
    token = TELEGRAPH_TOKEN
    if not token:
        return False
    from services.telegraph import _clean_telegraph_nodes as _cln_nodes_fn2  # lazy: telegraph→markdown cycle
    nodes = _cln_nodes_fn2(nodes)
    nodes = _postprocess_telegraph_nodes(nodes)  # CONSPECT QUALITY PATCH
    nodes = _final_telegraph_polish(nodes)  # AUDIT R11: и на editPage тоже
    _audit = audit_telegraph_page(title, nodes, page_type="edit")
    if _audit:
        _audit_summary = format_audit_issues(_audit)
        logger.warning("Telegraph editPage audit for %s: %s", page_url, _audit_summary)
        if should_abort_for_page_audit(_audit):
            return False
    path = page_url.replace("https://telegra.ph/", "")
    for _edit_attempt in range(3):
        try:
            resp = await loop.run_in_executor(None, lambda: requests.post(
                f"https://api.telegra.ph/editPage/{path}",
                json={
                    "access_token": token,
                    "title": title,
                    "author_name": (author or "")[:128],
                    "author_url": (author_url or "")[:512],
                    "content": nodes,
                    "return_content": False,
                },
                timeout=30,
            ))
            data = resp.json()
            ok = bool(data.get("ok"))
            if ok:
                try:
                    from services.pdf_generator import invalidate_telegraph_url
                    invalidate_telegraph_url(page_url)
                except Exception:
                    pass
                return True
            err_msg = data.get("error", "unknown error")
            flood_m = re.match(r"FLOOD_WAIT_(\d+)", err_msg)
            if flood_m:
                wait_sec = min(int(flood_m.group(1)), 30)
                logger.warning(
                    "Telegraph editPage: FLOOD_WAIT_%ds (attempt %d/3) — sleeping...",
                    wait_sec, _edit_attempt + 1,
                )
                await asyncio.sleep(wait_sec)
                continue
            logger.warning("_edit_telegraph_page: API returned ok=False, error=%r, url=%s", err_msg, page_url)
            return False
        except Exception as e:
            logger.warning("Telegraph editPage (attempt %d/3): %s", _edit_attempt + 1, e)
            if _edit_attempt < 2:
                await asyncio.sleep(3)
                continue
            return False
    return False


# ─── Synopsis v2.0: helper для Gemini ────────────────────────

def _extract_partial_sections(text: str) -> list:
    """Безопасный fallback-парсер для кривого JSON.
    Посимвольный сканер корректно обрабатывает вложенные {} и любой порядок ключей."""
    sections = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                raw = text[start:i + 1]
                if '"content"' in raw:
                    try:
                        obj = json.loads(raw)
                        if isinstance(obj, dict):
                            t = str(obj.get("title",   "")).strip()
                            v = str(obj.get("time",    "")).strip() or "0:00"
                            c = str(obj.get("content", "")).strip()
                            if t and c:
                                sections.append({"title": t, "time": v, "content": c})
                    except Exception as _e:
                        logger.debug("_extract_partial_sections: skipped malformed section: %s", _e)
                start = -1
    return sections

def visible_length(text: str) -> int:
    """
    Считает длину текста так, как его видит Telegram после парсинга HTML:
      • все теги (<b>, <a href="...">, </a>, <tg-emoji ...>, </tg-emoji> и т.д.) не считаются
      • &amp; &lt; &gt; &quot; &#32; и т.п. считаются как 1 символ каждый
      • <tg-emoji>...</tg-emoji> — считается вложенный текст (обычно 1 emoji)
      • <a href="...">текст</a> — считается только «текст»
    Не использует внешних библиотек. Работает через re.
    """
    # Убираем все теги целиком, оставляя только их текстовое содержимое
    # Порядок важен: сначала убираем самозакрывающиеся / пустые теги без текста внутри
    s = text
    # A08 FIX: используем pre-compiled паттерны вместо компиляции при каждом вызове
    s = _VL_TAG_RE.sub('', text)
    s = _VL_NAMED_RE.sub('x', s)
    s = _VL_DEC_RE.sub('x', s)
    s = _VL_HEX_RE.sub('x', s)
    # Одиночный & (не являющийся частью сущности) — 1 символ, не трогаем
    # Telegram считает символы в UTF-16 code units (суррогатные пары = 2 единицы)
    # Эмодзи вне BMP (U+1F000+) занимают 2 единицы → корректируем
    extra = sum(1 for ch in s if ord(ch) > 0xFFFF)
    return len(s) + extra


def safe_trim_caption(caption: str, limit: int = 1024) -> str:
    """
    Обрезает caption по видимой длине, не ломая HTML.
    Используется ТОЛЬКО как последний резерв — вызывать после всех попыток
    пересобрать caption через _build().
    Гарантирует: visible_length(result) <= limit и все HTML-теги закрыты.
    """
    if visible_length(caption) <= limit:
        return caption

    # FIX 2026-05-21 #8 P1: если строка не влезает целиком, пропускаем её,
    # но продолжаем пытаться добавить следующие — иначе при длинном main_topic
    # теряются ВСЕ таймкоды, ссылки и хэштеги.
    lines = caption.split("\n")
    result_lines = []
    current_len = 0
    for line in lines:
        line_vlen = visible_length(line)
        sep = 1 if result_lines else 0
        if current_len + sep + line_vlen <= limit:
            result_lines.append(line)
            current_len += sep + line_vlen
            continue
        # Строка не влезает целиком
        if not result_lines:
            # Первая строка слишком длинная — обрезаем посимвольно без лома тегов
            buf, vis, i = "", 0, 0
            while i < len(line):
                if line[i] == '<':
                    end = line.find('>', i)
                    if end == -1:
                        break
                    buf += line[i:end + 1]
                    i = end + 1
                elif line[i] == '&':
                    end = line.find(';', i)
                    if end == -1 or end - i > 10:
                        if vis < limit:
                            buf += line[i]; vis += 1
                        i += 1
                    else:
                        entity = line[i:end + 1]
                        if vis < limit:
                            buf += entity; vis += 1
                        i = end + 1
                else:
                    # AUDIT R39: эмодзи вне BMP = 2 UTF-16 единицы (как в
                    # visible_length), иначе эмодзи-насыщенная строка обрезалась
                    # «по 1» и итог превышал 1024 → Telegram отклонял отправку.
                    _w = 2 if ord(line[i]) > 0xFFFF else 1
                    if vis + _w <= limit:
                        buf += line[i]; vis += _w
                    i += 1
            result_lines.append(buf)
            current_len = vis
            # НЕ break — продолжаем пытаться добавить следующие короткие строки
            continue
        # Не первая, и не влезает — пропускаем и пробуем следующую
        # (например, длинный таймкод пропускаем, но хэштеги ниже могут влезть)
        continue

    result = "\n".join(result_lines)

    # Закрываем незакрытые теги (простые парные теги Telegram HTML)
    _PAIRED = ["b", "i", "u", "s", "a", "tg-emoji", "code", "pre", "spoiler"]
    open_tags = []
    for m in re.finditer(r'<(/?)(\w[\w-]*)(?:\s[^>]*)?>',  result):
        closing, tag = m.group(1), m.group(2).lower()
        if tag not in _PAIRED:
            continue
        if closing:
            # Ищем тег в стеке (не только вершину) — на случай неправильной вложенности
            if tag in open_tags:
                # Убираем все теги от вершины до найденного включительно
                while open_tags and open_tags[-1] != tag:
                    open_tags.pop()
                if open_tags:
                    open_tags.pop()
        else:
            open_tags.append(tag)
    for tag in reversed(open_tags):
        result += f"</{tag}>"

    return result


def _trim_timestamps(timestamps_str: str, max_lines: int) -> str:
    """
    Умная обрезка таймкодов — сохраняет покрытие всего материала.
    Всегда сохраняет первый (0:00) и последний таймкод,
    остальные слоты распределяет равномерно по всей длительности.
    Результат покрывает материал от начала до конца, а не только первую половину.
    """
    lines = [l for l in timestamps_str.split("\n") if l.strip()]
    if len(lines) <= max_lines:
        return timestamps_str
    if max_lines <= 0:
        return ""
    if max_lines == 1:
        return lines[0]
    if max_lines == 2:
        return "\n".join([lines[0], lines[-1]])

    # Всегда первый и последний
    result = [lines[0]]
    middle_lines = lines[1:-1]
    remaining_slots = max_lines - 2

    if remaining_slots >= len(middle_lines):
        result.extend(middle_lines)
    else:
        # Равномерная выборка из середины
        step = len(middle_lines) / remaining_slots
        for i in range(remaining_slots):
            idx = int(i * step)
            result.append(middle_lines[idx])

    result.append(lines[-1])
    return "\n".join(result)


def get_caption_timestamp_limit(format_name: str) -> int:
    """Возвращает максимальное число строк таймкодов в caption по format.
    Поскольку полное описание отправляется отдельным сообщением (4096 символов),
    лимиты увеличены: qa — все вопросы, sermon/lecture — умное покрытие."""
    return {
        "sermon":     30,   # умная выборка равномерно по всей проповеди
        "lecture":    30,   # то же для лекций и семинаров
        "qa":         999,  # все вопросы без обрезки
        "interview":  25,
        "discussion": 25,
        "other":      20,
    }.get(format_name or "other", 20)




# ════════════════════════════════════════════════════════
# CONSPECT QUALITY PATCH: postprocess Telegraph nodes
# ════════════════════════════════════════════════════════

def _postprocess_telegraph_nodes(nodes: list) -> list:
    """Fix common Gemini generation bugs in Telegraph nodes:
    space before/after ⏱, double spaces, '. .', hyphen→dash in verses,
    Unicode spaces, bidi isolates, merge adjacent text nodes,
    space between <strong> and <a>⏱
    """
    def _fix_text(text):
        if not isinstance(text, str):
            return text
        # FIX (Python 3.12+): replacement-строка в re.sub НЕ понимает \uXXXX —
        # это вызывало re.PatternError 'bad escape \u at position 3' и КАСКАДНО
        # ломало публикацию Synopsis / StudyAnalysis / ReflectionApplication
        # (все три страницы Gemini генерировал успешно, но post-process падал).
        # Решение: lambda вместо raw-replacement, чтобы Python разрешал \u23f1
        # на этапе компиляции литерала, а не передавал в parser re.
        _CLOCK = '⏱'  # ⏱
        _ENDASH = '–'  # –
        # AUDIT R11: эмодзи-вариант ⏱️ (U+FE0F) ломал все ⏱-регэкспы ниже —
        # селектор не пробел и не цифра. Нормализуем к базовому символу.
        text = text.replace('⏱️', '⏱')
        # AUDIT R11: литеральный «\n» (бэкслеш+n из перекодированного JSON)
        # публиковался как текст. Настоящих переводов строк это не касается.
        text = re.sub(r'\s*\\n\s*', ' ', text)
        text = re.sub(r'(\S)⏱', lambda m: m.group(1) + ' ' + _CLOCK, text)
        text = re.sub(r'⏱(\S)',  lambda m: _CLOCK + ' ' + m.group(1), text)
        # Strip leading zero from minute in timestamps: 05:32 → 5:32
        text = re.sub(r'\b0(\d:\d{2})\b', r'\1', text)
        text = re.sub(r' {2,}', ' ', text)
        text = re.sub(r'\.\s+\.', '.', text)
        for ch in [' ', ' ', ' ']:
            text = text.replace(ch, ' ')
        for ch in ['​', '­']:
            text = text.replace(ch, '')
        text = re.sub(r'[⁦-⁩‪-‮‏]', '', text)  # bidi isolates + RTL mark; U+200E (LTR) preserved for RTL fix
        text = re.sub(r'(\d+:\d+)\s*[-–—]\s*(\d+)', lambda m: m.group(1) + _ENDASH + m.group(2), text)  # fix hyphen/en/em-dash in verse ranges
        # BUG-D: strip stray space before , or ) after Hebrew/RTL words (LTR mark removal leaves space)
        text = re.sub(r'\*\*\s+([,;)])', r'**\1', text)  # **word** , → **word**,
        # FIX 2026-05-25: NBSP (\xa0) нормализация — Gemini часто вставляет
        text = text.replace('\u00a0', ' ')  # NBSP → обычный пробел
        text = re.sub(r' {2,}', ' ', text)  # повторный проход после NBSP замены
        # FIX 2026-05-25: пробел перед пунктуацией
        text = re.sub(r' +([.,;:!?)])', r'\1', text)
        # FIX 2026-05-25: пробел после открывающей скобки перед bold
        text = re.sub(r'\(\s+\*\*', '(**', text)
        # FIX 2026-05-25: malformed hour timestamps: 1:0:00 → 1:00:00
        text = re.sub(r'(\d+):(\d):(\d{2})', lambda m: f"{m.group(1)}:{m.group(2).zfill(2)}:{m.group(3)}", text)
        # AUDIT R9 (иврит): U+200E (LTR mark) нужен ПОСЛЕ иврита, чтобы тире/
        # латиница дальше не зеркалились, но окружённый пробелами он рисует
        # видимую дыру «** ‎ —». Прижимаем маркер к предыдущему символу.
        text = re.sub(r'[ \t]+\u200e', '\u200e', text)
        text = re.sub(r'\u200e{2,}', '\u200e', text)
        # ПРАВИЛО ПРОЕКТА (AGENTS.md, оператор 2026-07-05): ⏱-таймкод стоит
        # ДО точки. R12: общий хелпер, тот же что на markdown-стадии —
        # здесь это второй рубеж для строк, не прошедших через section-путь.
        text = _fix_ts_period_order(text)
        # FIX 2026-05-25: квадратные скобки — запрещены промптом, Gemini иногда вставляет
        # НО: не трогаем [N/M] навигацию и Markdown-ссылки [text](url)
        # FIX AUDIT R4: комментарий обещал не трогать [N/M], а регэксп трогал —
        # навигация multipart-страниц публиковалась как «Назад: 1/3» без скобок.
        text = re.sub(r'\[(?!\d+\s*/\s*\d+\])([^\]]*?)\](?!\()', r'\1', text)  # [text] → text
        if "⚠️" in text and re.search(r"заблуж|ложн|ошиб|ерес|отриц", text, flags=re.IGNORECASE):
            text = text.replace("⚠️", "❌")
        # spacing polish for Gemini/Markdown glue in source cards and Study bullets
        text = re.sub(r"(^|\n)-(?=\S)", r"\1- ", text)
        text = re.sub(r"(^|\n)-\*\s+\*", r"\1- **", text)
        text = re.sub(r"([а-яё])([A-Z][A-Za-z])", r"\1 \2", text)
        text = re.sub(r"([а-яё])«", r"\1 «", text)
        text = re.sub(r"(?<!\d)([.!?])([А-ЯЁA-Z])", r"\1 \2", text)
        text = re.sub(r"(:\d{1,3})\.([А-ЯЁA-Z])", r"\1. \2", text)
        # AUDIT R17 (дамп 2026-07-09, «Диагностика сердца»): Gemini иногда
        # приклеивает «:» сразу после «?» в конце вопроса-триггера («...
        # одобрение?:»), путая формат жирного вопроса с form-меткой
        # «Заголовок:\n\nТекст». После «?» двоеточие никогда не нужно.
        text = re.sub(r"\?:(?=\s*$)", "?", text, flags=re.MULTILINE)
        # AUDIT R14 (дамп 2026-07-09): «…их следствием. возрождение…» — модель
        # начала новое предложение со строчного термина. Капитализируем букву
        # после «. » ТОЛЬКО если предыдущее слово ≥5 строчных букв (реальное
        # окончание предложения, а не сокращения «т.д.», «см.», «напр.»,
        # «стр.»). НЕ трогаем markdown-маркеры и цифры.
        text = re.sub(
            r"(?<![*_])([а-яё]{5,})\. ([а-яё])",
            lambda m: f"{m.group(1)}. {m.group(2).upper()}",
            text,
        )
        # Prefer visually cleaner error labels: cross stays in the middle, not at line end.
        text = re.sub(
            r"\*\*([^*\n]{3,120}?)\*\*\s*❌\s*\*\*([^*\n]{3,120}?)\s*\.\*\*",
            lambda m: f"**{m.group(1).strip()}** ❌ **Подмена: {m.group(2).strip().rstrip('.')}.**",
            text,
        )
        text = re.sub(r",\s*\*", ", *", text)
        # Preserve paragraph boundaries from Gemini pseudo-separators; guard URLs (https://).
        text = re.sub(r"(?<!:)\s+/\s*/\s+", "\n\n", text)
        # На уровне отдельных inline-фрагментов нельзя гонять source_map/person-name
        # pass: строка внутри parenthetical может быть просто "John MacArthur",
        # и тогда она ошибочно превращается в "Джон МакАртур". Source-card
        # нормализуется выше на цельной строке; здесь оставляем только typos.
        text = normalize_common_typos(text, source_map=False)
        text = re.sub(r"\bДанный\s+академический\s+труд\b", "Академический труд", text, flags=re.IGNORECASE)
        text = re.sub(r"\bДанный\s+труд\b", "Этот труд", text, flags=re.IGNORECASE)
        text = re.sub(r"\bДанная\s+книга\b", "Эта книга", text, flags=re.IGNORECASE)
        text = re.sub(r"Историко\s+[—-]\s+грамматическ", "Историко-грамматическ", text)
        text = re.sub(r"Лексико\s+[—-]\s+семантическ", "Лексико-семантическ", text)
        text = scrub_third_person_phrases(text)
        return text

    def _ends_no_space(n):
        if isinstance(n, str):
            return bool(n) and not n.endswith(" ")
        if isinstance(n, dict):
            ch = n.get("children", [])
            return _ends_no_space(ch[-1]) if ch else False
        return False

    def _walk(nodes_in):
        result = []
        for node in nodes_in:
            if isinstance(node, str):
                fixed = _fix_text(node)
                if result and isinstance(result[-1], str):
                    result[-1] += fixed
                else:
                    result.append(fixed)
            elif isinstance(node, dict):
                nn = dict(node)
                if "children" in nn:
                    nn["children"] = _walk(nn["children"])
                    patched = []
                    for j, child in enumerate(nn["children"]):
                        if (j > 0 and isinstance(child, dict)
                                and child.get("tag") == "a"
                                and child.get("children")
                                and isinstance(child["children"][0], str)
                                and child["children"][0].lstrip().startswith("\u23f1")):
                            prev = patched[-1] if patched else None
                            if prev is not None and _ends_no_space(prev):
                                patched.append(" ")
                        patched.append(child)
                    nn["children"] = patched
                result.append(nn)
            else:
                result.append(node)
        return result

    def _is_emoji_stub_node(node) -> bool:
        """True if node is <p><b>EMOJI.</b></p> or <p>EMOJI.</p> — useless stub from Gemini section labels."""
        import unicodedata
        if not isinstance(node, dict) or node.get('tag') != 'p':
            return False
        ch = node.get('children', [])
        if not ch:
            return False

        def _emoji_only(text: str) -> bool:
            """True if string contains only emoji/symbols (+ optional dot/space)."""
            t = text.strip().rstrip('.')
            if not t:
                return False
            return all(
                unicodedata.category(c) in ('So', 'Mn', 'Cf', 'Co') or
                ord(c) > 0x1F000 or ord(c) in range(0x2600, 0x2700) or
                ord(c) in range(0x2300, 0x2400)
                for c in t if c.strip()
            )

        # Case 1: <p><b>EMOJI.</b></p>
        if (len(ch) == 1 and isinstance(ch[0], dict) and ch[0].get('tag') == 'b'):
            b_children = ch[0].get('children', [])
            text = ''.join(c if isinstance(c, str) else '' for c in b_children)
            return _emoji_only(text)

        # Case 2: <p>EMOJI.</p> — plain text, no bold
        if all(isinstance(c, str) for c in ch):
            text = ''.join(ch)
            return _emoji_only(text)

        return False

    def _flatten_text(node) -> str:
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            return ''.join(_flatten_text(c) for c in node.get('children', []))
        return ''

    def _normalize_paragraph_node(node):
        """Post-fixes for common Gemini Telegraph regressions:
        1) strip section-leading emoji labels like `🔑 ` / `📖 ` / `🗣️ `
        2) de-bold bare list markers like `**•**` / `**-**`
        3) make standalone question paragraphs visually explicit in diagnostics sections
        """
        if not isinstance(node, dict) or node.get('tag') != 'p':
            return node
        children = list(node.get('children', []))
        if not children:
            return node

        def _strip_leading_icon_prefix(ch):
            icon_re = re.compile(r'^\s*[🔑📖🏛️⚖️📚📜🛡️🗣️🧭🗝️]+(?:️)?\s*')
            out = list(ch)
            while out and isinstance(out[0], str):
                new0 = icon_re.sub('', out[0])
                if new0 != out[0]:
                    if new0:
                        out[0] = new0
                        break
                    out = out[1:]
                    continue
                break
            return out

        def _deb0ld_marker(ch):
            if not ch or not isinstance(ch[0], dict) or ch[0].get('tag') not in ('b', 'strong'):
                return ch
            raw = ''.join(_flatten_text(c) for c in ch[0].get('children', [])).strip()
            raw = raw.replace('\\-', '-').strip()
            if raw in {'•', '-', '–', '—'}:
                return ['• '] + ch[1:]
            return ch

        children = _strip_leading_icon_prefix(children)
        children = _deb0ld_marker(children)

        def _fix_split_hyphenated_bold_term(ch):
            """Repair old pages where one hyphenated term was split as
            <b>Историко</b> + " — грамматический".
            """
            out = list(ch)
            for i in range(len(out) - 1):
                cur, nxt = out[i], out[i + 1]
                if not (isinstance(cur, dict) and cur.get('tag') in ('b', 'strong') and isinstance(nxt, str)):
                    continue
                head = _flatten_text(cur).strip()
                mapping = {
                    'Историко': 'грамматический',
                    'историко': 'грамматический',
                    'Лексико': 'семантический',
                    'лексико': 'семантический',
                }
                tail_word = mapping.get(head)
                if not tail_word:
                    continue
                m = re.match(rf'^\s+[—-]\s+({tail_word}\w*)', nxt, flags=re.IGNORECASE)
                if not m:
                    continue
                nn = dict(cur)
                nn['children'] = [head + '-' + m.group(1)]
                out[i] = nn
                out[i + 1] = nxt[m.end():]
            return [x for x in out if not (isinstance(x, str) and x == '')]

        def _trim_inline_spacing(ch):
            """Normalize spaces across inline Telegraph children.

            Fixes visible glitches such as:
              <b>Term. </b> + " Text"  → <b>Term.</b> + " Text"
              <i> (1 Кор 4:13)</i>       → <i>(1 Кор 4:13)</i>
            without touching semantic spaces inside normal text.
            """
            def _trim_node_edges(n):
                if isinstance(n, dict) and n.get('tag') in ('b', 'strong', 'i', 'em'):
                    nn = dict(n)
                    cc = list(nn.get('children', []))
                    if cc and isinstance(cc[0], str):
                        cc[0] = cc[0].lstrip()
                    if cc and isinstance(cc[-1], str):
                        cc[-1] = cc[-1].rstrip()
                    # Verse ranges inside bold refs: 1:26 – 28 → 1:26–28
                    cc = [
                        re.sub(r'(\d+:\d+)\s*[-–—]\s*(\d+)', r'\1–\2', x)
                        if isinstance(x, str) else x
                        for x in cc
                    ]
                    nn['children'] = cc
                    return nn
                if isinstance(n, str):
                    return re.sub(r'(\d+:\d+)\s*[-–—]\s*(\d+)', r'\1–\2', n)
                return n

            out = []
            for _xi, x in enumerate(ch):
                trimmed = _trim_node_edges(x)
                # VISUAL FIX 2026-06-10: если у b/i срезали хвостовой пробел,
                # а следующий элемент — текст без ведущего пробела/пунктуации,
                # возвращаем пробел наружу (иначе «1.Введение», «4:34:«цитата»).
                if (isinstance(x, dict) and isinstance(trimmed, dict)
                        and x.get('tag') in ('b', 'strong', 'i', 'em')):
                    _old_cc = x.get('children', [])
                    _had_trail = bool(_old_cc and isinstance(_old_cc[-1], str)
                                      and _old_cc[-1] != _old_cc[-1].rstrip())
                    if _had_trail and _xi + 1 < len(ch):
                        _nxt = ch[_xi + 1]
                        _nxt_txt = _nxt if isinstance(_nxt, str) else _flatten_text(_nxt)
                        if _nxt_txt and not _nxt_txt[0] in ' .,;:!?)»':
                            out.append(trimmed)
                            out.append(' ')
                            continue
                out.append(trimmed)
            compact = []
            for item in out:
                if isinstance(item, str) and compact:
                    prev = compact[-1]
                    prev_text = _flatten_text(prev) if isinstance(prev, dict) else str(prev)
                    if prev_text.endswith(' ') and item.startswith(' ') and item != ' ':
                        item = item.lstrip()
                compact.append(item)
            return compact

        children = _trim_inline_spacing(children)
        children = _fix_split_hyphenated_bold_term(children)
        children = _trim_inline_spacing(children)

        def _normalize_quote_ref_bullet(ch):
            """Turn quote-first bullets into canonical scripture micro-blocks.

            Gemini sometimes emits:
              • «Как прах...» *(1 Кор 4:13)*.
              • **«Безумное мира» *(1 Кор 1:27)*.**
            Canonical form is:
              • **1 Кор 4:13:** *«Как прах...»*
            """
            if not ch:
                return ch
            first = ch[0] if isinstance(ch[0], str) else ''
            if not first.strip().startswith('•'):
                return ch

            def _mk(ref, quote):
                ref = ref.strip().strip('()').rstrip('.:')
                quote = quote.strip().strip('*_').strip()
                quote = quote.rstrip('.')
                if not quote.startswith('«'):
                    quote = '«' + quote.lstrip('"“')
                if not quote.endswith('»'):
                    quote = quote.rstrip('"”') + '»'
                return ['• ', {'tag': 'b', 'children': [f'{ref}:']}, ' ', {'tag': 'i', 'children': [quote]}]

            # Case A: plain quote + italic ref. After _walk(), the marker and
            # quote may be either separate (`['• ', '«quote» ', <i>ref</i>]`)
            # or merged into one string (`['• «quote» ', <i>ref</i>]`).
            quote_idx = 1 if len(ch) >= 3 and isinstance(ch[1], str) and '«' in ch[1] else None
            italic_idx = 2
            if quote_idx is None and len(ch) >= 2 and isinstance(ch[0], str) and '«' in ch[0]:
                quote_idx = 0
                italic_idx = 1
            if quote_idx is not None and len(ch) > italic_idx:
                italic = ch[italic_idx]
                if isinstance(italic, dict) and italic.get('tag') in ('i', 'em'):
                    ref = _flatten_text(italic).strip()
                    if re.match(r'^\(?\s*(?:[1-3]\s*)?[А-ЯЁA-Z][^)]*\d+:\d+', ref):
                        quote = ch[quote_idx].replace('•', '', 1).strip()
                        return _mk(ref, quote)

            # Case B: whole quote+ref accidentally bolded
            if len(ch) >= 2 and isinstance(ch[1], dict) and ch[1].get('tag') in ('b', 'strong'):
                btxt = _flatten_text(ch[1]).strip()
                m = re.search(r'([«"](.+?)[»"])\s*\(?\s*((?:[1-3]\s*)?[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z.\s]*\d+:\d+(?:[–—-]\d+)?)\s*\)?', btxt)
                if m:
                    return _mk(m.group(3), m.group(1))
            return ch

        children = _normalize_quote_ref_bullet(children)

        # FIX 2026-05-25: fix "( **Term**)" → "(**Term**)" spacing in term definitions
        def _fix_paren_bold_space(ch):
            """Fix '( **' → '(**' in text nodes before bold children."""
            out = list(ch)
            for i in range(len(out)):
                if isinstance(out[i], str) and out[i].rstrip().endswith('('):
                    # Next child is bold? Check if we need to remove trailing space
                    if i + 1 < len(out) and isinstance(out[i+1], dict) and out[i+1].get('tag') in ('b', 'strong'):
                        out[i] = out[i].rstrip() + '('
                        out[i] = out[i].replace('( (', '(')  # avoid double
                        if out[i].endswith('(('):
                            out[i] = out[i][:-1]
            return out

        children = _fix_paren_bold_space(children)
        children = _trim_inline_spacing(children)

        # FIX 2026-05-25: de-bold source cards in "Карта источников"
        # Pattern: <p>• <b>Author, <em>Title</em>...</b></p> → unwrap <b>
        def _debold_source_card(ch):
            """Unwrap bold from source card entries like • **Author, *Title*...**"""
            if len(ch) < 2:
                return ch
            # Check: first child is "• " text, second is <b> with comma inside
            first_text = ch[0] if isinstance(ch[0], str) else ""
            if not first_text.strip().startswith("•"):
                return ch
            second = ch[1] if len(ch) > 1 else None
            if not isinstance(second, dict) or second.get("tag") not in ("b", "strong"):
                return ch
            # Check if <b> contains a comma (Author, Title pattern)
            b_text = ""
            for bc in second.get("children", []):
                if isinstance(bc, str):
                    b_text += bc
                elif isinstance(bc, dict):
                    b_text += "".join(str(x) for x in bc.get("children", []))
            if "," not in b_text:
                return ch
            # Has italic inside? (book title)
            has_italic = any(
                isinstance(bc, dict) and bc.get("tag") in ("em", "i")
                for bc in second.get("children", [])
            )
            if not has_italic:
                return ch
            # Unwrap: replace <b>...</b> with its children
            out = [ch[0]] + list(second.get("children", [])) + list(ch[2:])
            return out

        children = _debold_source_card(children)
        children = _trim_inline_spacing(children)

        # V3-P21: source-map cards — prefer original titles and dedupe bilingual authors
        # even when Gemini did not use italic/quote markup, e.g.
        # • Джон МакАртур, Странный огонь (John MacArthur, Strange Fire).
        _plain_before_source_norm = _flatten_text({'children': children}).strip()
        _source_norm = normalize_source_map_text(_plain_before_source_norm)
        if _plain_before_source_norm.startswith(('•', '-')):
            from core.source_titles import _ensure_source_title_bold
            _source_norm = _ensure_source_title_bold(_source_norm)
            # FIX: Only replace children with flat text if the node has NO
            # existing <b>/<strong>/<em> tags that would be destroyed.
            # The old code flattened ALL bullets, losing bold/italic formatting
            # that _section_to_nodes_v2 had already correctly built from blocks.
            _has_inline_tags = any(
                isinstance(c, dict) and c.get('tag') in ('b', 'strong', 'em', 'i', 'a')
                for c in children
            )
            _has_links = any(isinstance(c, dict) and c.get('tag') == 'a' for c in children)
            _has_parenthetical_original = bool(re.search(r"\([^)]*[A-Za-z]{3,}[^)]*\)", _plain_before_source_norm))
            if _source_norm != _plain_before_source_norm and (not _has_inline_tags or (_has_parenthetical_original and not _has_links)):
                # For repair of already-published pages, source cards may contain
                # existing <strong> around the invented Russian title. In that
                # exact source-card shape it is safer to flatten and re-parse the
                # canonical markdown than to preserve stale inline tags.
                children = _md_parse_inline(_source_norm)

        # Existing Telegraph pages may already contain raw Markdown markers in
        # plain text nodes (e.g. "• **term**"), because old renderers saved them
        # as text. Repair/postprocess must parse those markers into Telegraph
        # inline nodes instead of only normalizing the string.
        _plain_for_md_parse = _flatten_text({'children': children})
        _has_inline_tags_after = any(
            isinstance(c, dict) and c.get('tag') in ('b', 'strong', 'em', 'i', 'a', 'code')
            for c in children
        )
        if (not _has_inline_tags_after
                and ('**' in _plain_for_md_parse or re.search(r'(?<!\*)\*[^*\n]{2,160}\*(?!\*)', _plain_for_md_parse))):
            children = _md_parse_inline(_plain_for_md_parse)
            children = _trim_inline_spacing(children)

        node['children'] = children

        plain = _flatten_text(node).strip()
        has_links = any(isinstance(c, dict) and c.get('tag') == 'a' for c in children)
        has_bold = any(isinstance(c, dict) and c.get('tag') in ('b', 'strong') for c in children)
        if (plain.endswith('?') and not has_links and not has_bold and 12 <= len(plain) <= 180
                and plain.count('?') == 1 and plain.count('.') <= 1 and plain.count('!') == 0):
            node['children'] = [{'tag': 'b', 'children': children}]
        return node

    def _split_inline_bullet_paragraph_node(node):
        """Split paragraphs where Gemini glued several bullet cards into one <p>.

        Live example: `• **A** — text. • **B** — text. • **C** — text.`
        Telegraph renders that as one unreadable paragraph. We split only on a
        whitespace-prefixed bullet marker, preserving inline children.
        """
        if not isinstance(node, dict) or node.get('tag') != 'p':
            return [node]
        children = list(node.get('children', []))
        flat = _flatten_text({'children': children})
        if flat.count('•') < 2 or ' • ' not in flat:
            return [node]

        paragraphs: list[list] = []
        current: list = []

        def _flush():
            nonlocal current
            # Trim empty text edges, but keep semantic spaces inside.
            while current and isinstance(current[0], str) and not current[0].strip():
                current.pop(0)
            while current and isinstance(current[-1], str) and not current[-1].strip():
                current.pop()
            if current:
                paragraphs.append(current)
            current = []

        for child in children:
            if not isinstance(child, str) or ' • ' not in child:
                current.append(child)
                continue
            parts = re.split(r'(\s+•\s+)', child)
            for part in parts:
                if not part:
                    continue
                if re.fullmatch(r'\s+•\s+', part):
                    _flush()
                    current.append('• ')
                else:
                    current.append(part)
        _flush()

        if len(paragraphs) <= 1:
            return [node]
        return [{'tag': 'p', 'children': para} for para in paragraphs]

    def _split_scripture_explanation_node(node):
        """Split `• **Ref:** *«quote»* Explanation...` into two paragraphs.

        In the Study page section «Ключевые тексты» the ref+quote should be the
        first line, and the explanation should start below as its own paragraph.
        This is a formatting-only fix; it preserves links and inline nodes.
        """
        if not isinstance(node, dict) or node.get('tag') != 'p':
            return [node]
        ch = list(node.get('children', []))
        if len(ch) < 4:
            return [node]
        if not (isinstance(ch[0], str) and ch[0].strip().startswith('•')):
            return [node]
        bold_idx = next((i for i, c in enumerate(ch[:3])
                         if isinstance(c, dict) and c.get('tag') in ('b', 'strong')), None)
        if bold_idx is None:
            return [node]
        btxt = _flatten_text(ch[bold_idx]).strip()
        if not re.search(r'\b(?:[1-3]\s*)?[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z.\s]+\d+:\d+', btxt):
            return [node]
        if ':' not in btxt:
            return [node]
        italic_idx = None
        for i in range(bold_idx + 1, min(len(ch), bold_idx + 5)):
            if isinstance(ch[i], dict) and ch[i].get('tag') in ('i', 'em'):
                itxt = _flatten_text(ch[i]).strip()
                if itxt.startswith('«') or itxt.startswith('"') or itxt.startswith('“'):
                    italic_idx = i
                    break
        if italic_idx is None or italic_idx >= len(ch) - 1:
            return [node]

        head = ch[:italic_idx + 1]
        tail = ch[italic_idx + 1:]
        # Trim whitespace-only separators and stray punctuation after the quote.
        # Gemini often writes `*«quote»*.`; that dot must not become a separate
        # paragraph after splitting the scripture micro-block.
        while tail and isinstance(tail[0], str) and not tail[0].strip():
            tail.pop(0)
        if tail and isinstance(tail[0], str):
            tail[0] = tail[0].lstrip()
            tail[0] = re.sub(r'^[\s.。]+', '', tail[0]).lstrip()
            if tail[0] == "":
                tail.pop(0)
        while tail and isinstance(tail[0], str) and not tail[0].strip():
            tail.pop(0)

        new_head = dict(node)
        new_head['children'] = head
        if not ''.join(_flatten_text(x) for x in tail).strip():
            return [new_head]
        new_tail = {'tag': 'p', 'children': tail}
        return [new_head, new_tail]

    walked = _walk(nodes)
    cleaned = [n for n in walked if not _is_emoji_stub_node(n)]
    normalized = [_normalize_paragraph_node(n) for n in cleaned]
    split = []
    for n in normalized:
        for sn in _split_scripture_explanation_node(n):
            split.extend(_split_inline_bullet_paragraph_node(sn))

    # AUDIT R18 (дамп 2026-07-09, повторный прогон «Что такое Евангелие?»):
    # когда Gemini не заполнил structured "application"-блок (challenge/
    # anchor_timestamp/concrete_step) и вместо этого написал этот же паттерн
    # обычным content-текстом, заголовок-триггер перед "Шаг: ..." остаётся
    # НЕ жирным — в отличие от _structured_blocks_to_nodes_v2, который
    # гарантированно оборачивает challenge в **. Речь ровно про эту пару
    # абзацев (контракт "application"-блока: bold-триггер + "Шаг:"),
    # поэтому дожимаем формат здесь же, постфактум.
    def _dedup_adjacent_timestamp_link(children: list) -> list:
        """Тот же баг иногда дублирует один inline-таймкод дважды подряд
        ("... комфортной ⏱ 41:14. ⏱ 41:14") — схлопываем повтор."""
        out = list(children)
        i = 0
        while i < len(out) - 1:
            cur = out[i]
            j = i + 1
            while j < len(out) and isinstance(out[j], str) and not out[j].strip().strip('.'):
                j += 1
            if (isinstance(cur, dict) and cur.get('tag') == 'a'
                    and j < len(out) and isinstance(out[j], dict) and out[j].get('tag') == 'a'
                    and cur.get('attrs') == out[j].get('attrs')
                    and cur.get('children') == out[j].get('children')):
                del out[i + 1:j + 1]
                continue
            i += 1
        return out

    for _idx in range(1, len(split)):
        _cur = split[_idx]
        if not (isinstance(_cur, dict) and _cur.get('tag') == 'p'):
            continue
        if not _flatten_text(_cur).strip().startswith('Шаг:'):
            continue
        _prev = split[_idx - 1]
        if not (isinstance(_prev, dict) and _prev.get('tag') == 'p'):
            continue
        _prev_children = _prev.get('children') or []
        if _prev_children and isinstance(_prev_children[0], dict) and _prev_children[0].get('tag') in ('b', 'strong'):
            continue  # уже жирный — контракт application-блока соблюдён
        _prev_children = _dedup_adjacent_timestamp_link(_prev_children)
        split[_idx - 1] = {**_prev, 'children': [{'tag': 'b', 'children': _prev_children}]}

    return split
