#!/usr/bin/env python3
"""
Telegraph publisher — публикация с рекурсивным дроблением.
Извлечено из bot.py строки 4437–5288.
"""

from core.text_utils import (
    _strip_meta_lines,
    normalize_author_name,
    title_case_fragment,
    normalize_title_text,  # FIX telegraph
    join_title_author,  # R49: не дублировать имя автора
)
from converters.md_telegraph import (
    _section_to_nodes_v2,
    _build_toc_nodes_v2,
    _build_nav_nodes_v2,
    _split_sections_smart,
    _create_telegraph_page_single,
    _final_telegraph_polish,
    _postprocess_telegraph_nodes,
    _estimate_nodes_v2,  # FIX telegraph
    _extract_partial_sections,  # FIX telegraph
)
from converters.telegraph_nodes import (
    _HEADING_BOLD_STRIP_RE as _HEADING_BOLD_STRIP_RE,
    _clean_node_texts as _clean_node_texts,
    _clean_telegraph_nodes,
    _clean_text_block as _clean_text_block,
    _demote_paragraph_bold as _demote_paragraph_bold,
    _md_to_telegraph_nodes,
)

from core.core_utils import _md_parse_inline, time_to_seconds  # FIX: circular imports + AUDIT-FIX BUG 1
from core.url_utils import get_youtube_timestamp_url  # AUDIT R15: mini-outline ссылки на части 2+
from core.globals import (
    TELEGRAPH_TOKEN,
    GEMINI_CLIENTS,
    make_audio_config,
    is_quota_error,
    is_overload_error,
)
from services.gemini_router import gemini_generate
from core.database import GEMINI_MODEL  # FIX telegraph
from core.model_catalog import fallback_model
from core.telegraph_contract import (
    compose_telegraph_title,
    fit_telegraph_author_name,
    fit_telegraph_author_url,
    fit_telegraph_title,
)
from core.utils import format_timestamp  # FIX telegraph
from core.prompts import SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA, SYNOPSIS_VERBATIM_PROMPT  # FIX telegraph
from core.content_audit import audit_expanded_sections, format_content_audit_issues, has_content_audit_warnings
from services.gemini_analyze import _spawn_safe_delete  # FIX: cleanup uploaded Gemini files
from core.observability import alog_gemini_response, alog_gemini_run
from core.content_audit import get_content_audit_mode, should_abort_for_content_audit
from core.candidate_schema import expanded_page_response_schema
from core.reasoning_guidance import build_synopsis_reasoning_note
from core.synopsis_quality import (
    audit_synopsis_density,
    filter_organizational_announcement_sections,
    format_synopsis_quality_issues,
    get_synopsis_density_profile,
    should_retry_synopsis_density,
    synopsis_density_score,
)
from core.synopsis_timestamps import format_timestamp_issues, unresolved_timestamp_issues
from core.prompt_compactor import compact_prompt_for_generation
from core.generated_pages import aget_related_materials, extract_scripture_refs
from core.page_audit import audit_telegraph_page, format_audit_issues, should_abort_for_page_audit
from converters.md_telegraph import _build_related_materials_nodes
from services.telegraph_edit import (
    TelegraphEditResult,
    edit_telegraph_page_once,
    run_telegraph_edit_with_retry,
)

import asyncio
import json  # FIX telegraph
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


async def _edit_telegraph_page_classified(
    page_url: str,
    title: str,
    author: str,
    nodes: list,
    loop,
    *,
    author_url: str = "",
) -> TelegraphEditResult:
    """Edit one page with the same cleaning/audit contract as legacy editPage.

    The transport returns a structured outcome. Identical requests are retried
    only for transient network/FLOOD_WAIT/5xx failures; deterministic
    CONTENT_TOO_BIG is returned after the first call so the caller can rebuild a
    smaller payload immediately.
    """
    if not TELEGRAPH_TOKEN:
        return TelegraphEditResult(ok=False, error="no_token", retryable=False)

    prepared = _clean_telegraph_nodes(nodes)
    prepared = _postprocess_telegraph_nodes(prepared)
    prepared = _final_telegraph_polish(prepared)
    page_audit = audit_telegraph_page(title, prepared, page_type="edit")
    if page_audit:
        audit_summary = format_audit_issues(page_audit)
        logger.warning("Telegraph editPage audit for %s: %s", page_url, audit_summary)
        if should_abort_for_page_audit(page_audit):
            return TelegraphEditResult(
                ok=False,
                error="page_audit_strict: " + audit_summary[:300],
                retryable=False,
            )

    async def _operation() -> TelegraphEditResult:
        return await edit_telegraph_page_once(
            page_url,
            title,
            author,
            prepared,
            loop,
            author_url=author_url,
            token=TELEGRAPH_TOKEN,
        )

    result = await run_telegraph_edit_with_retry(_operation, max_attempts=3)
    if result.ok:
        try:
            from services.pdf_generator import invalidate_telegraph_url

            invalidate_telegraph_url(page_url)
        except Exception:
            pass
    return result


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


async def _telegraph_post(title: str, author: str, nodes: list, loop, author_url: str = "") -> str | None:
    """Публикует страницу в Telegraph, возвращает URL или None.
    При CONTENT_TOO_BIG автоматически разбивает на части и создаёт оглавление."""

    # A05 FIX: финальная полировка перед публикацией (orphaned **, h1/h2, bidi, пустые nodes)
    nodes = _final_telegraph_polish(nodes)
    nodes = _clean_telegraph_nodes(nodes)

    # Ядерная чистка: убираем Cocoon AI и мусор из всех нод и title
    _dirty_phrases = [
        "Cocoon AI Summary",
        "Cocoon AI",
        "cocoon ai summary",
        "cocoon ai",
        "AI Summary",
        "Content Summary",
        "Auto-generated summary",
        "Auto-Generated Summary",
    ]
    for _phrase in _dirty_phrases:
        title = title.replace(_phrase, "").strip()
    try:
        _raw = json.dumps(nodes, ensure_ascii=False)
        for _phrase in _dirty_phrases:
            _raw = _raw.replace(_phrase, "")
        nodes = json.loads(_raw)
        # Убираем пустые ноды после чистки
        nodes = [n for n in nodes if n and (not isinstance(n, dict) or n.get("children", [True]))]
    except Exception:
        pass

    token = TELEGRAPH_TOKEN
    if not token:
        logger.warning("TELEGRAPH_TOKEN не задан")
        return None

    async def _post_once(t, ns):
        t = fit_telegraph_title(t)
        safe_author = fit_telegraph_author_name(author)
        safe_author_url = fit_telegraph_author_url(author_url)
        logger.info(f"Telegraph: публикую '{t}' ({len(ns)} блоков)")
        last_err = ""
        # FIX AUDIT R4: FLOOD_WAIT-ретрай — burst-публикация Synopsis+Study+
        # Reflection упиралась в FLOOD_WAIT_5, и Terms/Analytics страница
        # терялась навсегда, хотя 5 секунд ожидания решали проблему.
        for _attempt in range(3):
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.post(
                        "https://api.telegra.ph/createPage",
                        json={
                            "access_token": token,
                            "title": t,
                            "author_name": safe_author,
                            "author_url": safe_author_url,
                            "content": ns,
                            "return_content": False,
                        },
                        timeout=30,
                    ),
                )
                status_code = getattr(resp, "status_code", None)
                try:
                    data = resp.json()
                except Exception as json_error:
                    last_err = f"HTTP {status_code}: invalid JSON ({json_error})"
                    if (status_code in {408, 425, 429} or bool(status_code and status_code >= 500)) and _attempt < 2:
                        logger.warning("Telegraph transient HTTP %s with invalid JSON — retry", status_code)
                        await asyncio.sleep(2**_attempt)
                        continue
                    return None, last_err
                if data.get("ok"):
                    return data["result"]["url"], None
                last_err = data.get("error", "")
                _has_flood_wait = bool(re.match(r"FLOOD_WAIT_(\d+)", str(last_err)))
                if (status_code in {408, 425, 429} or bool(status_code and status_code >= 500)) and not _has_flood_wait:
                    if _attempt < 2:
                        logger.warning("Telegraph transient HTTP %s: %s — retry", status_code, last_err)
                        await asyncio.sleep(2**_attempt)
                        continue
                    return None, last_err or f"HTTP {status_code}"
            except (requests.Timeout, requests.ConnectionError, OSError) as e:
                last_err = str(e)
                logger.warning(f"Telegraph transient error: {e}")
                if _attempt < 2:
                    await asyncio.sleep(2**_attempt)
                    continue
                return None, last_err
            except Exception as e:
                logger.warning(f"Telegraph ошибка: {e}")
                return None, str(e)
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

    # Recursive split has no arbitrary depth cap. Every CONTENT_TOO_BIG split
    # strictly reduces node count, so recursion terminates at a single unsplittable
    # node instead of dropping valid Synopsis tails after a fixed number of parts.
    async def _publish_chunk(chunk, label: str, depth: int = 0) -> bool:
        part_title = compose_telegraph_title(title, f" ({label})")
        part_url, part_err = await _post_once(part_title, chunk)
        if part_url:
            parts_urls.append((label, part_url))
            return True
        if part_err == "CONTENT_TOO_BIG" and len(chunk) > 1:
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
            title,
            len(parts_urls),
        )
    if len(parts_urls) == 1:
        return parts_urls[0][1]

    # Оглавление
    toc_nodes = [{"tag": "hr"}]
    for num, part_url in parts_urls:
        toc_nodes.append(
            {"tag": "p", "children": [{"tag": "a", "attrs": {"href": part_url}, "children": [f"Часть {num}"]}]}
        )
    toc_title = compose_telegraph_title(title, " — Содержание")
    toc_url, _ = await _post_once(toc_title, toc_nodes)
    return toc_url


async def create_telegraph_synopsis(
    mp3_path,
    title,
    performer,
    duration,
    url="",
    existing_audio_part=None,
    existing_client=None,
    ai_data=None,
    rutube_url="",
    vk_url="",
    source_lang: str = "",
):
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
        _fmt = (ai_data or {}).get("format", "other") or "other"
        _timestamps = (ai_data or {}).get("timestamps", "") or ""
        _real_author = normalize_author_name((ai_data or {}).get("real_author") or performer)
        author = _real_author or normalize_author_name(performer) or "Проповедь"
        _real_title = normalize_title_text((ai_data or {}).get("real_title") or "") or ""
        prompt_title = _real_title or normalize_title_text(title) or title
        prompt_title = title_case_fragment(prompt_title)
        tg_title = prompt_title
        if author and author != "Проповедь":
            tg_title = join_title_author(tg_title, author)
        tg_title = tg_title[:256]  # fix #12: Telegraph API ограничение 256 символов

        is_qa = _fmt == "qa"
        _ts_lines = [l.strip() for l in _timestamps.splitlines() if l.strip()]

        if is_qa and _ts_lines:
            timestamps_block = "\n".join(f"- {l}" for l in _ts_lines)
            _qa_prompt_template = SYNOPSIS_PROMPT_QA
            logger.info("Synopsis QA: единый промпт для всех вызовов")
            prompt = (
                SYNOPSIS_REASONING_FIRST_NOTE
                + "\n\n"
                + _qa_prompt_template.format(
                    title=prompt_title,
                    duration=duration_str,
                    timestamps_block=timestamps_block,
                )
            )
            _syn_profile = get_synopsis_density_profile(_duration)
            _syn_max_tokens = _syn_profile.max_tokens
            logger.info(
                f"Synopsis: mode=QA format={_fmt} questions={len(_ts_lines)} duration={int(_duration) // 60}min"
            )
        else:
            _hm = (ai_data or {}).get("hermeneutic_method") or "mixed"

            _syn_profile = get_synopsis_density_profile(_duration)
            _syn_sections = _syn_profile.sections
            _syn_section_len = _syn_profile.section_len
            _syn_total = _syn_profile.total_chars
            _syn_max_tokens = _syn_profile.max_tokens
            # AUDIT M6: расширили карту format → инструкция. Раньше для
            # sermon/lecture/topical/narrative/testimony в промт уходила
            # пустая строка, и Gemini угадывал голос (1-е лицо vs безличное) по заголовку.
            _format_note_map = {
                "interview": "Формат: ИНТЕРВЬЮ — беседа двух людей. Сохраняй диалоговую структуру: голос ведущего и голос гостя, не сводя к монологу.",
                "discussion": "Формат: ДИСКУССИЯ — несколько участников. Сохраняй обмен позициями, не сглаживай разногласия в единый нарратив.",
                "sermon": "Формат: ПРОПОВЕДЬ — гомилетический материал. Сохраняй пасторский голос проповедника и обращения к слушателям (вы/мы), сохраняй риторические повторы.",
                "lecture": "Формат: ЛЕКЦИЯ — академический материал. Безличный регистр, чёткая логическая структура, термины не упрощай.",
                "testimony": "Формат: СВИДЕТЕЛЬСТВО — личный нарратив. Сохраняй 1-е лицо рассказчика, эмоциональную тональность и хронологию событий.",
                "topical": "Формат: ТЕМАТИЧЕСКИЙ ОБЗОР — несколько подтем под общей рамкой. Каждая секция должна заметно отличаться по подтеме.",
                "narrative": "Формат: НАРРАТИВ — повествование с сюжетной аркой. Сохраняй порядок событий и ключевые повороты.",
                "qa": "Формат: Q&A — вопросы и ответы. Каждая секция = один вопрос + развёрнутый ответ; не объединяй разные вопросы в одну секцию.",
            }
            _format_note = _format_note_map.get(_fmt, "")
            # BP-01 FIX: передаём primary-анализ в конспект, чтобы
            # Gemini не изобретал материал заново, а достраивал уже известное.
            _main_topic = (ai_data.get("main_topic") or "").strip()[:600]
            _analysis_summ = (ai_data.get("analysis_summary") or "").strip()[:500]
            # argument_arc: available via ai_data if needed later
            _key_cats_raw = ai_data.get("key_categories") or []
            _key_cats_str2 = (
                ", ".join(_key_cats_raw[:6]) if isinstance(_key_cats_raw, list) else str(_key_cats_raw)[:200]
            )
            # Таймкоды из первичного анализа — как якорная сетка
            _ts_raw = ai_data.get("timestamps") or []
            _ts_anchor = ""
            if isinstance(_ts_raw, list) and _ts_raw:
                _ts_source = _ts_raw[:24]
                # Preserve the final anchor even when the primary analysis has many timestamps.
                if len(_ts_raw) > 24 and _ts_raw[-1] not in _ts_source:
                    _ts_source = _ts_source[:-1] + [_ts_raw[-1]]
                _ts_lines = [f"{t.get('time', '?')} — {t.get('topic', '')}" for t in _ts_source if isinstance(t, dict)]
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
                SYNOPSIS_VERBATIM_PROMPT if _synopsis_verbatim_prompt_enabled() else SYNOPSIS_PROMPT_V2
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
                SYNOPSIS_REASONING_FIRST_NOTE
                + "\n"
                + _format_note
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
                _fmt,
                _hm,
                int(_duration) // 60,
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
                _compacted_prompt.original_chars,
                _compacted_prompt.compacted_chars,
                _compacted_prompt.removed_lines,
            )
        prompt = _compacted_prompt.text

        loop = asyncio.get_running_loop()
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
                        type(_schema_err).__name__,
                        str(_schema_err)[:180],
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
                client,
                GEMINI_MODEL,
                [audio, prompt_text],
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
                    existing_client,
                    GEMINI_MODEL,
                    [existing_audio_part, prompt],
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

            response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis, model_name=GEMINI_MODEL)

            # 429 / 503 на основной модели → только ЯВНО разрешённый fallback.
            # Политика проекта: cross-model downgrade по умолчанию выключен
            # (MP3BOT_FALLBACK_MODEL пуст), иначе деградация качества становится
            # невидимой. Ротацию клиентов/ключей делает сам gemini_generate.
            _fallback_model = fallback_model()
            if (
                response is None
                and _fallback_model
                and _fallback_model.casefold() != str(GEMINI_MODEL or "").casefold()
            ):
                logger.warning(
                    "Synopsis: основная модель недоступна — пробую разрешённый fallback %s (MP3BOT_FALLBACK_MODEL)",
                    _fallback_model,
                )
                try:

                    async def _call_fallback(client):
                        audio = await _upload(client)
                        return await _generate_synopsis_content(
                            client,
                            _fallback_model,
                            [audio, prompt],
                            max_tokens=_syn_max_tokens,
                            use_schema=not _transcript_attached,
                        )

                    response = await gemini_generate(
                        GEMINI_CLIENTS,
                        _call_fallback,
                        model_name=_fallback_model,
                    )
                    logger.info("Synopsis: fallback модель успешна")
                except Exception as _fb_err:
                    logger.warning(f"Synopsis fallback failed: {_fb_err}")
            elif response is None:
                logger.warning(
                    "Synopsis: основная модель недоступна; cross-model fallback "
                    "выключен политикой (MP3BOT_FALLBACK_MODEL) — апстрим честно "
                    "деградирует, модель не подменяется",
                )

        # ── Извлекаем текст ответа ────────────────────────────
        synopsis_text = _extract_response_text(response)
        _obs_video_id = str(getattr(mp3_path, "stem", ""))
        if not synopsis_text:
            await alog_gemini_run(
                task="synopsis",
                video_id=_obs_video_id,
                model=GEMINI_MODEL,
                thinking_level="high",
                json_valid=False,
                error="empty_response",
            )
            return None, None

        # ── Парсим JSON (с retry при сломанном JSON) ─────────

        def _try_parse_synopsis_json(text: str) -> tuple[list, list] | None:
            """Возвращает (outline, sections) или None если не распарсилось."""
            try:
                clean = text.strip()
                clean = re.sub(r"^```[a-z]*\s*", "", clean)
                clean = re.sub(r"\s*```$", "", clean).strip()
                s = clean.find("{")
                e = clean.rfind("}")
                if s == -1 or e <= s:
                    return None
                chunk = clean[s : e + 1]

                # Защита: Gemini иногда вставляет реальные переносы строк
                # внутри JSON-строк — это невалидный JSON. Экранируем их.
                def _fix_json_newlines(s: str) -> str:
                    result, in_str, i = [], False, 0
                    while i < len(s):
                        c = s[i]
                        if c == "\\" and i + 1 < len(s):
                            result.append(c + s[i + 1])
                            i += 2
                            continue
                        if c == '"':
                            in_str = not in_str
                        if in_str and c == "\n":
                            result.append("\\n")
                            i += 1
                            continue
                        if in_str and c == "\r":
                            result.append("\\r")
                            i += 1
                            continue
                        if in_str and c == "\t":
                            result.append("\\t")
                            i += 1
                            continue
                        result.append(c)
                        i += 1
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
        outline: list = []

        parsed = _try_parse_synopsis_json(synopsis_text)
        if parsed is not None:
            outline, sections = parsed
            logger.info("Synopsis: JSON распарсен успешно")
            await alog_gemini_response(
                response=response,
                task="synopsis",
                video_id=_obs_video_id,
                model=GEMINI_MODEL,
                thinking_level="high",
                json_valid=True,
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
                            existing_client,
                            GEMINI_MODEL,
                            [existing_audio_part, retry_prompt],
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

                    retry_response = await gemini_generate(
                        GEMINI_CLIENTS, _call_synopsis_retry, model_name=GEMINI_MODEL
                    )
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
                        "{\n"
                        '  "outline": [{"title": "...", "time": "0:00"}, ...],\n'
                        '  "sections": [{"title": "...", "time": "0:00", "content": "..."}, ...]\n'
                        "}"
                    )
                    simple_response = None
                    try:
                        if existing_audio_part is not None and existing_client is not None:
                            try:
                                simple_response = await _generate_synopsis_content(
                                    existing_client,
                                    GEMINI_MODEL,
                                    [existing_audio_part, simple_prompt],
                                    max_tokens=32000,
                                    use_schema=True,
                                )
                            except Exception:
                                simple_response = None
                        if simple_response is None:

                            async def _call_synopsis_simple(client):
                                return await _synopsis_with_client(client, _upload_retry, simple_prompt, loop)

                            simple_response = await gemini_generate(
                                GEMINI_CLIENTS, _call_synopsis_simple, model_name=GEMINI_MODEL
                            )
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
            logger.warning(f"Synopsis v2: удалено пустых sections={removed_empty} из {total_before_filter}")

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
        if _syn_quality_issues:
            logger.warning(
                "Synopsis v2: density/coverage audit: %s", format_synopsis_quality_issues(_syn_quality_issues)
            )

        if (
            _syn_quality_issues
            and _synopsis_density_retry_enabled()
            and should_retry_synopsis_density(_syn_quality_issues, _duration)
            and ((existing_audio_part is not None and existing_client is not None) or _transcript_attached)
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
                    _retry_sections = [
                        s
                        for s in _retry_sections
                        if isinstance(s, dict) and ((s.get("content") or "").strip() or s.get("blocks"))
                    ]
                    _retry_sections, _retry_outline, _retry_audit = audit_expanded_sections(
                        _retry_sections,
                        _retry_outline if isinstance(_retry_outline, list) else [],
                        label="SynopsisDensityRetry",
                        expected_author=author,
                    )
                    if _retry_audit and _audit_mode != "off":
                        _retry_log = logger.warning if has_content_audit_warnings(_retry_audit) else logger.info
                        _retry_log(
                            "Synopsis v2: density retry content audit: %s",
                            format_content_audit_issues(_retry_audit),
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
                            len(sections),
                            _old_score,
                            _new_score,
                            _old_issue_count,
                            _new_issue_count,
                        )
                    else:
                        logger.info(
                            "Synopsis v2: density retry rejected — not denser, not enough issue improvement, or too much thinner (score=%d->%d issues=%d->%d)",
                            _old_score,
                            _new_score,
                            _old_issue_count,
                            _new_issue_count,
                        )
            except Exception as _density_retry_err:
                logger.warning("Synopsis v2: density retry failed non-fatally: %s", str(_density_retry_err)[:180])

        # Final fixed-point verification runs after the density retry decision,
        # because the accepted retry becomes the actual publication payload.
        _ts_order_issues = unresolved_timestamp_issues(sections)
        if _ts_order_issues:
            logger.warning(
                "Synopsis v2: unresolved timestamp order after reconciliation: %s",
                format_timestamp_issues(_ts_order_issues),
            )

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
        # sections is the authoritative timeline after deterministic audit.
        # The Gemini outline may fill a missing time only; it can never overwrite
        # a reconciled section start.
        if outline and len(outline) == len(sections):
            merged = []
            for sec, oi in zip(sections, outline):
                merged.append(
                    {
                        "title": sec.get("title", ""),
                        "time": (sec.get("time") or oi.get("time") or ""),
                    }
                )
            outline = merged
        else:
            # Длины не совпадают — полностью перестраиваем из sections
            outline = [{"title": sec.get("title", ""), "time": sec.get("time", "")} for sec in sections]
            outline_rebuilt = True

        # ── Диагностика ───────────────────────────────────────
        try:
            total_nodes_estimate = _estimate_nodes_v2(
                sections,
                url,
                rutube_url=rutube_url,
                vk_url=vk_url,
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
            question_like_titles = sum(1 for s in sections if ((s.get("title") or "").strip().endswith("?")))
            logger.info(f"Synopsis QA: question_like_titles={question_like_titles}/{len(sections)}")
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
            logger.warning(f"Synopsis v2: мало sections ({len(sections)}) для {mins}мин — возможно неполный конспект")

        if _duration > 600 and total_nodes_estimate != -1 and total_nodes_estimate < 20:
            logger.warning(
                f"Synopsis v2: мало nodes ({total_nodes_estimate}) для {mins}мин — конспект может быть слишком коротким"
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
            sec_range = f"sections[{sec_offset}..{sec_offset + len(secs) - 1}]"
            part_nodes = []
            for sec_idx, sec in enumerate(secs):
                if sec_idx > 0:
                    part_nodes.append({"tag": "hr"})
                part_nodes.extend(
                    _section_to_nodes_v2(sec, yt_url=url, rutube_url=rutube_url, vk_url=vk_url, duration=duration)
                )

            logger.info(
                f"Synopsis v2 depth={depth}: {sec_range} ({len(secs)} secs, {len(part_nodes)} nodes) — публикую..."
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
                f"({len(part_nodes)} nodes) → делю [{mid}]+[{len(secs) - mid}]"
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

        # Try merging the final two parts using the exact API outcome. A
        # deterministic overflow simply means the current split is already the
        # compact safe layout; transient failures may retry inside the helper.
        if len(published_parts) >= 2:
            await asyncio.sleep(2)
        while len(published_parts) >= 2:
            _prev_secs, _prev_url = published_parts[-2]
            _last_secs, _last_url = published_parts[-1]
            _combined_secs = _prev_secs + _last_secs
            _combined_nodes = []
            for _cidx, _csec in enumerate(_combined_secs):
                if _cidx > 0:
                    _combined_nodes.append({"tag": "hr"})
                _combined_nodes.extend(
                    _section_to_nodes_v2(
                        _csec,
                        yt_url=url,
                        rutube_url=rutube_url,
                        vk_url=vk_url,
                        duration=duration,
                    )
                )
            _merged_result = await _edit_telegraph_page_classified(_prev_url, tg_title, author, _combined_nodes, loop)
            if not _merged_result.ok:
                if _merged_result.error != "CONTENT_TOO_BIG":
                    logger.warning(
                        "Synopsis v2: merge edit failed for %s: %s",
                        _prev_url,
                        _merged_result.error,
                    )
                break
            logger.info(
                "Synopsis v2: слил хвостовую часть (%d secs) в предыдущую — частей стало %d вместо %d",
                len(_last_secs),
                len(published_parts) - 1,
                len(published_parts),
            )
            published_parts[-2] = (_combined_secs, _prev_url)
            published_parts.pop()

        total = len(published_parts)
        parts = [p[0] for p in published_parts]
        parts_urls = [p[1] for p in published_parts]

        logger.info(f"Synopsis v2: опубликовано {total} частей")

        # ── Переименовываем и добавляем TOC + навигацию ──────
        # V3-P16: пауза перед editPage — Telegraph rate limit после быстрой публикации.
        # Без паузы editPage падает 3/3 при >50 nodes (0.7с между create и edit).

        async def _build_part_nodes_edit(
            i: int, part_secs: list, part_title: str, *, include_outline: bool = True
        ) -> list:
            _nodes: list = []
            if include_outline:
                if i == 0:
                    _nodes.extend(_build_toc_nodes_v2(outline, yt_url=url, parts=parts))
                elif total > 1:
                    # FIX BUG-5: add mini-outline for parts 2+ so reader sees section titles
                    # AUDIT R16: сквозная нумерация от глобального индекса секции.
                    _sec_offset_for_part = sum(len(p) for p in parts[:i])
                    _mini_outline = []
                    for _ps_idx, _ps in enumerate(part_secs):
                        _ps_title = _ps.get("title", "")
                        _ps_time = (_ps.get("time") or "").strip()
                        if not _ps_title:
                            continue
                        _global_num = _sec_offset_for_part + _ps_idx + 1
                        _children = [{"tag": "b", "children": [f"{_global_num}.\xa0"]}]
                        _children.extend(_md_parse_inline(_ps_title))
                        if _ps_time:
                            _secs = time_to_seconds(_ps_time)
                            if _secs is not None and url:
                                _children.append(" — ⏱\xa0")
                                _children.append(
                                    {
                                        "tag": "a",
                                        "attrs": {"href": get_youtube_timestamp_url(url, _secs)},
                                        "children": [_ps_time],
                                    }
                                )
                            else:
                                _children.append(f" — ⏱ {_ps_time}")
                        _mini_outline.append({"tag": "p", "children": _children})
                    if _mini_outline:
                        _nodes.extend(_mini_outline)
                        _nodes.append({"tag": "hr"})
            for sec_idx, sec in enumerate(part_secs):
                if sec_idx > 0:
                    _nodes.append({"tag": "hr"})
                _nodes.extend(
                    _section_to_nodes_v2(
                        sec, yt_url=url, rutube_url=rutube_url, vk_url=vk_url, page_title=part_title, duration=duration
                    )
                )
            if total > 1:
                _nodes.extend(_build_nav_nodes_v2(i, total, parts_urls))

            # FEATURE 2026-06-11: Блок «Читать также» в самом конце материала
            if i == total - 1:
                try:
                    _video_id = str(getattr(mp3_path, "stem", ""))
                    _scripture_refs = extract_scripture_refs(ai_data)
                    _related = await aget_related_materials(
                        author=author,
                        scripture=(_scripture_refs[0] if _scripture_refs else ""),
                        exclude_video_id=_video_id,
                        limit=3,
                    )
                    if _related:
                        _nodes.extend(_build_related_materials_nodes(_related))
                except Exception as _rel_err:
                    logger.debug("Related materials block skip: %s", _rel_err)
            return _nodes

        await asyncio.sleep(2)
        for i, (part_secs, page_url) in enumerate(published_parts):
            part_num = i + 1
            # FIX AUDIT R4: суффикс [N/M] не должен выталкивать итоговый
            # заголовок за лимит Telegraph 256 — editPage падал 3/3, и части
            # оставались без TOC/навигации.
            if total == 1:
                part_title = tg_title
            else:
                _sfx = f" [{part_num}/{total}]"
                part_title = tg_title[: 256 - len(_sfx)] + _sfx

            nodes_edit = await _build_part_nodes_edit(i, part_secs, part_title)
            edit_result = await _edit_telegraph_page_classified(page_url, part_title, author, nodes_edit, loop)
            ok = edit_result.ok

            # CONTENT_TOO_BIG is deterministic for an identical payload. Do not
            # spend 3/6/12 seconds repeating it; rebuild once without optional
            # outline nodes while preserving all section content and navigation.
            if not ok and edit_result.error == "CONTENT_TOO_BIG":
                logger.warning(
                    "Synopsis v2: editPage часть %d/%d CONTENT_TOO_BIG — "
                    "сразу пересобираю без оглавления (контент сохраняем)",
                    part_num,
                    total,
                )
                nodes_edit = await _build_part_nodes_edit(i, part_secs, part_title, include_outline=False)
                edit_result = await _edit_telegraph_page_classified(page_url, part_title, author, nodes_edit, loop)
                ok = edit_result.ok

            if ok:
                logger.info(f"Synopsis v2: editPage часть {part_num}/{total} → {page_url}")
            else:
                logger.warning(
                    "Synopsis v2: editPage часть %d/%d окончательно failed (%s), page content may stay outdated: %s",
                    part_num,
                    total,
                    edit_result.error,
                    page_url,
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
