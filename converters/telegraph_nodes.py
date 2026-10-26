#!/usr/bin/env python3
"""Markdown-to-Telegraph conversion and Telegraph-node cleaning helpers."""

import re

from core.core_utils import (
    _fix_rtl_in_text,
    _md_parse_inline,
    _polish_timestamps_in_text,
    normalize_misbolded_bullet_lead,
    unescape_markdown_markers,
)
from core.text_utils import BAD_META_PATTERNS, _scrub_inline, is_meta_garbage

_HEADING_BOLD_STRIP_RE = re.compile(r"^\*\*(?!\*)(.*?)(?<!\*)\*\*$")


def _demote_paragraph_bold(line: str) -> str:
    """DEEP-QUALITY FIX [D]: если в строке **bold** покрывает >70% символов —
    это явно жирный АБЗАЦ (плохо визуально). Понижаем: убираем **, оставляя текст обычным.
    Если жирное короткое (<50% строки) — оставляем как есть, это нормальный акцент.

    FIX 2026-05-24: MIN_LINE_LEN=80 — короткие строки (<80 символов)
    целиком bold — это осознанные акценты (тезисы, поворотные фразы).
    FIX 2026-05-25: per-block check — проверяем каждый bold-кусок по отдельности,
    а не всю строку. Demote только если ОДИН кусок > 120 символов.
    """
    if not line or "**" not in line:
        return line
    if "||" in line:
        # Legacy terms_data / source separators: не трогаем форматные разделители.
        return line
    # Считаем длину жирных кусков
    bold_matches = re.findall(r"\*\*([^*\n]+)\*\*", line)
    if not bold_matches:
        return line
    bold_len = sum(len(b) for b in bold_matches)
    total_len = len(re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line))
    if total_len == 0:
        return line
    bold_ratio = bold_len / total_len
    # FIX: bullet definition items like "• **Term — definition**, explanation"
    # have high bold_ratio but this is intentional formatting, not a "bold paragraph".
    # Don't demote if the line starts with a bullet marker + bold.
    if line.lstrip().startswith(("• **", "- **")):
        return line
    # FIX 2026-05-24: короткие строки (<80 символов) —
    # осознанные акценты, не трогаем.
    MIN_LINE_LEN = 80
    if total_len < MIN_LINE_LEN:
        return line
    # FIX 2026-05-25: per-block — если есть хотя бы один ОЧЕНЬ длинный bold-кусок
    # (>120 символов), снимаем bold только с НЕГО. Короткие не трогаем.
    MAX_SINGLE_BOLD = 120
    has_oversized = any(len(b) > MAX_SINGLE_BOLD for b in bold_matches)
    if has_oversized:

        def _demote_if_long(m):
            inner = m.group(1)
            if len(inner) > MAX_SINGLE_BOLD:
                return inner
            return m.group(0)

        return re.sub(r"\*\*([^*\n]+)\*\*", _demote_if_long, line)

    # Общий ratio check — если вся строка >70% жирная, снимаем всё
    if bold_ratio > 0.7:
        return re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
    return line


def _md_to_telegraph_nodes(md: str) -> list:
    """Полный Markdown → Telegraph nodes."""
    nodes = []
    prev_was_empty = False
    for line in md.split("\n"):
        s = line.strip()
        # Gemini sometimes escapes Markdown markers (`\*\*bold\*\*`) or emits
        # list items as `.**Text**` / `•.**Text**`. Normalize before parsing.
        s = unescape_markdown_markers(s)
        s = re.sub(r"^(?:[.•]\s*)+\*\*", "• **", s)
        s = re.sub(r"^(\s*[•\-]\s*)\*\*\s*[•\-]\s*", r"\1**", s)
        s = normalize_misbolded_bullet_lead(s)
        if not s:
            prev_was_empty = True
            continue
        # Пропускаем строки-мусор (Cocoon AI Summary и т.п.)
        if any(re.search(p, s, re.IGNORECASE) for p in BAD_META_PATTERNS):
            continue
        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue
        # DEEP-QUALITY FIX [D]: понижаем жирные абзацы (>70% жирного → обычный текст)
        s = _demote_paragraph_bold(s)
        # FINAL-POLISH FIX 2-4: чистим слипания таймкодов и точек
        s = _polish_timestamps_in_text(s)
        # v10b FIX: --- / *** / ___ → <hr> до фильтра мусора
        # (fullmatch r'[.\-...]' поглощал их раньше)
        if s in ("---", "***", "___"):
            nodes.append({"tag": "hr"})
            prev_was_empty = False
            continue
        # Пропускаем строки из одних знаков препинания/разделителей (. .. ─── и т.п.)
        # Gemini иногда генерирует одиночную точку как визуальный разделитель между блоками
        if re.fullmatch(r"[.\-–—─·•*_~\s]+", s):
            prev_was_empty = True
            continue
        # Добавляем визуальный отступ только после заголовков — между обычными <p>
        # Telegraph сам даёт отступ, лишний <br> даёт двойной пробел
        last_tag = nodes[-1].get("tag") if nodes else ""
        if prev_was_empty and nodes and last_tag in ("h3", "h4") and not s.startswith(("#", "---", "***", "___")):
            nodes.append({"tag": "p", "children": [{"tag": "br"}]})
        prev_was_empty = False
        if s.startswith("#### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r"\1", s[5:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s.startswith("### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r"\1", s[4:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s.startswith("## "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r"\1", s[3:])
            nodes.append({"tag": "h3", "children": [_h_text]})
        elif s.startswith("# "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r"\1", s[2:])
            nodes.append({"tag": "h3", "children": [_h_text]})
        # FIX 2026-05-21 #7: ##### и ###### → h4 (вместо <p>)
        elif s.startswith("###### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r"\1", s[7:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s.startswith("##### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r"\1", s[6:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s in ("---", "***", "___"):
            nodes.append({"tag": "hr"})
        elif s.startswith("> "):
            _bq_text = s[2:]
            _bq_has_rtl = bool(re.search(r"[\u0590-\u05FF\u0600-\u06FF]", _bq_text))
            if _bq_has_rtl:
                _bq_text = _fix_rtl_in_text(_bq_text)
            _bq_node = {"tag": "blockquote", "children": _md_parse_inline(_bq_text)}
            if _bq_has_rtl:
                _bq_node["attrs"] = {"dir": "ltr"}
            nodes.append(_bq_node)
        elif re.match(r"^\d+\.\s", s):
            num_m = re.match(r"^(\d+)\.\s+(.*)", s)
            if num_m:
                _num, _rest = num_m.group(1), num_m.group(2)
                _rest_stripped = _rest.lstrip()
                # Если _rest — только жирный заголовок (**Title.**) без текста после —
                # вливаем номер прямо внутрь <b>, чтобы получился один тег: <b>1. Title.</b>
                # Иначе два соседних <b> рендерятся в Telegraph непоследовательно.
                _only_bold = re.match(r"^\*\*([^*]+)\*\*\.?$", _rest_stripped)
                if _only_bold:
                    _title = _rest_stripped[2:].rstrip("*").rstrip(".")
                    _ob_node = {"tag": "p", "children": [{"tag": "b", "children": [f"{_num}. {_title}."]}]}
                    if re.search(r"[֐-׿؀-ۿ]", _title):
                        _ob_node["attrs"] = {"dir": "ltr"}
                    nodes.append(_ob_node)
                elif _rest_stripped.startswith("**"):
                    # Заголовок + текст: сливаем номер в первый <b>
                    _inline = _md_parse_inline(_rest_stripped)
                    _inline_has_rtl = bool(re.search(r"[\u0590-\u05FF\u0600-\u06FF]", _rest_stripped))
                    if _inline and isinstance(_inline[0], dict) and _inline[0].get("tag") == "b":
                        _inline[0]["children"] = [f"{_num}. "] + _inline[0]["children"]
                        _p_inl = {"tag": "p", "children": _inline}
                        if _inline_has_rtl:
                            _p_inl["attrs"] = {"dir": "ltr"}
                        nodes.append(_p_inl)
                    else:
                        _p_inl2 = {"tag": "p", "children": [{"tag": "b", "children": [f"{_num}. "]}] + _inline}
                        if _inline_has_rtl:
                            _p_inl2["attrs"] = {"dir": "ltr"}
                        nodes.append(_p_inl2)
                else:
                    _num_has_rtl = bool(re.search(r"[\u0590-\u05FF\u0600-\u06FF]", _rest))
                    if _num_has_rtl:
                        _rest = _fix_rtl_in_text(_rest)
                    _num_node = {
                        "tag": "p",
                        "children": [{"tag": "b", "children": [f"{_num}.\u00a0"]}] + _md_parse_inline(_rest),
                    }
                    if _num_has_rtl:
                        _num_node["attrs"] = {"dir": "ltr"}
                    nodes.append(_num_node)
            else:
                nodes.append({"tag": "p", "children": _md_parse_inline(re.sub(r"^\d+\.\s", "", s))})
        elif s.startswith(("- ", "• ", "· ")) or (
            s.startswith("* ")
            and not s.endswith("*/")
            and not (re.search(r"(?<!\*)\*\s*$", s) and not re.search(r"\*\*\s*$", s))
        ):  # "* " как список; исключаем CSS-комментарии и курсив-обёртки "* весь текст *"
            _li_text = s[2:]
            _li_has_rtl = bool(re.search(r"[\u0590-\u05FF\u0600-\u06FF]", _li_text))
            if _li_has_rtl:
                _li_text = _fix_rtl_in_text(_li_text)
            _li_node = {"tag": "p", "children": ["• "] + _md_parse_inline(_li_text)}
            if _li_has_rtl:
                _li_node["attrs"] = {"dir": "ltr"}
            nodes.append(_li_node)
        else:
            # Обычный абзац — сохраняем как единый <p>, не дробим на предложения.
            # dir="ltr" для строк содержащих RTL-символы (иврит, арабский) —
            # иначе браузер переключает выравнивание всего абзаца вправо.
            _has_rtl = bool(re.search(r"[\u0590-\u05FF\u0600-\u06FF]", s))
            _s_for_parse = _fix_rtl_in_text(s) if _has_rtl else s
            _p_children = _md_parse_inline(_s_for_parse)
            # v10b RTL FIX: если первый child — dict (bold/italic) с ивритом,
            # браузер BiDi считает весь абзац RTL и «улетает» вправо.
            # Вставляем LTR Mark (U+200E) первым символом → абзац остаётся LTR.
            # Telegraph strips dir="ltr" attr, поэтому только текстовый якорь работает.
            if (
                _has_rtl
                and _p_children
                and isinstance(_p_children[0], dict)
                and _p_children[0].get("tag") in ("b", "i")
            ):
                _p_children = ["\u200e"] + _p_children
            _p_node = {"tag": "p", "children": _p_children}
            if _has_rtl:
                _p_node["attrs"] = {"dir": "ltr"}
            nodes.append(_p_node)
    return nodes or [{"tag": "p", "children": [md]}]


def _clean_text_block(text: str) -> str:
    """Безопасная очистка текстового блока.
    Удаляет блок целиком ТОЛЬКО если он является 100% мусором (Cocoon AI и т.д.).
    НЕ редактирует нормальный текст, НЕ удаляет внутренние пробелы и переносы строк."""
    if not text:
        return ""
    if any(re.search(p, text, re.IGNORECASE) for p in BAD_META_PATTERNS):
        return ""
    return text


def _clean_node_texts(node):
    """Рекурсивно убирает None и пустые children из Telegraph nodes.
    НЕ редактирует текстовое содержимое — чтобы не ломать форматирование
    богословских статей, scripture, lexicon notes и narrative content."""
    if not isinstance(node, dict):
        return node
    children = node.get("children")
    if not isinstance(children, list):
        return node
    new_children = []
    for child in children:
        if isinstance(child, str):
            # Оставляем строку как есть — не прогоняем через _clean_text_block
            if child is not None:
                new_children.append(child)
        elif isinstance(child, dict):
            cleaned_child = _clean_node_texts(child)
            if cleaned_child:
                new_children.append(cleaned_child)
        elif child is not None:
            new_children.append(child)
    if not new_children:
        return None
    node["children"] = new_children
    return node


def _clean_telegraph_nodes(nodes: list) -> list:
    """Централизованная очистка Telegraph nodes от мусора. Применять перед любой публикацией."""
    cleaned = []
    for n in nodes:
        cn = _clean_node_texts(n)
        if not cn:
            continue
        # Выбрасываем node если после чистки он целиком мусор
        if isinstance(cn, dict) and cn.get("tag") in ("p", "h3", "h4", "blockquote"):
            children = cn.get("children", [])
            if all(isinstance(c, str) for c in children):
                if all(is_meta_garbage(c.strip()) for c in children if isinstance(c, str)):
                    continue
        cleaned.append(cn)
    # Убираем ведущие мусорные параграфы — они попадают в preview/snippet
    # Пустой text (is_meta_garbage возвращает True на "") не удаляем —
    # пустые параграфы служат визуальными разделителями между секциями.
    while cleaned and isinstance(cleaned[0], dict):
        if cleaned[0].get("tag") == "p":
            children = cleaned[0].get("children", [])
            text = " ".join(c for c in children if isinstance(c, str)).strip()
            if is_meta_garbage(text):
                cleaned.pop(0)
                continue
            # Убираем строки с датами типа "March 15 at 18:55"
            if re.search(r"\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}", text, re.IGNORECASE):
                cleaned.pop(0)
                continue
        break
    return cleaned
