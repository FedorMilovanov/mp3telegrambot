#!/usr/bin/env python3
"""
Telegraph Pages — публикация расширенных страниц.
Analytics, Questions, Terms, Study Analysis, Reflection.
Извлечено из bot.py строки 5289–7804.
"""
from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS, gemini_generate,   # FIX telegraph_pages
    is_overload_error, is_quota_error, make_text_config_smart,  # ULTIMATE FIX 3.5-FLASH
)
from core.database import GEMINI_MODEL      # FIX telegraph_pages
from core.text_utils import (
    _scrub_inline, normalize_author_name,
    _clean_field, _clean_meta_line,    # FIX telegraph_pages
    _strip_meta_lines, is_meta_garbage, # FIX telegraph_pages
    normalize_title_text, title_case_fragment,  # FIX telegraph_pages
)
from converters.md_telegraph import (
    _build_toc_nodes_v2, _build_nav_nodes_v2,
    _section_to_nodes_v2,              # FIX telegraph_pages
    _create_telegraph_page_single,     # FIX telegraph_pages
    _edit_telegraph_page,              # FIX telegraph_pages
    _estimate_nodes_v2,                # FIX telegraph_pages
)
from services.telegraph import _telegraph_post
from core.core_utils import _fix_rtl_in_text, _md_parse_inline  # AUDIT-V2-MDPARSE
from core.json_parser import (
    # _try_parse_synopsis_json,  # FIX 2026-05-21 #13: dead import — используется локальный в telegraph.py
    time_to_seconds,                   # FIX telegraph_pages
)
from core.url_utils import get_youtube_timestamp_url  # FIX telegraph_pages
from core.utils import format_timestamp               # FIX telegraph_pages
from core.prompts import STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT

import asyncio
import json      # FIX telegraph_pages
import logging
import re

# types — из google.genai
try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)

async def create_telegraph_analytics(ai_data: dict, title: str, author: str,
                                      yt_url: str = "") -> str | None:
    """v3.0 — Публикует аналитику (analysis_summary + argument_arc + key_categories) в Telegraph."""
    author = _clean_meta_line(author or "") or "Автор не указан"
    title  = _clean_meta_line(title  or "") or "Без названия"

    analysis_summary = _scrub_inline(_strip_meta_lines((ai_data or {}).get("analysis_summary", "")))
    argument_arc     = _scrub_inline(_strip_meta_lines((ai_data or {}).get("argument_arc", "")))
    key_categories   = (ai_data or {}).get("key_categories", []) or []

    if not analysis_summary and not argument_arc and not key_categories:
        return None

    loop  = asyncio.get_running_loop()
    nodes = []

    if analysis_summary and not is_meta_garbage(analysis_summary):
        nodes.append({"tag": "h3", "children": ["🧠 Аналитика"]})
        # AUDIT-V2-MDPARSE: прогоняем через _md_parse_inline → **bold** рендерится в Telegraph
        nodes.append({"tag": "p", "children": _md_parse_inline(analysis_summary)})

    if argument_arc:
        if nodes:
            nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": ["📈 Ход аргументации"]})
        # AUDIT-V2-MDPARSE: argument_arc через _md_parse_inline
        nodes.append({"tag": "p", "children": _md_parse_inline(argument_arc)})

    if key_categories:
        if nodes:
            nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": ["🗂 Ключевые понятия"]})
        for item in key_categories:
            item = _scrub_inline(_strip_meta_lines(_clean_field(str(item))))
            if not item:
                continue
            # Разбиваем "Понятие — объяснение" на bold + plain
            if " — " in item:
                term, _, expl = item.partition(" — ")
                nodes.append({"tag": "p", "children": [
                    {"tag": "b", "children": [term.strip()]},
                    f"  —  {expl.strip()}",
                ]})
            else:
                nodes.append({"tag": "p", "children": [item]})

    return await _telegraph_post(f"Аналитика: {title}", author, nodes, loop)


async def create_telegraph_questions(questions: list, title: str, author: str) -> str | None:
    """Публикует вопросы для обсуждения в Telegraph.
    v9: однофазная публикация — createPage сразу с финальным контентом и заголовком.
    Экономит 1 API-запрос на каждую публикацию.
    """
    author = _clean_meta_line(author) or "Автор не указан"
    title  = _clean_meta_line(title)  or "Без названия"

    if not isinstance(questions, list):
        return None

    green = [q for q in questions if str(q).startswith("🟢")]
    blue  = [q for q in questions if str(q).startswith("🔵")]
    other = [q for q in questions if not str(q).startswith(("🟢", "🔵"))]
    green = green + other

    if not green and not blue:
        return None

    loop = asyncio.get_running_loop()

    # BUG-C01: объединяем строки одного вопроса перед нумерацией,
    # чтобы многострочный вопрос не получал несколько "1." подряд.
    def _join_question_lines(raw: str) -> str:
        """Объединяет все строки одного вопроса в одну строку."""
        parts = [chunk.strip() for chunk in raw.splitlines() if chunk.strip()]
        return " ".join(parts)

    # ── Однофазная публикация (v9): сразу финальный контент ───────
    final_nodes: list = []

    if green:
        final_nodes.append({"tag": "h3", "children": ["Вопросы для размышления"]})
        for i, q in enumerate(green, 1):
            raw = re.sub(r"^🟢\s*", "", str(q)).strip()
            text = _scrub_inline(_strip_meta_lines(_clean_field(_join_question_lines(raw))))
            if not text or is_meta_garbage(text):
                continue
            final_nodes.append({"tag": "p", "children": [f"{i}. {text}"]})

    if blue:
        if final_nodes:
            final_nodes.append({"tag": "hr"})
        final_nodes.append({"tag": "h3", "children": ["Углубиться"]})
        for i, q in enumerate(blue, 1):
            raw = re.sub(r"^🔵\s*", "", str(q)).strip()
            text = _scrub_inline(_strip_meta_lines(_clean_field(_join_question_lines(raw))))
            if not text or is_meta_garbage(text):
                continue
            final_nodes.append({"tag": "p", "children": [f"{i}. {text}"]})

    if not final_nodes:
        return None

    page_url, err = await _create_telegraph_page_single(
        f"Вопросы: {title}", author, final_nodes, loop
    )
    if not page_url:
        logger.warning(f"Questions createPage failed: {err}")
        return None

    return page_url


async def create_telegraph_terms(terms_data: dict, title: str, author: str, yt_url: str = "") -> str | None:
    """Публикует блок «Термины» в Telegraph.
    terms_data — плоские массивы строк с разделителем ||.
    """
    author = _clean_meta_line(author or "") or "Автор не указан"
    title = _clean_meta_line(title or "") or "Без названия"

    td = terms_data or {}
    concepts = td.get("concepts", []) or []
    scripture = td.get("scripture", []) or []
    translations = td.get("translations", []) or []
    lexicon_notes = td.get("lexicon_notes", []) or []

    if not any([concepts, scripture, translations, lexicon_notes]):
        return None

    loop = asyncio.get_running_loop()
    nodes: list = []

    def _ts_nodes(times_str: str) -> list:
        if not times_str:
            return []
        parts = [t.strip() for t in re.split(r"[,\s•]+", times_str) if t.strip()]
        if not parts:
            return []
        # Лимит: максимум 3 таймкода на запись (начало темы + ключевой момент)
        # Валидация: пропускаем обрезанные/невалидные ("5", "41" без ":")
        valid_parts = []
        for t in parts:
            if ":" not in t:
                continue  # обрезанный или невалидный таймкод
            if time_to_seconds(t) is None:
                continue
            valid_parts.append(t)
            if len(valid_parts) >= 3:
                break
        if not valid_parts:
            return []
        children = []
        for i, ts in enumerate(valid_parts):
            secs = time_to_seconds(ts)
            if yt_url and secs is not None:
                href = get_youtube_timestamp_url(yt_url, secs)
                children.append({"tag": "a", "attrs": {"href": href}, "children": [ts]})
            else:
                children.append(ts)
            if i < len(valid_parts) - 1:
                children.append(", ")
        return children

    def _parse_line(line: str, n_fields: int) -> list[str]:
        parts = [p.strip() for p in str(line).split("||")]
        while len(parts) < n_fields:
            parts.append("")
        return parts[:n_fields]

    def _clean_text(s: str) -> str:
        s = _scrub_inline(_strip_meta_lines(_clean_field(s)))
        s = re.sub(r"[•·]", ", ", s)
        s = re.sub(r"\s*,\s*", ", ", s)
        # Убираем пробел между таймкодом и пунктуацией: "12:10 ." → "12:10."
        s = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+([.,!?\)])', r'\1\2', s)
        s = s.strip()
        if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', s):
            s = _fix_rtl_in_text(s)
        return s

    # #74: для lexicon_notes НЕ вызываем _clean_field (P0-баг #3 — добавляет точки к таймкодам).
    # Используем str().strip() вместо _clean_field.
    def _clean_text_safe(s: str) -> str:
        s = _scrub_inline(_strip_meta_lines(str(s).strip()))
        s = re.sub(r"[•·]", ", ", s)
        s = re.sub(r"\s*,\s*", ", ", s)
        s = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+([.,!?\)])', r'\1\2', s)
        s = s.strip()
        if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', s):
            s = _fix_rtl_in_text(s)
        return s

    first_section = True

    def _section_header(label: str):
        nonlocal first_section
        if not first_section:
            nodes.append({"tag": "hr"})
        first_section = False
        nodes.append({"tag": "h3", "children": [label]})

    # ── 🧩 Понятия ─────────────────────────────────────────────
    # Формат:
    # Термин || Объяснение || Почему важно: ... || Таймкоды: 5:09, 11:40
    if concepts:
        _section_header("🧩 Понятия")
        for raw in concepts:
            term, explanation, why, times_str = _parse_line(raw, 4)
            term = _clean_text(term)
            explanation = _clean_text(explanation)
            why = _clean_text(why)
            times_str = _clean_text_safe(times_str)  # #3: _clean_field добавлял точку к "1:23" → "1:23." → не кликабельно

            if not term:
                continue

            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [term]}]})

            # explanation и why — отдельными параграфами для читаемости
            if explanation:
                nodes.append({"tag": "p", "children": [explanation]})
            if why:
                why_clean = re.sub(r"^Почему важно:\s*", "", why, flags=re.IGNORECASE).strip()
                if why_clean:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [why_clean]}]})

            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    # ── 📖 Писание ─────────────────────────────────────────────
    # Формат:
    # Ссылка || Как используется || Ключевая фраза: ... || Таймкоды: ...
    if scripture:
        _section_header("📖 Писание")
        for raw in scripture:
            ref, use, phrase, times_str = _parse_line(raw, 4)
            ref = _clean_text(ref)
            use = _clean_text(use)
            phrase = _clean_text(phrase)
            times_str = _clean_text_safe(times_str)  # #3: таймкоды не должны получать точку

            if not ref:
                continue

            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [ref]}]})

            if use:
                nodes.append({"tag": "p", "children": [use]})

            if phrase:
                phrase = re.sub(r"^Ключевая фраза:\s*", "", phrase, flags=re.IGNORECASE).strip()
                if phrase:
                    if not phrase.startswith("«"):
                        phrase = f"«{phrase}»"
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [phrase]}]})

            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    # ── 🌍 Переводы ────────────────────────────────────────────
    # Формат:
    # Ссылка || ключевое слово/фраза || Русские переводы || Английские переводы || Наблюдение || Таймкоды
    if translations:
        _section_header("🌍 Переводы")
        for raw in translations:
            ref, focus, ru_raw, en_raw, observation, times_str = _parse_line(raw, 6)
            ref = _clean_text(ref)
            focus = _clean_text(focus)
            ru_raw = _clean_text(ru_raw)
            en_raw = _clean_text(en_raw)
            observation = _clean_text(observation)
            times_str = _clean_text_safe(times_str)  # #3: таймкоды не должны получать точку

            if not ref:
                continue

            header = ref
            if focus:
                header += f" — «{focus}»"
            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [header]}]})

            if ru_raw:
                ru_variants = [v.strip() for v in ru_raw.split(";") if v.strip()]
                for variant in ru_variants:
                    nodes.append({"tag": "p", "children": [variant]})

            if en_raw:
                en_variants = [v.strip() for v in en_raw.split(";") if v.strip()]
                for variant in en_variants:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [variant]}]})

            if observation:
                observation = re.sub(r"^Наблюдение:\s*", "", observation, flags=re.IGNORECASE).strip()
                if observation:
                    nodes.append({"tag": "p", "children": [observation]})

            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    # ── 🔤 Словарные заметки ───────────────────────────────────
    # Новый формат:
    # Ссылка на текст || русское ключевое слово/фраза || оригинальное слово || источник оригинала
    # || словарное пояснение || почему это важно здесь || таймкоды
    if lexicon_notes:
        _section_header("🔤 Словарные заметки")
        for raw in lexicon_notes:
            ref, ru_key, original_word, source_label, note, why, times_str, context_mention = _parse_line(raw, 8)
            # #74: используем _clean_text_safe (без _clean_field) для всех полей lexicon_notes
            ref = _clean_text_safe(ref)
            ru_key = _clean_text_safe(ru_key)
            original_word = _clean_text_safe(original_word)
            source_label = _clean_text_safe(source_label)
            note = _clean_text_safe(note)
            why = _clean_text_safe(why)
            times_str = _clean_text_safe(times_str)
            context_mention = _clean_text_safe(context_mention)

            if not ref and not original_word:
                continue

            # Заголовок
            header_parts = []
            if ref:
                header_parts.append(ref)
            if ru_key:
                header_parts.append(f"«{ru_key}»")
            header = " — ".join(header_parts) if header_parts else ""

            if header:
                nodes.append({"tag": "p", "children": [{"tag": "b", "children": [header]}]})

            # Оригинал и источник
            # dir="ltr" — принудительное выравнивание по левому краю для строк с ивритом/греческим,
            # чтобы браузер не переключался в RTL-режим из-за наличия RTL-символов
            # Расшифровка аббревиатур словарей для русскоязычного читателя
            _DICT_ABBREVS = {
                "HALOT": "HALOT (евр. словарь ВЗ)",
                "BDAG":  "BDAG (греч. словарь НЗ)",
                "BDB":   "BDB (евр. словарь)",
                "TWOT":  "TWOT (богосл. словарь ВЗ)",
                "TDNT":  "TDNT (богосл. словарь НЗ)",
                "NIDNTT": "NIDNTT (богосл. словарь НЗ)",
                "LXX":   "LXX (Септуагинта)",
                "NA28":  "NA28 (греч. текст НЗ)",
                "BHS":   "BHS (евр. текст ВЗ)",
            }
            def _expand_source(sl: str) -> str:
                """Расшифровывает аббревиатуры словарей в source_label."""
                if not sl:
                    return sl
                for abbr, full in _DICT_ABBREVS.items():
                    # Заменяем только если аббревиатура стоит как отдельное слово
                    import re as _re
                    sl = _re.sub(r'\b' + abbr + r'\b', full, sl)
                return sl

            if original_word:
                _source_display = _expand_source(source_label)
                if _source_display:
                    nodes.append({
                        "tag": "p",
                        "attrs": {"dir": "ltr"},
                        "children": [
                            {"tag": "b", "children": [original_word]},
                            f" — {_source_display}",
                        ],
                    })
                else:
                    nodes.append({
                        "tag": "p",
                        "attrs": {"dir": "ltr"},
                        "children": [{"tag": "b", "children": [original_word]}],
                    })

            # Словарное пояснение
            if note:
                nodes.append({"tag": "p", "children": [note]})

            # Почему важно
            if why:
                why = re.sub(r"^Почему важно:\s*", "", why, flags=re.IGNORECASE).strip()
                if why:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [why]}]})

            # Контраст / применение (поле 8) — без штампа «В материале:»
            if context_mention:
                nodes.append({
                    "tag": "p",
                    "children": [context_mention],
                })

            # Таймкоды
            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    if not nodes:
        return None

    return await _telegraph_post(f"Термины: {title}", author, nodes, loop)


# ════════════════════════════════════════════════════════════════════════════
# ARTICLE-LIKE СТРАНИЦЫ: Разбор материала + Размышление и применение
# ════════════════════════════════════════════════════════════════════════════

# ─── Helper: единый Gemini text-only запрос (без аудио) ──────────────────

async def _gemini_text_request(prompt: str, temperature: float = 0.4,
                                max_tokens: int = 8000) -> str | None:
    """Текстовый Gemini-запрос с multi-model fallback + thinking_level=high.

    v10 (3.5-flash era): GEMINI_MODEL=gemini-3.5-flash → fallback gemini-2.5-flash-lite.
    gemini-3.1-pro ПЛАТНАЯ — убрана из цепочки.
    На каждой модели: до 2 попыток с паузой при 503/overload. Time-budget 180s.
    """
    if not GEMINI_CLIENTS:
        return None

    # QUOTA-SMART FIX: умный 429 handling + time-budget
    #
    # При 429 (quota exhausted) — сразу к следующей модели.
    # Ключи делят квоту проекта, поэтому при 429 на одном ключе
    # все остальные ключи того же проекта тоже дадут 429.
    # Не тратим время на бесполезный перебор ключей.
    #
    # При 503/500 (overload) — retry с короткой паузой, может пройти.
    #
    # КАЧЕСТВО: ВСЕ задачи на gemini-3.5-flash (GEMINI_MODEL).
    # Никаких lite — fallback только на 2.5-flash-lite (свежая модель).
    #
    # Time-budget 180с — защита от зависания.
    import time as _time
    _start_time = _time.time()
    _TIME_BUDGET = 180  # секунд

    # ВСЕ на максимальном качестве — 3.5-flash
    # Резерв: 2.5-flash-lite (свежая модель, не lite по качеству)
    _models = [GEMINI_MODEL]
    if GEMINI_MODEL != "gemini-2.5-flash-lite":
        _models.append("gemini-2.5-flash-lite")

    def _is_internal_error(e: Exception) -> bool:
        s = str(e)
        return "500" in s or "INTERNAL" in s.upper()

    # Импортируем напрямую чтобы не идти через gemini_generate
    from core.globals import GEMINI_CLIENTS as _CLIENTS

    last_err = None
    for model_idx, model_name in enumerate(_models):
        # Time-budget check
        if _time.time() - _start_time > _TIME_BUDGET:
            logger.warning(
                "_gemini_text_request: TIME-BUDGET (%ds) исчерпан — fallback",
                _TIME_BUDGET,
            )
            break

        if model_idx > 0:
            logger.warning(
                "_gemini_text_request: переключаюсь на модель %s (#%d/%d)",
                model_name, model_idx + 1, len(_models),
            )

        # 2 попытки на модель — fast fail
        _max_attempts = 2
        _all_keys_quota = True  # True = все ключи дали 429

        for attempt in range(_max_attempts):
            # Time-budget check
            if _time.time() - _start_time > _TIME_BUDGET:
                break

            _client_err = None
            _got_response = False

            import core.globals
            start_idx = getattr(core.globals, "_current_client_idx", 0)
            
            for i in range(len(_CLIENTS)):
                idx = (start_idx + i) % len(_CLIENTS)
                client = _CLIENTS[idx]
                core.globals._current_client_idx = (idx + 1) % len(_CLIENTS)
                try:
                    resp = await client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=make_text_config_smart(
                            temperature=temperature,
                            max_output_tokens=max_tokens,
                            model_name=model_name,
                            thinking_level="high",
                        ),
                    )
                    try:
                        result = resp.text or ""
                    except (ValueError, AttributeError):
                        result = ""
                        if resp.candidates:
                            for part in resp.candidates[0].content.parts:
                                if not getattr(part, "thought", False) and getattr(part, "text", None):
                                    result = part.text
                                    break
                    if result:
                        # PATCH v2: thinking tokens logging
                        _meta = getattr(resp, 'usage_metadata', None)
                        if _meta:
                            logger.info(
                                "Gemini[%s] tokens: prompt=%s thoughts=%s output=%s",
                                model_name,
                                getattr(_meta, 'prompt_token_count', '?'),
                                getattr(_meta, 'thoughts_token_count', '?'),
                                getattr(_meta, 'candidates_token_count', '?'),
                            )
                        return result
                    logger.warning(
                        "_gemini_text_request[%s]: пустой ответ (finish=%s)",
                        model_name,
                        resp.candidates[0].finish_reason if resp.candidates else "?",
                    )
                    _got_response = True
                    _all_keys_quota = False
                    break
                except Exception as e:
                    _client_err = e
                    if is_quota_error(e):
                        # 429 — квота проекта, все ключи дадут тот же результат
                        # Не тратим время, сразу к следующей модели
                        continue
                    # 503/500 или другая ошибка
                    _all_keys_quota = False
                    if _is_internal_error(e) or is_overload_error(e):
                        continue
                    raise

            if _got_response:
                break  # пустой ответ — следующая модель

            # ГЛАВНЫЙ ФИКС: все ключи дали 429 — модель исчерпана,
            # ретраить бесполезно, идём к следующей модели сразу
            if _all_keys_quota:
                logger.warning(
                    "_gemini_text_request[%s]: квота 429 на всех ключах — следующая модель",
                    model_name,
                )
                last_err = _client_err
                break

            # 503/500 — пауза и retry
            last_err = _client_err
            if attempt < _max_attempts - 1:
                wait = 15
                logger.warning(
                    "_gemini_text_request[%s]: 503/500 (попытка %d/%d), жду %dс...",
                    model_name, attempt + 1, _max_attempts, wait,
                )
                await asyncio.sleep(wait)

    if last_err:
        elapsed = _time.time() - _start_time
        logger.warning(
            "_gemini_text_request: все модели исчерпаны (за %.1fs), последняя ошибка: %s",
            elapsed, str(last_err)[:200],
        )
    return None


def _parse_expanded_json(text: str, max_depth: int = 50, max_iterations: int = 500_000) -> tuple[list, list] | None:
    """Парсит JSON {outline, sections} из ответа Gemini. Возвращает (outline, sections) или None.
    Поддерживает восстановление обрезанного JSON (max_tokens hit).

    BUG-C03: защита от зависания — max_depth и max_iterations.
    """
    text = text.strip()
    # Убираем Unicode bidi-изоляторы которые Gemini иногда вставляет вокруг иврита/арабского
    # PATCH: keep \u200e/\u200f for RTL
    text = re.sub(r'[\u2066-\u2069\u202a-\u202e]', '', text)
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())

    # Попытка 1: чистый JSON
    try:
        data = json.loads(text)
        return data.get("outline", []), data.get("sections", [])
    except json.JSONDecodeError:
        pass

    # Попытка 2: найти первый { ... }
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e > s:
        try:
            data = json.loads(text[s:e+1])
            return data.get("outline", []), data.get("sections", [])
        except json.JSONDecodeError:
            # FIX 2026-05-21 #3 P1: Gemini иногда вставляет сырые \n внутри JSON-строк.
            # Экранируем их, как это делает _fix_json_newlines в telegraph.py.
            def _fix_json_newlines(s_in: str) -> str:
                out, in_str, i = [], False, 0
                while i < len(s_in):
                    c = s_in[i]
                    if c == '\\' and i + 1 < len(s_in):
                        out.append(c + s_in[i + 1]); i += 2; continue
                    if c == '"':
                        in_str = not in_str
                    if in_str and c == '\n':
                        out.append('\\n'); i += 1; continue
                    if in_str and c == '\r':
                        out.append('\\r'); i += 1; continue
                    if in_str and c == '\t':
                        out.append('\\t'); i += 1; continue
                    out.append(c); i += 1
                return ''.join(out)
            try:
                data = json.loads(_fix_json_newlines(text[s:e+1]))
                return data.get("outline", []), data.get("sections", [])
            except json.JSONDecodeError:
                pass

    # Попытка 3: Gemini обрезал JSON на полуслове (max_tokens) —
    # восстанавливаем sections до последнего полного объекта.
    if s != -1:
        chunk = text[s:]
        last_complete = -1
        depth = 0
        in_str = False
        escape = False
        iterations = 0
        for idx, ch in enumerate(chunk):
            # BUG-C03: лимит итераций — защита от зависания на больших входах
            iterations += 1
            if iterations > max_iterations:
                logger.warning(
                    f"_parse_expanded_json: превышен лимит итераций ({max_iterations}), прерываем."
                )
                break
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
                # BUG-C03: лимит глубины вложенности
                if depth > max_depth:
                    logger.warning(
                        f"_parse_expanded_json: превышена максимальная глубина ({max_depth}), прерываем."
                    )
                    break
            elif ch == "}":
                depth -= 1
                if depth == 1:  # закрыли один section (внутри sections-массива)
                    last_complete = idx
        if last_complete > 0:
            # Обрезаем до последнего полного section и закрываем массив + объект
            fixed = chunk[:last_complete+1] + "\n  ]\n}"
            try:
                data = json.loads(fixed)
                sections = data.get("sections", [])
                if sections:
                    outline = data.get("outline", [
                        {"title": s.get("title", ""), "time": s.get("time") or ""}
                        for s in sections
                    ])
                    logger.info(
                        f"_parse_expanded_json: восстановлен обрезанный JSON, "
                        f"sections={len(sections)}"
                    )
                    return outline, sections
            except json.JSONDecodeError:
                pass

    return None


async def _publish_expanded_page(
    sections: list,
    outline: list,
    page_title: str,
    tg_title: str,
    author: str,
    yt_url: str = "",
    include_toc: bool = True,
    rutube_url: str = "",
    vk_url: str = "",
    duration: int = 0,
    plain_scripture: bool = False,  # FIXED #127: пробрасывается в _section_to_nodes_v2
) -> str | None:
    """Публикует article-like страницу по модели Конспекта (createPage + editPage).
    Использует рекурсивное дробление при CONTENT_TOO_BIG — аналогично Synopsis."""
    loop = asyncio.get_running_loop()

    published_parts: list[tuple[list, str]] = []

    async def _publish_recursive(secs: list, depth: int = 0) -> bool:
        if not secs:
            return True

        body_nodes: list = []
        for sec_idx, sec in enumerate(secs):
            if sec_idx > 0:
                body_nodes.append({"tag": "hr"})
            body_nodes.extend(_section_to_nodes_v2(sec, yt_url=yt_url,
                                                    rutube_url=rutube_url, vk_url=vk_url,
                                                    duration=duration,
                                                    plain_scripture=plain_scripture))

        sec_offset = sum(len(p[0]) for p in published_parts)
        sec_range  = f"sections[{sec_offset}..{sec_offset + len(secs) - 1}]"
        logger.info(
            "Expanded publish depth=%d: %s (%d secs, %d nodes) — публикую...",
            depth, sec_range, len(secs), len(body_nodes),
        )

        # FIX 2026-05-21 P0 #14: Telegraph path генерируется из title ОДИН РАЗ при createPage
        # и editPage НЕ меняет URL. Старая логика создавала с " [draft]" → URL навсегда содержал
        # "-draft-DD-MM-N" (комментарий #93 был ошибочный — editPage не перезаписывает path).
        # Теперь сразу даём финальный title — URL будет чистым.
        page_url, err = await _create_telegraph_page_single(tg_title, author, body_nodes, loop)

        if page_url:
            logger.info("Expanded publish depth=%d: %s → OK %s", depth, sec_range, page_url)
            published_parts.append((secs, page_url))
            return True

        if err != "CONTENT_TOO_BIG":
            logger.warning("Expanded publish depth=%d: %s ошибка '%s' — fail", depth, sec_range, err)
            return False

        if len(secs) == 1:
            sec = secs[0]
            paragraphs = [p for p in sec.get('content', '').split('\n\n') if p.strip()]
            if len(paragraphs) > 5:
                mid_p = len(paragraphs) // 2
                s1 = {**sec, 'content': '\n\n'.join(paragraphs[:mid_p]),
                      'title': sec.get('title', '') + ' (ч.\u200b1)'}
                s2 = {**sec, 'content': '\n\n'.join(paragraphs[mid_p:]),
                      'title': sec.get('title', '') + ' (ч.\u200b2)'}
                logger.info(
                    "Expanded publish depth=%d: auto-split 1 section into 2 chunks",
                    depth,
                )
                if not await _publish_recursive([s1], depth + 1):
                    return False
                return await _publish_recursive([s2], depth + 1)
            logger.warning(
                "Expanded publish depth=%d: %s один section (%d nodes) нельзя разбить — fail",
                depth, sec_range, len(body_nodes),
            )
            return False

        mid = max(1, len(secs) // 2)
        logger.info(
            "Expanded publish depth=%d: %s CONTENT_TOO_BIG (%d nodes) → делю [%d]+[%d]",
            depth, sec_range, len(body_nodes), mid, len(secs) - mid,
        )
        if not await _publish_recursive(secs[:mid], depth + 1):
            return False
        return await _publish_recursive(secs[mid:], depth + 1)

    success = await _publish_recursive(sections)
    if not success or not published_parts:
        return None

    parts      = [p[0] for p in published_parts]
    parts_urls = [p[1] for p in published_parts]
    total      = len(parts)

    for i, (page_url, part_secs) in enumerate(zip(parts_urls, parts)):
        part_num   = i + 1
        part_title = f"{page_title} (часть {part_num}/{total})" if total > 1 else page_title

        final_nodes: list = []
        if include_toc and i == 0:
            final_nodes.extend(_build_toc_nodes_v2(outline, yt_url=yt_url, parts=parts, duration=duration))

        # Nav вверху для частей 2+ (не для первой — там TOC)
        if total > 1 and i > 0:
            final_nodes.extend(_build_nav_nodes_v2(i, total, parts_urls, leading_hr=False))
            final_nodes.append({"tag": "hr"})

        for sec_idx, sec in enumerate(part_secs):
            if sec_idx > 0:
                final_nodes.append({"tag": "hr"})
            final_nodes.extend(_section_to_nodes_v2(sec, yt_url=yt_url,
                                                     rutube_url=rutube_url, vk_url=vk_url,
                                                     page_title=part_title, duration=duration,
                                                     plain_scripture=plain_scripture))

        if total > 1:
            # _build_nav_nodes_v2 already includes its own leading <hr> — do NOT add extra one
            final_nodes.extend(_build_nav_nodes_v2(i, total, parts_urls))

        ok = False
        for retry_attempt in range(3):
            ok = await _edit_telegraph_page(page_url, part_title, author, final_nodes, loop)
            if ok:
                break
            logger.warning(
                "Expanded publish: editPage часть %d/%d попытка %d/3 failed для %s",
                part_num, total, retry_attempt + 1, page_url,
            )
            await asyncio.sleep(3)

        if ok:
            logger.info("Expanded publish: editPage часть %d/%d -> %s", part_num, total, page_url)
        else:
            logger.warning(
                "Expanded publish: editPage часть %d/%d окончательно failed, page content may stay outdated: %s",
                part_num, total, page_url,
            )

    return parts_urls[0]


async def _run_expanded_pipeline(
    label: str,
    prompt: str,
    page_prefix: str,
    tg_title: str,
    author: str,
    yt_url: str = "",
    include_toc: bool = True,
    fallback_fn=None,
    max_tokens: int = 8000,
    rutube_url: str = "",
    vk_url: str = "",
    duration: int = 0,
    plain_scripture: bool = False,  # FIXED #127: пробрасывается в _publish_expanded_page
) -> str | None:
    """Универсальный runner для article-like pages: Gemini -> парсинг -> публикация -> fallback."""

    try:
        logger.info("%s: запрос к Gemini", label)
        raw = await _gemini_text_request(prompt, temperature=0.4, max_tokens=max_tokens)
        if not raw:
            logger.warning("%s: Gemini вернул пустой ответ -- fallback", label)
            return await fallback_fn() if fallback_fn else None

        parsed = _parse_expanded_json(raw)

        if parsed is None:
            logger.warning("%s: сломанный JSON -- retry. Начало ответа: %.500s", label, raw)
            await asyncio.sleep(5)
            retry_prompt = (
                "Твой предыдущий ответ содержал сломанный JSON. "
                "Повтори строго: только валидный JSON {outline, sections}, "
                "без ```json, без текста до/после.\n\n" + prompt
            )
            _retry_tokens = min(max_tokens * 2, 65000)  # PATCH: escalate
            raw2 = await _gemini_text_request(retry_prompt, max_tokens=_retry_tokens)
            if raw2:
                parsed = _parse_expanded_json(raw2)
            if parsed is None:
                logger.warning("%s: retry тоже дал сломанный JSON -- fallback", label)
                return await fallback_fn() if fallback_fn else None
            logger.info("%s: retry успешен", label)

        outline, sections = parsed

        if not isinstance(sections, list):
            logger.warning("%s: sections не список -- fallback", label)
            return await fallback_fn() if fallback_fn else None

        sections = [s for s in sections if isinstance(s, dict) and (s.get("title") or s.get("content"))]
        if not sections:
            logger.warning("%s: sections пуст после валидации -- fallback", label)
            return await fallback_fn() if fallback_fn else None

        if (
            isinstance(outline, list)
            and outline
            and len(outline) == len(sections)
            and all(isinstance(oi, dict) for oi in outline)
        ):
            outline = [
                {"title": s.get("title", ""), "time": (oi.get("time") or s.get("time") or "")}
                for s, oi in zip(sections, outline)
            ]
        else:
            outline = [
                {"title": s.get("title", ""), "time": s.get("time") or ""}
                for s in sections
            ]

        try:
            est = _estimate_nodes_v2(sections, yt_url,
                                     rutube_url=rutube_url, vk_url=vk_url,
                                     duration=duration)
        except Exception:
            est = -1

        logger.info(
            "%s: sections=%d outline=%d nodes_estimate=%d",
            label, len(sections), len(outline), est,
        )

        if " — " in tg_title:
            _sep = ": "
        elif ":" in tg_title:
            _sep = " — "
        else:
            _sep = ": "

        url = await _publish_expanded_page(
            sections=sections,
            outline=outline,
            page_title=f"{page_prefix}{_sep}{tg_title}",
            tg_title=f"{page_prefix}{_sep}{tg_title}",
            author=author,
            yt_url=yt_url,
            include_toc=include_toc,
            rutube_url=rutube_url,
            vk_url=vk_url,
            duration=duration,
            plain_scripture=plain_scripture,
        )

        if url:
            return url

        logger.warning("%s: publish failed -- fallback", label)
        return await fallback_fn() if fallback_fn else None

    except Exception as e:
        # FALLBACK-DIAG: явно показываем, что мы СВАЛИЛИСЬ на compact-страницу.
        # Раньше юзер видел в Telegram '📖 Разбор материала', не подозревая что
        # это короткая Аналитика-fallback. Теперь это видно в логах громко.
        logger.error(
            "%s: ❌ UNEXPECTED ERROR (%s: %s) — переключаюсь на COMPACT-FALLBACK. "
            "Это значит, что в Telegram пользователь увидит УРЕЗАННУЮ версию страницы. "
            "Чините ошибку выше.",
            label, type(e).__name__, str(e)[:200],
        )
        logger.exception("%s: traceback ↓", label)
        return await fallback_fn() if fallback_fn else None




# Большие article-like промпты импортируются из prompts.py.
# Локальные дубликаты удалены, чтобы не было расхождения версий.

# ФУНКЦИЯ: create_telegraph_study_analysis
# ════════════════════════════════════════════════════════════════════════════

async def create_telegraph_study_analysis(
    ai_data: dict | None,
    title: str,
    author: str,
    yt_url: str = "",
    compact_fn=None,
    rutube_url: str = "",
    vk_url: str = "",
    synopsis_outline: list | None = None,
    duration: float = 0,
) -> str | None:
    """
    «Разбор материала» -- большая article-like страница.
    Объединяет и усиливает: аналитику, термины, Писание, богословскую глубину.
    compact_fn -- callable() без аргументов, fallback (напр. create_telegraph_analytics).
    """
    if not GEMINI_CLIENTS:
        logger.info("StudyAnalysis: нет GEMINI_CLIENTS -- fallback")
        return await compact_fn() if compact_fn else None

    _ai = ai_data or {}
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean  = _clean_meta_line(title  or "") or "Без названия"

    real_author  = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = normalize_title_text(_ai.get("real_title") or "") or title_clean
    prompt_title = title_case_fragment(prompt_title)
    # v10 FIX #18 (P3): нормализация пунктуации заголовка после Gemini.
    # «Свидетельство. Трус» → «Свидетельство: Трус»,  « - » → « — »
    import re as _re_p3
    prompt_title = _re_p3.sub(r'(?<=\w)\.\s+(?=[А-ЯA-Z])', ': ', prompt_title)
    prompt_title = _re_p3.sub(r'\s+-\s+', ' — ', prompt_title)

    _fmt = _ai.get("format", "other") or "other"

    # BUG-C02: единый источник duration — ai_data.duration приоритетен,
    # fallback на аргумент duration. Оба источника больше не расходятся.
    _duration_raw = _ai.get("duration") or 0
    try:
        _duration_from_ai = float(_duration_raw)
    except (ValueError, TypeError):
        _duration_from_ai = 0
    effective_duration = _duration_from_ai or float(duration or 0)

    _key_cats    = _ai.get("key_categories", []) or []
    key_cats_str = "\n".join(f"- {c}" for c in _key_cats[:12]) if _key_cats else "не указаны"

    _td           = _ai.get("terms_data") or {}
    concepts      = _td.get("concepts", [])      or []
    scripture     = _td.get("scripture", [])      or []
    translations  = _td.get("translations", [])   or []
    lexicon_notes = _td.get("lexicon_notes", [])  or []

    def _fmt_block(items, max_n=12):
        if not items:
            return "не указаны"
        return "\n".join(f"- {str(x).split('||')[0].strip()}" for x in items[:max_n])

    def _fmt_scripture(items, max_n=12):
        if not items:
            return "не указаны"
        parts = []
        for x in items[:max_n]:
            fields = str(x).split("||")
            ref = fields[0].strip() if fields else ""
            use = fields[1].strip() if len(fields) > 1 else ""
            parts.append(f"- {ref}: {use}" if use else f"- {ref}")
        return "\n".join(parts)

    _hm_study = _ai.get("hermeneutic_method") or "mixed"

    if synopsis_outline is not None:
        _outline_lines = "\n".join(
            f"  {i+1}. {s.get('title', '')} [{s.get('time', '')}]"
            for i, s in enumerate(synopsis_outline)
        )
        _synopsis_context = (
            f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\n"
            f"НЕ ПОВТОРЯЙ содержание этих разделов. Твоя задача — дать то, "
            f"чего в конспекте нет: исследовательскую глубину, богословский анализ, "
            f"разбор терминов и Писания."
        )
    else:
        _synopsis_context = ""

    prompt = STUDY_ANALYSIS_PROMPT.format(
        title=prompt_title,
        author=real_author,
        duration=format_timestamp(effective_duration) if effective_duration else "не указана",
        format_name=_fmt,
        hermeneutic_method=_hm_study,
        main_topic=(_ai.get("main_topic") or "").strip()[:800]        or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указано",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800]    or "не указан",
        key_categories=key_cats_str,
        timestamps=(_ai.get("timestamps") or "").strip()[:800]        or "не указаны",
        concepts=_fmt_block(concepts, 12),
        scripture=_fmt_scripture(scripture, 12),
        translations=_fmt_block(translations, 8),
        lexicon_notes=_fmt_block(lexicon_notes, 8),
        synopsis_context=_synopsis_context,
    )

    tg_title = prompt_title
    if real_author and real_author != "Автор не указан":
        tg_title = f"{tg_title} — {real_author}"
    tg_title = tg_title[:256]  # fix #12: Telegraph API ограничение 256 символов

    return await _run_expanded_pipeline(
        label="StudyAnalysis",
        prompt=prompt,
        page_prefix="📖 Разбор материала",
        tg_title=tg_title,
        author=author_clean,
        yt_url=yt_url,
        include_toc=True,
        fallback_fn=compact_fn,
        max_tokens=32000,   # PATCH: was 16K
        rutube_url=rutube_url,
        vk_url=vk_url,
        duration=int(effective_duration) if effective_duration else 0,
    )


# ════════════════════════════════════════════════════════════════════════════
# ФУНКЦИЯ: create_telegraph_reflection_application
# ════════════════════════════════════════════════════════════════════════════

async def create_telegraph_reflection_application(
    questions: list,
    title: str,
    author: str,
    ai_data: dict | None = None,
    duration: int = 0,
    yt_url: str = "",
    compact_fn=None,
    rutube_url: str = "",
    vk_url: str = "",
    synopsis_outline: list | None = None,
) -> str | None:
    """
    «Размышление и применение» -- большая пасторская article-like страница.
    Объединяет и усиливает: вопросы, применение, самоиспытание, молитву.
    compact_fn -- callable() без аргументов, fallback (напр. create_telegraph_questions).
    """
    if not GEMINI_CLIENTS:
        logger.info("ReflectionApplication: нет GEMINI_CLIENTS -- fallback")
        return await compact_fn() if compact_fn else None

    _ai          = ai_data or {}
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean  = _clean_meta_line(title  or "") or "Без названия"
    real_author  = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = normalize_title_text(_ai.get("real_title") or "") or title_clean
    prompt_title = title_case_fragment(prompt_title)  # единый стиль капитализации
    # v10 FIX #18 (P3): нормализация пунктуации (зеркало fix в create_telegraph_study_analysis)
    import re as _re_p3r
    prompt_title = _re_p3r.sub(r'(?<=\w)\.\s+(?=[А-ЯA-Z])', ': ', prompt_title)
    prompt_title = _re_p3r.sub(r'\s+-\s+', ' — ', prompt_title)

    _ts       = (_ai.get("timestamps") or "").strip()
    _ts_block = "\n".join(
        f"- {l.strip()}" for l in _ts.splitlines() if l.strip()
    ) or "не указаны"
    duration_str = format_timestamp(duration) if duration else "не указана"

    if isinstance(questions, list) and questions:
        green  = [q for q in questions if str(q).startswith("\U0001f7e2")]
        blue   = [q for q in questions if str(q).startswith("\U0001f535")]
        other  = [q for q in questions if not str(q).startswith(("\U0001f7e2", "\U0001f535"))]
        merged = (green + other + blue)[:15]
    else:
        merged = []

    if not merged:
        logger.info("ReflectionApplication: нет вопросов -- fallback")
        return await compact_fn() if compact_fn else None

    def _strip_marker(q: str) -> str:
        return re.sub(r"^[\U0001f7e2\U0001f535]\s*", "", str(q)).strip()

    questions_block = "\n".join(f"- {_strip_marker(q)}" for q in merged)

    _key_cats_list = (_ai.get("key_categories") or [])
    _key_cats_str  = "\n".join(f"- {c}" for c in _key_cats_list[:12]) if _key_cats_list else "не указаны"
    _hm            = (_ai.get("hermeneutic_method") or "mixed") or "mixed"

    # Формируем контекст конспекта для Reflection — чтобы не повторять его содержание
    if synopsis_outline is not None:
        _outline_lines = "\n".join(
            f"  {i+1}. {s.get('title', '')} [{s.get('time', '')}]"
            for i, s in enumerate(synopsis_outline)
        )
        _synopsis_context = (
            f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\n"
            f"НЕ ПОВТОРЯЙ содержание этих разделов. Твоя задача — дать то, "
            f"чего в конспекте нет: пасторское применение, самоиспытание, "
            f"молитвенный отклик и личные вопросы к читателю."
        )
    else:
        _synopsis_context = ""

    prompt = REFLECTION_APPLICATION_PROMPT.format(
        title=prompt_title,
        author=real_author,
        duration=duration_str,
        hermeneutic_method=_hm,
        main_topic=(_ai.get("main_topic") or "").strip()[:800]              or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указана",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800]          or "не указан",
        key_categories=_key_cats_str,
        questions_block=questions_block,
        timestamps_block=_ts_block,
        synopsis_context=_synopsis_context,
    )

    tg_title = (
        f"{prompt_title} — {real_author}"
        if real_author and real_author != "Автор не указан"
        else prompt_title
    )
    tg_title = tg_title[:256]  # fix #12: Telegraph API ограничение 256 символов

    return await _run_expanded_pipeline(
        label="ReflectionApplication",
        prompt=prompt,
        page_prefix="🙏 Размышление и применение",
        tg_title=tg_title,
        author=author_clean,
        yt_url=yt_url,
        include_toc=True,
        fallback_fn=compact_fn,
        max_tokens=24000,   # PATCH: was 14K
        rutube_url=rutube_url,
        vk_url=vk_url,
        duration=int(duration) if duration else 0,
        plain_scripture=True,  # FIXED #127: REFLECTION требует plain text для Scripture refs в скобках
    )


