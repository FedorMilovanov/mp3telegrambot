#!/usr/bin/env python3
"""
Telegraph publisher — публикация с рекурсивным дроблением.
Извлечено из bot.py строки 4437–5288.
"""
from core.text_utils import (
    _scrub_inline, _strip_meta_lines, normalize_author_name,
    title_case_fragment, normalize_title_text,  # FIX telegraph
    BAD_META_PATTERNS, is_meta_garbage,          # FIX telegraph
)
from converters.md_telegraph import (
    _section_to_nodes_v2, _build_toc_nodes_v2, _build_nav_nodes_v2,
    _split_sections_smart, _create_telegraph_page_single, _edit_telegraph_page,
    _final_telegraph_polish,
    _HEADING_BOLD_STRIP_RE,   # FIX telegraph
    _estimate_nodes_v2,       # FIX telegraph
    _extract_partial_sections,# FIX telegraph
)
# _fix_rtl_in_text и _md_parse_inline перенесены в core_utils (разрыв цикла markdown ↔ caption/telegraph)
from core.core_utils import _fix_rtl_in_text, _md_parse_inline, _polish_timestamps_in_text, normalize_misbolded_bullet_lead, unescape_markdown_markers  # FIX: circular imports + AUDIT-FIX BUG 1
from core.globals import (
    TELEGRAPH_TOKEN, GEMINI_CLIENTS,
    gemini_generate,          # FIX telegraph,
    make_audio_config,
    is_quota_error, is_overload_error,
)
from core.database import GEMINI_MODEL      # FIX telegraph
from core.utils import format_timestamp     # FIX telegraph
from core.prompts import SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA, SYNOPSIS_VERBATIM_PROMPT  # FIX telegraph
from core.content_audit import audit_expanded_sections, format_content_audit_issues, has_content_audit_warnings
from services.gemini_analyze import _spawn_safe_delete  # FIX: cleanup uploaded Gemini files
from core.observability import alog_gemini_response, alog_gemini_run
from core.content_audit import get_content_audit_mode, should_abort_for_content_audit
from core.candidate_schema import expanded_page_response_schema
from core.reasoning_guidance import build_synopsis_reasoning_note
from core.synopsis_quality import (
    audit_synopsis_density, filter_organizational_announcement_sections,
    format_synopsis_quality_issues, get_synopsis_density_profile,
    should_retry_synopsis_density, synopsis_density_score,
)
from core.prompt_compactor import compact_prompt_for_generation
from core.generated_pages import aget_related_materials, extract_scripture_refs
from converters.md_telegraph import _build_related_materials_nodes

import asyncio
import json      # FIX telegraph
import logging
import os
import re
import tempfile
from pathlib import Path
import requests

# types — из google.genai
try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)


SYNOPSIS_REASONING_FIRST_NOTE = build_synopsis_reasoning_note()



def _synopsis_density_retry_enabled() -> bool:
    """Opt-out flag for one extra Synopsis retry when long output is too thin."""
    return (os.getenv("SYNOPSIS_DENSITY_RETRY", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}

def _synopsis_structured_output_enabled() -> bool:
    """Opt-in flag for Synopsis structured {outline, sections}.

    История репо показала: старый schema-free Synopsis чаще давал плотную
    «стенограмму со структурой». response_schema полезна для JSON-формы, но
    на длинных дословных конспектах может сжимать content. Поэтому Synopsis
    по умолчанию работает в legacy JSON mode; включение: SYNOPSIS_STRUCTURED=1.
    """
    return (os.getenv("SYNOPSIS_STRUCTURED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


def _synopsis_verbatim_prompt_enabled() -> bool:
    """Default ON: clean prompt for structured verbatim transcript Synopsis."""
    return (os.getenv("SYNOPSIS_VERBATIM_PROMPT", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _demote_paragraph_bold(line: str) -> str:
    """DEEP-QUALITY FIX [D]: если в строке **bold** покрывает >70% символов —
    это явно жирный АБЗАЦ (плохо визуально). Понижаем: убираем **, оставляя текст обычным.
    Если жирное короткое (<50% строки) — оставляем как есть, это нормальный акцент.

    FIX 2026-05-24: MIN_LINE_LEN=80 — короткие строки (<80 символов)
    целиком bold — это осознанные акценты (тезисы, поворотные фразы).
    FIX 2026-05-25: per-block check — проверяем каждый bold-кусок по отдельности,
    а не всю строку. Demote только если ОДИН кусок > 120 символов.
    """
    if not line or '**' not in line:
        return line
    if "||" in line:
        # Legacy terms_data / source separators: не трогаем форматные разделители.
        return line
    # Считаем длину жирных кусков
    bold_matches = re.findall(r'\*\*([^*\n]+)\*\*', line)
    if not bold_matches:
        return line
    bold_len = sum(len(b) for b in bold_matches)
    total_len = len(re.sub(r'\*\*([^*\n]+)\*\*', r'\1', line))
    if total_len == 0:
        return line
    bold_ratio = bold_len / total_len
    # FIX: bullet definition items like "• **Term — definition**, explanation"
    # have high bold_ratio but this is intentional formatting, not a "bold paragraph".
    # Don't demote if the line starts with a bullet marker + bold.
    if line.lstrip().startswith(('• **', '- **')):
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
        return re.sub(r'\*\*([^*\n]+)\*\*', _demote_if_long, line)

    # Общий ratio check — если вся строка >70% жирная, снимаем всё
    if bold_ratio > 0.7:
        return re.sub(r'\*\*([^*\n]+)\*\*', r'\1', line)
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
        if re.fullmatch(r'[.\-–—─·•*_~\s]+', s):
            prev_was_empty = True
            continue
        # Добавляем визуальный отступ только после заголовков — между обычными <p>
        # Telegraph сам даёт отступ, лишний <br> даёт двойной пробел
        last_tag = nodes[-1].get("tag") if nodes else ""
        if prev_was_empty and nodes and last_tag in ("h3", "h4") and not s.startswith(("#", "---", "***", "___")):
            nodes.append({"tag": "p", "children": [{"tag": "br"}]})
        prev_was_empty = False
        if s.startswith("#### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r'\1', s[5:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s.startswith("### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r'\1', s[4:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s.startswith("## "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r'\1', s[3:])
            nodes.append({"tag": "h3", "children": [_h_text]})
        elif s.startswith("# "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r'\1', s[2:])
            nodes.append({"tag": "h3", "children": [_h_text]})
        # FIX 2026-05-21 #7: ##### и ###### → h4 (вместо <p>)
        elif s.startswith("###### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r'\1', s[7:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s.startswith("##### "):
            _h_text = _HEADING_BOLD_STRIP_RE.sub(r'\1', s[6:])
            nodes.append({"tag": "h4", "children": [_h_text]})
        elif s in ("---", "***", "___"):
            nodes.append({"tag": "hr"})
        elif s.startswith("> "):
            _bq_text = s[2:]
            _bq_has_rtl = bool(re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _bq_text))
            if _bq_has_rtl:
                _bq_text = _fix_rtl_in_text(_bq_text)
            _bq_node = {"tag": "blockquote", "children": _md_parse_inline(_bq_text)}
            if _bq_has_rtl:
                _bq_node["attrs"] = {"dir": "ltr"}
            nodes.append(_bq_node)
        elif re.match(r'^\d+\.\s', s):
            num_m = re.match(r'^(\d+)\.\s+(.*)', s)
            if num_m:
                _num, _rest = num_m.group(1), num_m.group(2)
                _rest_stripped = _rest.lstrip()
                # Если _rest — только жирный заголовок (**Title.**) без текста после —
                # вливаем номер прямо внутрь <b>, чтобы получился один тег: <b>1. Title.</b>
                # Иначе два соседних <b> рендерятся в Telegraph непоследовательно.
                _only_bold = re.match(r'^\*\*([^*]+)\*\*\.?$', _rest_stripped)
                if _only_bold:
                    _title = _rest_stripped[2:].rstrip('*').rstrip('.')
                    _ob_node = {"tag": "p", "children": [
                        {"tag": "b", "children": [f"{_num}. {_title}."]}
                    ]}
                    if re.search(r'[֐-׿؀-ۿ]', _title):
                        _ob_node["attrs"] = {"dir": "ltr"}
                    nodes.append(_ob_node)
                elif _rest_stripped.startswith("**"):
                    # Заголовок + текст: сливаем номер в первый <b>
                    _inline = _md_parse_inline(_rest_stripped)
                    _inline_has_rtl = bool(re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _rest_stripped))
                    if _inline and isinstance(_inline[0], dict) and _inline[0].get("tag") == "b":
                        _inline[0]["children"] = [f"{_num}. "] + _inline[0]["children"]
                        _p_inl = {"tag": "p", "children": _inline}
                        if _inline_has_rtl:
                            _p_inl["attrs"] = {"dir": "ltr"}
                        nodes.append(_p_inl)
                    else:
                        _p_inl2 = {"tag": "p", "children":
                            [{"tag": "b", "children": [f"{_num}. "]}] + _inline}
                        if _inline_has_rtl:
                            _p_inl2["attrs"] = {"dir": "ltr"}
                        nodes.append(_p_inl2)
                else:
                    _num_has_rtl = bool(re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _rest))
                    if _num_has_rtl:
                        _rest = _fix_rtl_in_text(_rest)
                    _num_node = {"tag": "p", "children":
                        [{"tag": "b", "children": [f"{_num}.\u00a0"]}] + _md_parse_inline(_rest)
                    }
                    if _num_has_rtl:
                        _num_node["attrs"] = {"dir": "ltr"}
                    nodes.append(_num_node)
            else:
                nodes.append({"tag": "p", "children": _md_parse_inline(re.sub(r'^\d+\.\s', '', s))})
        elif s.startswith(("- ", "• ", "· ")) or (
            s.startswith("* ") and not s.endswith("*/") and
            not (re.search(r'(?<!\*)\*\s*$', s) and not re.search(r'\*\*\s*$', s))
        ):  # "* " как список; исключаем CSS-комментарии и курсив-обёртки "* весь текст *"
            _li_text = s[2:]
            _li_has_rtl = bool(re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _li_text))
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
            _has_rtl = bool(re.search(r'[\u0590-\u05FF\u0600-\u06FF]', s))
            _s_for_parse = _fix_rtl_in_text(s) if _has_rtl else s
            _p_children = _md_parse_inline(_s_for_parse)
            # v10b RTL FIX: если первый child — dict (bold/italic) с ивритом,
            # браузер BiDi считает весь абзац RTL и «улетает» вправо.
            # Вставляем LTR Mark (U+200E) первым символом → абзац остаётся LTR.
            # Telegraph strips dir="ltr" attr, поэтому только текстовый якорь работает.
            if (_has_rtl and _p_children
                    and isinstance(_p_children[0], dict)
                    and _p_children[0].get("tag") in ("b", "i")):
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
            if re.search(r'\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}', text, re.IGNORECASE):
                cleaned.pop(0)
                continue
        break
    return cleaned


async def _telegraph_post(title: str, author: str, nodes: list, loop, author_url: str = "") -> str | None:
    """Публикует страницу в Telegraph, возвращает URL или None.
    При CONTENT_TOO_BIG автоматически разбивает на части и создаёт оглавление."""

    # A05 FIX: финальная полировка перед публикацией (orphaned **, h1/h2, bidi, пустые nodes)
    nodes = _final_telegraph_polish(nodes)
    nodes = _clean_telegraph_nodes(nodes)

    # Ядерная чистка: убираем Cocoon AI и мусор из всех нод и title
    _dirty_phrases = [
        "Cocoon AI Summary", "Cocoon AI", "cocoon ai summary", "cocoon ai",
        "AI Summary", "Content Summary", "Auto-generated summary", "Auto-Generated Summary",
    ]
    for _phrase in _dirty_phrases:
        title = title.replace(_phrase, "").strip()
    try:

        _raw = json.dumps(nodes, ensure_ascii=False)
        for _phrase in _dirty_phrases:
            _raw = _raw.replace(_phrase, "")
        nodes = json.loads(_raw)
        # Убираем пустые ноды после чистки
        nodes = [n for n in nodes if n and (
            not isinstance(n, dict) or n.get("children", [True])
        )]
    except Exception:
        pass

    token = TELEGRAPH_TOKEN
    if not token:
        logger.warning("TELEGRAPH_TOKEN не задан")
        return None

    async def _post_once(t, ns):
        logger.info(f"Telegraph: публикую '{t}' ({len(ns)} блоков)")
        last_err = ""
        # FIX AUDIT R4: FLOOD_WAIT-ретрай — burst-публикация Synopsis+Study+
        # Reflection упиралась в FLOOD_WAIT_5, и Terms/Analytics страница
        # терялась навсегда, хотя 5 секунд ожидания решали проблему.
        for _attempt in range(3):
            try:
                resp = await loop.run_in_executor(None, lambda: requests.post(
                    "https://api.telegra.ph/createPage",
                    json={"access_token": token, "title": t,
                          "author_name": (author or "")[:128],  # FIX 2026-05-21 P1: Telegraph API limit
                          "author_url": (author_url or "")[:512],  # AUDIT M21
                          "content": ns, "return_content": False},
                    timeout=30,
                ))
                data = resp.json()
                if data.get("ok"):
                    return data["result"]["url"], None
                last_err = data.get("error", "")
            except Exception as e:
                logger.warning(f"Telegraph ошибка: {e}")
                last_err = str(e)
            _fw = re.search(r"FLOOD_WAIT_(\d+)", str(last_err))
            if _fw and _attempt < 2:
                _wait = min(int(_fw.group(1)), 30) + 1
                logger.info(f"Telegraph FLOOD_WAIT — жду {_wait}s и повторяю")
                await asyncio.sleep(_wait)
                continue
            return None, last_err
        return None, last_err

    url, err = await _post_once(title, nodes)
    if url:
        return url

    # Если ошибка не CONTENT_TOO_BIG — выходим
    if err != "CONTENT_TOO_BIG":
        logger.warning(f"Telegraph API: {err}")
        return None

    # CONTENT_TOO_BIG — разбиваем пополам и публикуем по частям
    logger.info(f"Telegraph: CONTENT_TOO_BIG для '{title}', разбиваю на части...")

    def _find_split_index(ns: list) -> int:
        """Ищет индекс разреза: идёт назад от середины до первого h3/h4-узла
        и делает разрез ПЕРЕД ним. Если h3/h4 не найден — делит по середине."""
        mid = max(1, len(ns) // 2)
        for j in range(mid, 0, -1):
            node = ns[j]
            if isinstance(node, dict) and node.get("tag") in ("h3", "h4"):
                return j  # разрез перед этим заголовком
        return mid  # fallback: по середине

    split_idx = _find_split_index(nodes)
    chunks = [nodes[:split_idx], nodes[split_idx:]]
    chunks = [c for c in chunks if c]  # убираем пустые

    parts_urls = []

    # FIX AUDIT R4: рекурсивный сплит вместо жёстких двух уровней — раньше
    # третья глубина CONTENT_TOO_BIG (или FLOOD_WAIT части) молча ТЕРЯЛА
    # четверть контента, а TOC рапортовал успех.
    async def _publish_chunk(chunk, label: str, depth: int = 0) -> bool:
        part_url, part_err = await _post_once(f"{title} ({label})", chunk)
        if part_url:
            parts_urls.append((label, part_url))
            return True
        if part_err == "CONTENT_TOO_BIG" and depth < 4 and len(chunk) > 1:
            _s = _find_split_index(chunk)
            _subs = [c for c in [chunk[:_s], chunk[_s:]] if c]
            _ok = True
            for _i, _sub in enumerate(_subs, 1):
                _ok = await _publish_chunk(_sub, f"{label}.{_i}", depth + 1) and _ok
            return _ok
        logger.warning(f"Telegraph: часть '{label}' не опубликована: {part_err}")
        return False

    _all_ok = True
    for idx, chunk in enumerate(chunks, 1):
        _all_ok = await _publish_chunk(chunk, str(idx)) and _all_ok

    if not parts_urls:
        return None
    if not _all_ok:
        logger.warning(
            "Telegraph: '%s' опубликован НЕ полностью (%d частей) — TOC неполный",
            title, len(parts_urls),
        )
    if len(parts_urls) == 1:
        return parts_urls[0][1]

    # Оглавление
    toc_nodes = [{"tag": "hr"}]
    for num, part_url in parts_urls:
        toc_nodes.append({"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": part_url}, "children": [f"Часть {num}"]}
        ]})
    toc_url, _ = await _post_once(f"{title} — Содержание", toc_nodes)
    return toc_url


async def create_telegraph_synopsis(mp3_path, title, performer, duration, url="",
                                     existing_audio_part=None, existing_client=None,
                                     ai_data=None, rutube_url="", vk_url="",
                                     source_lang: str = ""):
    """
    v2.1 — Format-aware конспект в Telegraph.

    1. Gemini возвращает JSON {outline, sections}.
    2. Для format=qa использует SYNOPSIS_PROMPT_QA с timestamps как каркасом.
    3. Для остальных форматов — обычный SYNOPSIS_PROMPT_V2.
    4. Деление на части — только по границам разделов.
    5. Навигация добавляется через editPage после публикации всех частей.
    """
    # FIX: track uploaded Gemini files for cleanup after synopsis generation
    _synopsis_uploaded_files: list[tuple] = []  # [(client, file_name), ...]
    try:
        if not GEMINI_CLIENTS:
            logger.warning("Synopsis: нет GEMINI_CLIENTS — пропускаем")
            return None, None

        try:
            _duration = float(duration)
        except (ValueError, TypeError):
            _duration = 0

        duration_str = format_timestamp(_duration)
        _fmt         = (ai_data or {}).get("format", "other") or "other"
        _timestamps  = (ai_data or {}).get("timestamps", "") or ""
        _real_author = normalize_author_name((ai_data or {}).get("real_author") or performer)
        author       = _real_author or normalize_author_name(performer) or "Проповедь"
        _real_title  = normalize_title_text((ai_data or {}).get("real_title") or "") or ""
        prompt_title = _real_title or normalize_title_text(title) or title
        prompt_title = title_case_fragment(prompt_title)
        tg_title     = prompt_title
        if author and author != "Проповедь":
            tg_title = f"{tg_title} — {author}"
        tg_title = tg_title[:256]  # fix #12: Telegraph API ограничение 256 символов

        is_qa = (_fmt == "qa")
        _ts_lines = [l.strip() for l in _timestamps.splitlines() if l.strip()]

        if is_qa and _ts_lines:
            timestamps_block = "\n".join(f"- {l}" for l in _ts_lines)
            _qa_prompt_template = SYNOPSIS_PROMPT_QA
            logger.info("Synopsis QA: единый промпт для всех вызовов")
            prompt = SYNOPSIS_REASONING_FIRST_NOTE + "\n\n" + _qa_prompt_template.format(
                title=prompt_title,
                duration=duration_str,
                timestamps_block=timestamps_block,
            )
            _syn_profile = get_synopsis_density_profile(_duration)
            _syn_max_tokens = _syn_profile.max_tokens
            logger.info(
                f"Synopsis: mode=QA format={_fmt} questions={len(_ts_lines)} "
                f"duration={int(_duration)//60}min"
            )
        else:
            _hm = (ai_data or {}).get("hermeneutic_method") or "mixed"

            _syn_profile = get_synopsis_density_profile(_duration)
            _syn_sections    = _syn_profile.sections
            _syn_section_len = _syn_profile.section_len
            _syn_total       = _syn_profile.total_chars
            _syn_max_tokens  = _syn_profile.max_tokens
            # AUDIT M6: расширили карту format → инструкция. Раньше для
            # sermon/lecture/topical/narrative/testimony в промт уходила
            # пустая строка, и Gemini угадывал голос (1-е лицо vs безличное) по заголовку.
            _format_note_map = {
                "interview":  "Формат: ИНТЕРВЬЮ — беседа двух людей. Сохраняй диалоговую структуру: голос ведущего и голос гостя, не сводя к монологу.",
                "discussion": "Формат: ДИСКУССИЯ — несколько участников. Сохраняй обмен позициями, не сглаживай разногласия в единый нарратив.",
                "sermon":     "Формат: ПРОПОВЕДЬ — гомилетический материал. Сохраняй пасторский голос проповедника и обращения к слушателям (вы/мы), сохраняй риторические повторы.",
                "lecture":    "Формат: ЛЕКЦИЯ — академический материал. Безличный регистр, чёткая логическая структура, термины не упрощай.",
                "testimony":  "Формат: СВИДЕТЕЛЬСТВО — личный нарратив. Сохраняй 1-е лицо рассказчика, эмоциональную тональность и хронологию событий.",
                "topical":    "Формат: ТЕМАТИЧЕСКИЙ ОБЗОР — несколько подтем под общей рамкой. Каждая секция должна заметно отличаться по подтеме.",
                "narrative":  "Формат: НАРРАТИВ — повествование с сюжетной аркой. Сохраняй порядок событий и ключевые повороты.",
                "qa":         "Формат: Q&A — вопросы и ответы. Каждая секция = один вопрос + развёрнутый ответ; не объединяй разные вопросы в одну секцию.",
            }
            _format_note = _format_note_map.get(_fmt, "")
            # BP-01 FIX: передаём primary-анализ в конспект, чтобы
            # Gemini не изобретал материал заново, а достраивал уже известное.
            _main_topic     = (ai_data.get("main_topic") or "").strip()[:600]
            _analysis_summ  = (ai_data.get("analysis_summary") or "").strip()[:500]
            # argument_arc: available via ai_data if needed later
            _key_cats_raw   = ai_data.get("key_categories") or []
            _key_cats_str2  = ", ".join(_key_cats_raw[:6]) if isinstance(_key_cats_raw, list) else str(_key_cats_raw)[:200]
            # Таймкоды из первичного анализа — как якорная сетка
            _ts_raw = ai_data.get("timestamps") or []
            _ts_anchor = ""
            if isinstance(_ts_raw, list) and _ts_raw:
                _ts_source = _ts_raw[:24]
                # Preserve the final anchor even when the primary analysis has many timestamps.
                if len(_ts_raw) > 24 and _ts_raw[-1] not in _ts_source:
                    _ts_source = _ts_source[:-1] + [_ts_raw[-1]]
                _ts_lines = [f"{t.get('time','?')} — {t.get('topic','')}" for t in _ts_source if isinstance(t, dict)]
                _ts_anchor = "\n".join(_ts_lines)
            _primary_context = ""
            if _main_topic or _analysis_summ:
                _primary_context = (
                    f"\n\n--- ЧТО УЖЕ ИЗВЕСТНО О МАТЕРИАЛЕ (не переизобретай) ---\n"
                    f"Главная тема: {_main_topic}\n"
                    f"Суть: {_analysis_summ}\n"
                    + (f"Ключевые категории: {_key_cats_str2}\n" if _key_cats_str2 else "")
                    + (f"Опорные таймкоды:\n{_ts_anchor}\n" if _ts_anchor else "")
                    + "Покрой ход материала до последнего опорного таймкода; не обрывай конспект на первой половине.\n"
                    + "НЕ ПРОТИВОРЕЧЬ этим данным. Достраивай конспект, не переизобретай.\n"
                    + "--- КОНЕЦ КОНТЕКСТА ---"
                )
            _syn_prompt_template = (
                SYNOPSIS_VERBATIM_PROMPT
                if _synopsis_verbatim_prompt_enabled()
                else SYNOPSIS_PROMPT_V2
            )
            _verbatim_context = ""
            if _ts_anchor:
                _verbatim_context = (
                    "\n\n--- ОПОРНЫЕ ТАЙМКОДЫ ДЛЯ ПОКРЫТИЯ (не summary) ---\n"
                    + _ts_anchor
                    + "\nИспользуй их только как карту покрытия начала/середины/финала. "
                    + "Не превращай эти короткие тезисы в готовый конспект; пиши по transcript/audio.\n"
                    + "--- КОНЕЦ КАРТЫ ---"
                )
            _syn_format_note = (
                SYNOPSIS_REASONING_FIRST_NOTE + "\n" + _format_note
                + (_verbatim_context if _syn_prompt_template is SYNOPSIS_VERBATIM_PROMPT else _primary_context)
            )
            prompt = _syn_prompt_template.format(
                title=prompt_title,
                duration=duration_str,
                hermeneutic_method=_hm,
                format_note=_syn_format_note,
                syn_sections=_syn_sections,
                syn_section_len=_syn_section_len,
                syn_total=_syn_total,
            )
            logger.info(
                "Synopsis: mode=normal format=%s hermeneutic=%s duration=%dmin prompt=%s",
                _fmt, _hm, int(_duration) // 60,
                "verbatim" if _syn_prompt_template is SYNOPSIS_VERBATIM_PROMPT else "v2",
            )

        _transcript_attached = False
        _transcript_text = ""
        # Дословность: если YouTube/auto captions доступны, добавляем timed
        # transcript как текстовую опору. Аудио всё равно прикладывается, но
        # transcript резко снижает риск «обзорной статьи» вместо стенограммы.
        if not is_qa and url:
            try:
                from services.youtube_transcript import download_youtube_transcript_text
                try:
                    _tr_max = int(os.getenv("SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS", "120000") or "120000")
                except ValueError:
                    _tr_max = 120000
                _tr_workdir = Path(tempfile.gettempdir()) / f"synopsis_transcript_{getattr(mp3_path, 'stem', 'audio')}"
                _tr_text = await download_youtube_transcript_text(
                    url,
                    _tr_workdir,
                    lang=source_lang or "en",
                    max_chars=max(10000, min(_tr_max, 250000)),
                    expected_duration=_duration,
                )
                if _tr_text:
                    _transcript_attached = True
                    _transcript_text = _tr_text
                    # AUDIT R5 (Gemini 3.x guide): ДАННЫЕ — сначала, ЗАДАЧА и
                    # критические правила — последними строками промпта.
                    # Раньше 120К-стенограмма шла ПОСЛЕ инструкций, и модель
                    # теряла форматные ограничения, прочитанные до неё.
                    prompt = (
                        "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
                        "ОРИГИНАЛЬНАЯ АНГЛИЙСКАЯ СТЕНОГРАММА / AUTO-CAPTIONS\n"
                        "(данные; задача и правила — ПОСЛЕ стенограммы)\n"
                        "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n\n"
                        + _tr_text
                        + "\n\n%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
                        "КОНЕЦ СТЕНОГРАММЫ — ДАЛЕЕ ЗАДАЧА И ПРАВИЛА\n"
                        "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n\n"
                        + prompt
                        + "\n\nСТЕНОГРАММА ВЫШЕ — главный текстовый скелет речи: "
                        "переводи и структурируй её максимально дословно на русский, "
                        "не превращай в summary. Сохраняй порядок фраз, примеры, имена, "
                        "риторические вопросы и переходы. Аудиофайл используй для "
                        "проверки интонации и спорных мест."
                    )
            except Exception as _tr_err:
                logger.info("[SynopsisTranscript] prompt attach failed: %s", str(_tr_err)[:160])

        if _transcript_attached:
            logger.info("Synopsis: transcript-backed mode — structured response_schema disabled for verbatim density")

        _compacted_prompt = compact_prompt_for_generation(prompt)
        if _compacted_prompt.saved_chars:
            logger.info(
                "Synopsis: prompt compacted %d -> %d chars (removed_lines=%d)",
                _compacted_prompt.original_chars, _compacted_prompt.compacted_chars,
                _compacted_prompt.removed_lines,
            )
        prompt = _compacted_prompt.text

        loop   = asyncio.get_running_loop()
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)

        async def _generate_synopsis_content(
            client,
            model_name: str,
            contents,
            max_tokens: int = 32000,
            *,
            use_schema: bool = True,
        ):
            """Generate synopsis JSON.

            For transcript-backed verbatim Synopsis we prefer legacy JSON mode:
            response_schema is excellent for shape, but in long transcripts it can
            bias Gemini toward terse object fields. The prompt/audit still enforce
            JSON and density.
            """
            if use_schema and _synopsis_structured_output_enabled():
                try:
                    return await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=make_audio_config(
                                max_output_tokens=max_tokens,
                                model_name=model_name,
                                response_mime_type="application/json",
                                response_schema=expanded_page_response_schema(),
                            ),
                        ),
                        timeout=960.0,
                    )
                except Exception as _schema_err:
                    if is_quota_error(_schema_err) or is_overload_error(_schema_err):
                        raise
                    logger.warning(
                        "Synopsis structured output failed (%s: %s) — retry legacy JSON config",
                        type(_schema_err).__name__, str(_schema_err)[:180],
                    )
            return await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=make_audio_config(max_output_tokens=max_tokens, model_name=model_name),
                ),
                timeout=960.0,
            )

        async def _synopsis_with_client(client, upload_fn, prompt_text, _loop):
            """fix #2: вспомогательная функция — загружает аудио и вызывает Gemini."""
            audio = await upload_fn(client)
            if audio is None:
                raise RuntimeError("_synopsis_with_client: upload_fn вернул None")
            if getattr(audio, "state", None) == "FAILED":
                raise RuntimeError("_synopsis_with_client: файл в состоянии FAILED")
            return await _generate_synopsis_content(
                client, GEMINI_MODEL, [audio, prompt_text],
                max_tokens=_syn_max_tokens,
                use_schema=not _transcript_attached,
            )

        def _extract_response_text(resp) -> str:
            if resp is None:
                return ""
            try:
                return (resp.text or "").strip()
            except Exception:
                chunks = []
                candidates = getattr(resp, "candidates", None) or []
                if candidates:
                    first = candidates[0]
                    content = getattr(first, "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        if not getattr(part, "thought", False):
                            text_part = getattr(part, "text", None)
                            if text_part:
                                chunks.append(text_part)
                return "".join(chunks).strip()

        # ── Запрос к Gemini ───────────────────────────────────
        response = None
        await asyncio.sleep(2)  # AUDIT-V2-SLEEP: 2s достаточно (было 5s)

        if existing_audio_part is not None and existing_client is not None:
            try:
                response = await _generate_synopsis_content(
                    existing_client, GEMINI_MODEL, [existing_audio_part, prompt],
                    max_tokens=_syn_max_tokens,
                    use_schema=not _transcript_attached,
                )
            except Exception as e:
                logger.warning(f"Synopsis v2 existing_audio_part failed: {e}, re-uploading...")
                response = None

        if response is None:
            async def _upload(client):
                if file_size_mb > 20:
                    uf = await client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    )
                    _uf_start = asyncio.get_running_loop().time()
                    while uf.state == "PROCESSING":
                        if asyncio.get_running_loop().time() - _uf_start > 300:
                            raise TimeoutError("Gemini file processing timeout (300s)")
                        await asyncio.sleep(3)
                        uf = await client.aio.files.get(name=uf.name)
                    if uf.state == "FAILED":  # fix #8
                        raise Exception("Gemini file processing failed")
                    _synopsis_uploaded_files.append((client, uf.name))  # FIX: track for cleanup
                    return uf
                else:
                    audio_bytes = mp3_path.read_bytes()
                    return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")

            async def _call_synopsis(client):
                return await _synopsis_with_client(client, _upload, prompt, loop)

            # AUDIT-SUPER-FIX-BUG3: multi-model fallback для synopsis
            # MODEL MIGRATION 2026-07: резерв — GA gemini-3.1-flash-lite
            # (2.5-flash-lite выключается ~22.07.2026).
            response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis, model_name=GEMINI_MODEL)

            # 429 / 503 на основной модели → пробуем резервную
            if response is None:
                logger.warning("Synopsis: основная модель недоступна — пробую gemini-3.1-flash-lite")
                try:
                    async def _call_fallback(client):
                        audio = await _upload(client)
                        return await _generate_synopsis_content(
                            client, "gemini-3.1-flash-lite", [audio, prompt],
                            max_tokens=_syn_max_tokens,
                            use_schema=not _transcript_attached,
                        )
                    response = await gemini_generate(GEMINI_CLIENTS, _call_fallback, model_name=GEMINI_MODEL)
                    logger.info("Synopsis: fallback модель успешна")
                except Exception as _fb_err:
                    logger.warning(f"Synopsis fallback failed: {_fb_err}")

        # ── Извлекаем текст ответа ────────────────────────────
        synopsis_text = _extract_response_text(response)
        _obs_video_id = str(getattr(mp3_path, "stem", ""))
        if not synopsis_text:
            await alog_gemini_run(
                task="synopsis", video_id=_obs_video_id, model=GEMINI_MODEL,
                thinking_level="high", json_valid=False, error="empty_response",
            )
            return None, None

        # ── Парсим JSON (с retry при сломанном JSON) ─────────

        def _try_parse_synopsis_json(text: str) -> tuple[list, list] | None:
            """Возвращает (outline, sections) или None если не распарсилось."""
            try:
                clean = text.strip()
                clean = re.sub(r"^```[a-z]*\s*", "", clean)
                clean = re.sub(r"\s*```$",       "", clean).strip()
                s = clean.find("{")
                e = clean.rfind("}")
                if s == -1 or e <= s:
                    return None
                chunk = clean[s:e + 1]
                # Защита: Gemini иногда вставляет реальные переносы строк
                # внутри JSON-строк — это невалидный JSON. Экранируем их.
                def _fix_json_newlines(s: str) -> str:
                    result, in_str, i = [], False, 0
                    while i < len(s):
                        c = s[i]
                        if c == "\\" and i + 1 < len(s):
                            result.append(c + s[i + 1]); i += 2; continue
                        if c == '"': in_str = not in_str
                        if in_str and c == "\n": result.append("\\n"); i += 1; continue
                        if in_str and c == "\r": result.append("\\r"); i += 1; continue
                        if in_str and c == "\t": result.append("\\t"); i += 1; continue
                        result.append(c); i += 1
                    return "".join(result)
                try:
                    data = json.loads(chunk)
                except Exception:
                    data = json.loads(_fix_json_newlines(chunk))
                return data.get("outline", []) or [], data.get("sections", []) or []
            except Exception:
                return None

        def _looks_like_json(text: str) -> bool:
            t = text.strip()
            return t.startswith("{") or '"sections"' in t or '"outline"' in t

        sections: list = []
        outline:  list = []

        parsed = _try_parse_synopsis_json(synopsis_text)
        if parsed is not None:
            outline, sections = parsed
            logger.info("Synopsis: JSON распарсен успешно")
            await alog_gemini_response(
                response=response, task="synopsis", video_id=_obs_video_id,
                model=GEMINI_MODEL, thinking_level="high", json_valid=True,
            )
        elif _looks_like_json(synopsis_text):
            # Сломанный JSON → один retry с явным указанием на ошибку формата
            # (temperature на 3.x не переопределяется — см. make_audio_config)
            logger.warning("Synopsis: сломанный JSON от Gemini — выполняю retry")
            await asyncio.sleep(10)  # Пауза перед retry
            retry_response = None
            try:
                retry_prompt = (
                    "Твой предыдущий ответ содержал сломанный JSON. "
                    "Повтори запрос строго: только валидный JSON {outline, sections}, "
                    "без ```json, без текста до/после.\n\n" + prompt
                )
                if existing_audio_part is not None and existing_client is not None:
                    try:
                        retry_response = await _generate_synopsis_content(
                            existing_client, GEMINI_MODEL, [existing_audio_part, retry_prompt],
                            max_tokens=_syn_max_tokens,
                            use_schema=not _transcript_attached,
                        )
                    except Exception as re_e:
                        logger.warning(f"Synopsis retry existing_audio_part failed: {re_e}")
                        retry_response = None
                # Определяем _upload_retry здесь — вне условия — чтобы она была
                # доступна и в _call_synopsis_simple (3-й retry), даже если
                # 2-й retry прошёл через ветку existing_audio_part и этот блок
                # не выполнялся. Иначе возможен NameError.
                async def _upload_retry(client):
                    if file_size_mb > 20:
                        uf = await client.aio.files.upload(
                            file=mp3_path,
                            config=types.UploadFileConfig(
                                mime_type="audio/mpeg",
                                display_name=f"{performer} - {title}",
                            ),
                        )
                        _uf_start = asyncio.get_running_loop().time()
                        while uf.state == "PROCESSING":
                            if asyncio.get_running_loop().time() - _uf_start > 300:
                                raise TimeoutError("Gemini file processing timeout (300s)")
                            await asyncio.sleep(3)
                            uf = await client.aio.files.get(name=uf.name)
                        if uf.state == "FAILED":  # fix #8
                            raise Exception("Gemini file processing failed")
                        _synopsis_uploaded_files.append((client, uf.name))  # FIX: track for cleanup
                        return uf
                    else:
                        return types.Part.from_bytes(data=mp3_path.read_bytes(), mime_type="audio/mpeg")
                if retry_response is None:
                    async def _call_synopsis_retry(client):
                        return await _synopsis_with_client(client, _upload_retry, retry_prompt, loop)
                    retry_response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis_retry, model_name=GEMINI_MODEL)
                retry_text = _extract_response_text(retry_response)
                parsed_retry = _try_parse_synopsis_json(retry_text)
                if parsed_retry is not None:
                    outline, sections = parsed_retry
                    logger.info("Synopsis: retry успешен — JSON распарсен")
                else:
                    logger.warning("Synopsis: retry тоже вернул сломанный JSON — пробую упрощённый промпт")
                    # ── 3-й retry: упрощённый промпт (6 секций, меньше текста) ──
                    # FIX AUDIT R5: это НЕ format-строка — двойные {{ }} показывали
                    # модели буквально невалидный JSON в последнем retry.
                    simple_prompt = (
                        f"Создай краткий конспект материала «{title}» ({duration_str}).\n"
                        "Только валидный JSON без ```json и без текста вне JSON.\n"
                        "Ровно 6 разделов, каждый не более 150 слов.\n\n"
                        '{\n'
                        '  "outline": [{"title": "...", "time": "0:00"}, ...],\n'
                        '  "sections": [{"title": "...", "time": "0:00", "content": "..."}, ...]\n'
                        '}'
                    )
                    simple_response = None
                    try:
                        if existing_audio_part is not None and existing_client is not None:
                            try:
                                simple_response = await _generate_synopsis_content(
                                    existing_client, GEMINI_MODEL, [existing_audio_part, simple_prompt],
                                    max_tokens=32000,
                                    use_schema=True,
                                )
                            except Exception:
                                simple_response = None
                        if simple_response is None:
                            async def _call_synopsis_simple(client):
                                return await _synopsis_with_client(client, _upload_retry, simple_prompt, loop)
                            simple_response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis_simple, model_name=GEMINI_MODEL)
                        simple_text = _extract_response_text(simple_response)
                        parsed_simple = _try_parse_synopsis_json(simple_text)
                        if parsed_simple is not None:
                            outline, sections = parsed_simple
                            logger.info("Synopsis: упрощённый retry успешен")
                        else:
                            # Все три попытки провалились — Markdown-fallback из лучшего текста
                            logger.warning("Synopsis: все 3 попытки дали сломанный JSON — Markdown fallback")
                            best_text = retry_text if len(retry_text) > len(synopsis_text) else synopsis_text
                            # Пробуем вытащить хоть что-то из частично сгенерированного JSON
                            _partial = _extract_partial_sections(best_text)
                            if _partial:
                                outline, sections = [], _partial
                                logger.info(f"Synopsis: восстановлено {len(sections)} секций из неполного JSON")
                            else:
                                content = _md_to_telegraph_nodes(best_text)
                                return await _telegraph_post(tg_title, author, content, loop), None
                    except Exception as simple_err:
                        logger.warning(f"Synopsis: упрощённый retry exception: {simple_err} — Markdown fallback")
                        content = _md_to_telegraph_nodes(retry_text or synopsis_text)
                        return await _telegraph_post(tg_title, author, content, loop), None
            except Exception as retry_err:
                logger.warning(f"Synopsis: retry exception: {retry_err} — abort")
                return None, None
        else:
            # Не JSON вообще → Markdown fallback (как было)
            logger.warning("Synopsis: не-JSON от Gemini — Markdown fallback")
            content = _md_to_telegraph_nodes(synopsis_text)
            return await _telegraph_post(tg_title, author, content, loop), None

        # ── Валидация sections ────────────────────────────────
        if not isinstance(sections, list):
            logger.warning("Synopsis v2: sections не является списком — abort")
            return None, None
        if not isinstance(outline, list):
            outline = []

        sections = [s for s in sections if isinstance(s, dict)]
        if not sections:
            logger.warning("Synopsis v2: sections пуст после валидации — abort")
            return None, None

        total_before_filter = len(sections)
        sections = [s for s in sections if ((s.get("content") or "").strip() or s.get("blocks"))]
        removed_empty = total_before_filter - len(sections)
        if removed_empty > 0:
            logger.warning(
                f"Synopsis v2: удалено пустых sections={removed_empty} "
                f"из {total_before_filter}"
            )

        if not sections:
            logger.warning("Synopsis v2: все sections пусты после фильтрации content — abort")
            return None, None

        sections, outline, _content_audit = audit_expanded_sections(
            sections,
            outline if isinstance(outline, list) else [],
            label="Synopsis",
            expected_author=author,
        )
        _audit_mode = get_content_audit_mode()
        if _content_audit and _audit_mode != "off":
            _log_audit = logger.warning if has_content_audit_warnings(_content_audit) else logger.info
            _log_audit(
                "Synopsis v2: content audit before publish: %s",
                format_content_audit_issues(_content_audit),
            )
        sections, _org_issues = filter_organizational_announcement_sections(sections)
        if _org_issues:
            logger.warning(
                "Synopsis v2: organizational announcement filter: %s",
                format_synopsis_quality_issues(_org_issues),
            )
            outline = [{"title": s.get("title", ""), "time": s.get("time") or ""} for s in sections]

        if _content_audit and should_abort_for_content_audit(_content_audit):
            logger.warning("Synopsis v2: content audit strict abort")
            return None, None

        _syn_quality_issues = audit_synopsis_density(sections, _duration)
        # AUDIT #023: validate inline timestamps don't precede section.time
        from core.synopsis_quality import audit_inline_timestamp_order
        _ts_order_issues = audit_inline_timestamp_order(sections)
        if _ts_order_issues:
            logger.warning("Synopsis v2: inline timestamp order: %s", format_synopsis_quality_issues(_ts_order_issues))
        if _syn_quality_issues:
            logger.warning("Synopsis v2: density/coverage audit: %s", format_synopsis_quality_issues(_syn_quality_issues))

        if (
            _syn_quality_issues
            and _synopsis_density_retry_enabled()
            and should_retry_synopsis_density(_syn_quality_issues, _duration)
            and (
                (existing_audio_part is not None and existing_client is not None)
                or _transcript_attached
            )
        ):
            try:
                _min_section_chars = 700
                try:
                    _min_section_chars = max(650, int(str(_syn_profile.section_len).split("-", 1)[0]))
                except Exception:
                    pass
                _density_retry_prompt = (
                    prompt
                    + "\n\nТвой предыдущий Synopsis был СЛИШКОМ СЖАТЫМ для длительности материала. "
                    + "Это не косметическая просьба, а обязательная правка качества.\n"
                    + f"ЖЁСТКИЙ МИНИМУМ: суммарно не меньше {_syn_profile.min_total_chars} знаков живого content; "
                    + f"каждый обычный section — не меньше {_min_section_chars} знаков и 4–8 абзацев.\n"
                    + "Сделай более полный 99.9%-дословный конспект-сжатую стенограмму: больше sections, "
                    + "больше дословных авторских фраз, имён, дат, историй, риторических вопросов, "
                    + "inline-якоря ⏱ внутри content, покрытие до финальной части, без обзорной статьи.\n"
                    + "Если не хватает места, лучше меньше декоративных blocks, но длиннее content каждой секции.\n"
                    + f"Проблемы качества: {format_synopsis_quality_issues(_syn_quality_issues)}"
                )
                if _transcript_attached:
                    logger.info("Synopsis v2: density retry uses transcript-only text path")
                    async def _call_density_retry_text(client):
                        return await _generate_synopsis_content(
                            client,
                            GEMINI_MODEL,
                            _density_retry_prompt,
                            max_tokens=min(int(_syn_max_tokens * 1.35), 65000),
                            use_schema=False,
                        )
                    _retry_resp = await gemini_generate(
                        GEMINI_CLIENTS, _call_density_retry_text, model_name=GEMINI_MODEL
                    )
                else:
                    _retry_resp = await _generate_synopsis_content(
                        existing_client,
                        GEMINI_MODEL,
                        [existing_audio_part, _density_retry_prompt],
                        max_tokens=min(int(_syn_max_tokens * 1.35), 65000),
                        use_schema=False,
                    )
                _retry_text = _extract_response_text(_retry_resp)
                _retry_parsed = _try_parse_synopsis_json(_retry_text)
                if _retry_parsed is not None:
                    _retry_outline, _retry_sections = _retry_parsed
                    _retry_sections = [s for s in _retry_sections if isinstance(s, dict) and ((s.get("content") or "").strip() or s.get("blocks"))]
                    _retry_sections, _retry_outline, _retry_audit = audit_expanded_sections(
                        _retry_sections,
                        _retry_outline if isinstance(_retry_outline, list) else [],
                        label="SynopsisDensityRetry",
                        expected_author=author,
                    )
                    _retry_quality_issues = audit_synopsis_density(_retry_sections, _duration)
                    _old_score = synopsis_density_score(sections, _duration)
                    _new_score = synopsis_density_score(_retry_sections, _duration)
                    _old_issue_count = len(_syn_quality_issues)
                    _new_issue_count = len(_retry_quality_issues)
                    _retry_improved_issues = _new_issue_count < _old_issue_count
                    _retry_not_much_thinner = _new_score >= int(_old_score * 0.85)
                    if _new_score > _old_score or (_retry_improved_issues and _retry_not_much_thinner):
                        sections, outline = _retry_sections, _retry_outline
                        _syn_quality_issues = _retry_quality_issues
                        logger.info(
                            "Synopsis v2: density retry accepted (sections=%d score=%d->%d issues=%d->%d)",
                            len(sections), _old_score, _new_score, _old_issue_count, _new_issue_count,
                        )
                    else:
                        logger.info(
                            "Synopsis v2: density retry rejected — not denser, not enough issue improvement, or too much thinner (score=%d->%d issues=%d->%d)",
                            _old_score, _new_score, _old_issue_count, _new_issue_count,
                        )
            except Exception as _density_retry_err:
                logger.warning("Synopsis v2: density retry failed non-fatally: %s", str(_density_retry_err)[:180])

        # ── Валидация плотности sections (BP-04) ─────────────
        _thin_count = sum(1 for s in sections if len(((s.get("content") or "").strip() or s.get("blocks"))) < 100)
        if _thin_count > 0:
            logger.warning(
                f"Synopsis v2: {_thin_count}/{len(sections)} sections имеют content < 100 символов — "
                f"возможно неполный конспект"
            )

        # ── Защита от рассинхрона outline / sections ─────────
        # Для QA: промпт инструктирует модель НЕ включать 4 секции применения
        # (малая группа, семья, самоиспытание, молитва) в outline — у них time="".
        # Дополняем outline этими секциями явно, чтобы они попали в оглавление
        # без таймкода и без ложного rebuild-предупреждения.
        outline_rebuilt = False
        # Всегда строим финальный outline из sections — sections является единственным
        # источником правды. outline от Gemini используем только для извлечения таймкодов
        # (Gemini иногда пропускает последний раздел или путает порядок).
        if outline and len(outline) == len(sections):
            # Синхронизируем: берём title/time из sections, но если outline дал таймкод
            # для этой позиции — доверяем ему (он точнее при QA-формате)
            merged = []
            for sec, oi in zip(sections, outline):
                merged.append({
                    "title": sec.get("title", ""),
                    "time":  (oi.get("time") or sec.get("time") or ""),
                })
            outline = merged
        else:
            # Длины не совпадают — полностью перестраиваем из sections
            outline = [
                {"title": sec.get("title", ""), "time": sec.get("time", "")}
                for sec in sections
            ]
            outline_rebuilt = True

        # ── Диагностика ───────────────────────────────────────
        try:
            total_nodes_estimate = _estimate_nodes_v2(
                sections, url, rutube_url=rutube_url, vk_url=vk_url,
                duration=int(_duration or 0),
            )
        except Exception:
            total_nodes_estimate = -1

        mins = int(_duration) // 60
        _mode_label = f"QA({len(_ts_lines)}q)" if is_qa else f"normal({_fmt})"
        logger.info(
            f"Synopsis v2: mode={_mode_label} sections={len(sections)} outline={len(outline)} "
            f"outline_rebuilt={outline_rebuilt} nodes_estimate={total_nodes_estimate} "
            f"duration={mins}min"
        )

        if is_qa:
            question_like_titles = sum(
                1 for s in sections
                if ((s.get("title") or "").strip().endswith("?"))
            )
            logger.info(
                f"Synopsis QA: question_like_titles={question_like_titles}/{len(sections)}"
            )
            if len(sections) >= 3 and question_like_titles < max(1, len(sections) // 2):
                logger.warning(
                    "Synopsis QA: значительная часть заголовков не выглядит вопросительной — "
                    "проверь качество Q&A-структуры"
                )

            titles = [(s.get("title") or "").strip() for s in sections]
            non_empty_titles = [t for t in titles if t]
            dup_count = len(non_empty_titles) - len(set(non_empty_titles))
            if dup_count > 0:
                logger.warning(
                    f"Synopsis QA: обнаружены повторяющиеся заголовки={dup_count} — "
                    "это может быть как реальное совпадение вопросов, так и признак деградации структуры"
                )

        if is_qa and _ts_lines and len(sections) < len(_ts_lines):
            logger.warning(
                f"Synopsis QA: sections={len(sections)} < questions={len(_ts_lines)} "
                f"— часть вопросов может быть пропущена в конспекте"
            )

        if _duration > 1800 and len(sections) < 4:
            logger.warning(
                f"Synopsis v2: мало sections ({len(sections)}) для {mins}мин "
                f"— возможно неполный конспект"
            )

        if _duration > 600 and total_nodes_estimate != -1 and total_nodes_estimate < 20:
            logger.warning(
                f"Synopsis v2: мало nodes ({total_nodes_estimate}) для {mins}мин "
                f"— конспект может быть слишком коротким"
            )

        # AUDIT R5 GROUNDING: сохраняем фрагменты финальной стенограммы в
        # общий ai_data (in-memory blackboard) — Study/Reflection работают
        # text-only и без этого просили verbatim-цитаты у модели, у которой
        # нет текста речи (главный источник галлюцинаций цитат).
        # Ключ приватный: db_save его отбрасывает, в кэш не попадает.
        try:
            if isinstance(ai_data, dict) and sections:
                _ground_parts: list[str] = []
                _ground_len = 0
                for _gs in sections:
                    _g_title = str((_gs or {}).get("title") or "").strip()
                    _g_time = str((_gs or {}).get("time") or "").strip()
                    _g_text = str((_gs or {}).get("content") or "").strip()
                    if not _g_text:
                        continue
                    _chunk = f"### {_g_title} ({_g_time})\n{_g_text}"
                    _ground_parts.append(_chunk)
                    _ground_len += len(_chunk)
                    if _ground_len > 24000:
                        break
                if _ground_parts:
                    ai_data["_synopsis_grounding"] = "\n\n".join(_ground_parts)[:24000]
        except Exception:
            pass

        # ── Рекурсивная публикация по sections ───────────────
        # Стратегия: publish-first → при CONTENT_TOO_BIG делить sections пополам.
        # Деление только по границам sections. Никакой обрезки нод.
        # Если любая часть не публикуется — весь synopsis считается неуспешным.

        published_parts: list[tuple[list, str]] = []  # (part_secs, url)

        async def _publish_recursive(secs: list, depth: int = 0) -> bool:
            """Публикует список sections рекурсивно.
            Возвращает True если ВСЕ части успешно опубликованы.
            При любом fail — возвращает False (без частичного результата).
            """
            if not secs:
                return True

            sec_offset = sum(len(p[0]) for p in published_parts)
            sec_range  = f"sections[{sec_offset}..{sec_offset + len(secs) - 1}]"
            part_nodes = []
            for sec_idx, sec in enumerate(secs):
                if sec_idx > 0:
                    part_nodes.append({"tag": "hr"})
                part_nodes.extend(_section_to_nodes_v2(sec, yt_url=url,
                                                        rutube_url=rutube_url, vk_url=vk_url,
                                                        duration=duration))

            logger.info(
                f"Synopsis v2 depth={depth}: {sec_range} "
                f"({len(secs)} secs, {len(part_nodes)} nodes) — публикую..."
            )
            # FIX 2026-05-21 P0 #14: Telegraph path генерируется из title ОДИН РАЗ при createPage
            # и НЕ меняется при editPage. Раньше создавали с " [draft]" → URL навсегда содержал "-draft-DD-MM".
            # Теперь сразу даём финальный title — URL будет чистым.
            page_url, err = await _create_telegraph_page_single(tg_title, author, part_nodes, loop)

            if page_url:
                logger.info(f"Synopsis v2 depth={depth}: {sec_range} → OK {page_url}")
                published_parts.append((secs, page_url))
                return True

            if err != "CONTENT_TOO_BIG":
                logger.warning(f"Synopsis v2 depth={depth}: {sec_range} ошибка '{err}' — fail")
                return False

            # CONTENT_TOO_BIG — делим пополам по sections
            if len(secs) == 1:
                logger.warning(
                    f"Synopsis v2 depth={depth}: {sec_range} один section "
                    f"({len(part_nodes)} nodes) не влезает в Telegraph — fail"
                )
                return False

            mid = max(1, len(secs) // 2)
            logger.info(
                f"Synopsis v2 depth={depth}: {sec_range} CONTENT_TOO_BIG "
                f"({len(part_nodes)} nodes) → делю [{mid}]+[{len(secs)-mid}]"
            )
            ok_left = await _publish_recursive(secs[:mid], depth + 1)
            if not ok_left:
                return False
            return await _publish_recursive(secs[mid:], depth + 1)

        success = await _publish_recursive(sections)

        if not success:
            logger.warning("Synopsis v2: публикация не удалась — возвращаю None")
            # Удалять уже созданные страницы не можем (нет API delete),
            # но не возвращаем ссылку — бот не покажет Конспект в caption
            return None, None

        total      = len(published_parts)
        parts      = [p[0]   for p in published_parts]
        parts_urls = [p[1]   for p in published_parts]

        logger.info(f"Synopsis v2: опубликовано {total} частей")

        # ── Переименовываем и добавляем TOC + навигацию ──────
        # V3-P16: пауза перед editPage — Telegraph rate limit после быстрой публикации.
        # Без паузы editPage падает 3/3 при >50 nodes (0.7с между create и edit).
        await asyncio.sleep(2)
        for i, (part_secs, page_url) in enumerate(published_parts):
            part_num   = i + 1
            # FIX AUDIT R4: суффикс [N/M] не должен выталкивать итоговый
            # заголовок за лимит Telegraph 256 — editPage падал 3/3, и части
            # оставались без TOC/навигации.
            if total == 1:
                part_title = tg_title
            else:
                _sfx = f" [{part_num}/{total}]"
                part_title = tg_title[:256 - len(_sfx)] + _sfx

            nodes_edit: list = []
            if i == 0:
                nodes_edit.extend(_build_toc_nodes_v2(outline, yt_url=url, parts=parts))
            elif total > 1:
                # FIX BUG-5: add mini-outline for parts 2+ so reader sees section titles
                _mini_outline = []
                for _ps in part_secs:
                    _ps_title = _ps.get("title", "")
                    _ps_time = _ps.get("time", "")
                    if _ps_title:
                        _mini_outline.append({"tag": "p", "children": [
                            {"tag": "b", "children": [f"• {_ps_title}"]},
                            *([f" — ⏱ {_ps_time}"] if _ps_time else []),
                        ]})
                if _mini_outline:
                    nodes_edit.extend(_mini_outline)
                    nodes_edit.append({"tag": "hr"})
            for sec_idx, sec in enumerate(part_secs):
                if sec_idx > 0:
                    nodes_edit.append({"tag": "hr"})
                nodes_edit.extend(_section_to_nodes_v2(sec, yt_url=url,
                                                        rutube_url=rutube_url, vk_url=vk_url,
                                                        page_title=part_title, duration=duration))
            if total > 1:
                nodes_edit.extend(_build_nav_nodes_v2(i, total, parts_urls))
                
            # FEATURE 2026-06-11: Блок «Читать также» в самом конце материала
            if i == total - 1:
                try:
                    _video_id = str(getattr(mp3_path, 'stem', ''))
                    _scripture_refs = extract_scripture_refs(ai_data)
                    _related = await aget_related_materials(
                        author=author,
                        scripture=(_scripture_refs[0] if _scripture_refs else ""),
                        exclude_video_id=_video_id,
                        limit=3
                    )
                    if _related:
                        nodes_edit.extend(_build_related_materials_nodes(_related))
                except Exception as _rel_err:
                    logger.debug("Related materials block skip: %s", _rel_err)

            ok = False
            for retry_attempt in range(3):
                ok = await _edit_telegraph_page(page_url, part_title, author, nodes_edit, loop)
                if ok:
                    break
                wait_time = 3 * (2 ** retry_attempt)  # 3 → 6 → 12 сек
                logger.warning(
                    f"Synopsis v2: editPage часть {part_num}/{total} "
                    f"попытка {retry_attempt + 1}/3 failed для {page_url}, жду {wait_time}с..."
                )
                await asyncio.sleep(wait_time)

            # Edge fallback: first part can exceed Telegraph edit limit when
            # TOC is prepended after publish-time splitting. If editPage failed
            # for part 1, retry once without TOC but with section content and
            # next/prev navigation so the chain remains usable.
            if not ok and i == 0 and total > 1:
                _nodes_no_toc: list = []
                for sec_idx, sec in enumerate(part_secs):
                    if sec_idx > 0:
                        _nodes_no_toc.append({"tag": "hr"})
                    _nodes_no_toc.extend(_section_to_nodes_v2(
                        sec, yt_url=url,
                        rutube_url=rutube_url, vk_url=vk_url,
                        page_title=part_title, duration=duration,
                    ))
                _nodes_no_toc.extend(_build_nav_nodes_v2(i, total, parts_urls))
                logger.warning(
                    "Synopsis v2: editPage часть 1/%d упала с TOC — повтор без оглавления (content-size fallback)",
                    total,
                )
                await asyncio.sleep(2)
                ok = await _edit_telegraph_page(page_url, part_title, author, _nodes_no_toc, loop)

            # FIX BUG-5 fallback: if editPage for part 2+ failed with mini-outline,
            # retry without mini-outline
            if not ok and i > 0 and total > 1:
                _nodes_no_mini: list = []
                for sec_idx, sec in enumerate(part_secs):
                    if sec_idx > 0:
                        _nodes_no_mini.append({"tag": "hr"})
                    _nodes_no_mini.extend(_section_to_nodes_v2(
                        sec, yt_url=url,
                        rutube_url=rutube_url, vk_url=vk_url,
                        page_title=part_title, duration=duration,
                    ))
                _nodes_no_mini.extend(_build_nav_nodes_v2(i, total, parts_urls))
                logger.warning(
                    "Synopsis v2: editPage часть %d/%d упала с mini-outline — повтор без него",
                    part_num, total,
                )
                await asyncio.sleep(2)
                ok = await _edit_telegraph_page(page_url, part_title, author, _nodes_no_mini, loop)

            if ok:
                logger.info(f"Synopsis v2: editPage часть {part_num}/{total} → {page_url}")
            else:
                logger.warning(
                    f"Synopsis v2: editPage часть {part_num}/{total} окончательно failed, page content may stay outdated: {page_url}"
                )

        return parts_urls[0], outline

    except Exception as e:
        logger.warning(f"Synopsis v2 error: {e}")
        return None, None
    finally:
        # FIX: cleanup uploaded Gemini files to prevent file leak
        for _cl_client, _cl_name in _synopsis_uploaded_files:
            try:
                _spawn_safe_delete(_cl_client, _cl_name)
            except Exception:
                pass


# ─── Видимая длина HTML-caption и умная обрезка ──────────────



