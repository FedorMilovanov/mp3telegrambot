#!/usr/bin/env python3
"""
Telegraph Pages — публикация расширенных страниц.
Analytics, Questions, Terms, Study Analysis, Reflection.
Извлечено из bot.py строки 5289–7804.
"""
from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS, gemini_generate,
    is_overload_error, is_quota_error, make_text_config_smart,
)
from core.database import GEMINI_MODEL
from core.text_utils import (
    _scrub_inline, normalize_author_name,
    _clean_field, _clean_meta_line,
    _strip_meta_lines, is_meta_garbage,
    normalize_title_text, title_case_fragment,
    join_title_author,
)
from converters.md_telegraph import (
    _build_toc_nodes_v2, _build_nav_nodes_v2,
    _section_to_nodes_v2,
    _create_telegraph_page_single,
    _edit_telegraph_page,
    _estimate_nodes_v2,
)
from services.telegraph import _telegraph_post
from core.core_utils import _fix_rtl_in_text, _md_parse_inline
from core.json_parser import time_to_seconds
from core.url_utils import get_youtube_timestamp_url
from core.utils import format_timestamp
from core.prompts import REFLECTION_APPLICATION_PROMPT
from services.study_synthesis_policy import TEACHERLY_STUDY_PROMPT as STUDY_ANALYSIS_PROMPT
from core.observability import alog_gemini_response, alog_gemini_run
from core.source_packs import get_source_pack_for_ai_data
from core.candidate_schema import expanded_page_response_schema
from core.candidate_schema import combined_expanded_pages_response_schema
from core.analysis_profiles import get_expanded_analysis_profile
from core.reasoning_guidance import build_reasoning_first_block
from core.adaptive_generation import get_adaptive_text_generation_params
from core.prompt_compactor import compact_prompt_for_generation
from core.question_quality import normalize_question_text, question_is_usable, question_key
from core.content_audit import audit_expanded_sections, format_content_audit_issues, has_content_audit_warnings
from core.content_audit import get_content_audit_mode, should_abort_for_content_audit
from core.generated_pages import aget_related_materials, extract_scripture_refs
from converters.md_telegraph import _build_related_materials_nodes
from services import gemini_capacity_control as capacity_control

import asyncio
from typing import Optional, List, Tuple
import json
from dataclasses import dataclass
import logging
import re
import time
import os

try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class CombinedStudyReflectionResult:
    study_url: str | None = None
    reflection_url: str | None = None
    study_page_type: str = "study"
    reflection_page_type: str = "reflection"
    mode: str = "combined"


def _expanded_structured_output_enabled() -> bool:
    """Opt-out flag for Study/Reflection structured {outline, sections}."""
    return (os.getenv("EXPANDED_PAGES_STRUCTURED", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def combined_study_reflection_enabled() -> bool:
    """Opt-in only: quality-first default keeps Study and Reflection separate.

    Combined generation saves one Gemini request, but it is a larger multi-task
    prompt. Keep disabled unless explicitly enabled for quota experiments.
    """
    return (os.getenv("COMBINE_STUDY_REFLECTION", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


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
        nodes.append({"tag": "p", "children": _md_parse_inline(analysis_summary)})

    if argument_arc:
        if nodes:
            nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": ["📈 Ход аргументации"]})
        nodes.append({"tag": "p", "children": _md_parse_inline(argument_arc)})

    if key_categories:
        if nodes:
            nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": ["🗂 Ключевые понятия"]})
        for item in key_categories:
            item = _scrub_inline(_strip_meta_lines(_clean_field(str(item))))
            if not item:
                continue
            if " — " in item:
                term, _, expl = item.partition(" — ")
                nodes.append({"tag": "p", "children": [
                    {"tag": "b", "children": [term.strip()]},
                    f"  —  {expl.strip()}",
                ]})
            else:
                nodes.append({"tag": "p", "children": [item]})

    return await _telegraph_post(f"Аналитика: {title}", author, nodes, loop)


def _normalize_question_items(questions: list, *, max_items: int = 15) -> list[str]:
    """Normalize generated reflection questions before publishing/reusing them."""
    if not isinstance(questions, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in questions:
        text = str(raw or "").replace("\r", " ").strip()
        if not text:
            continue
        marker = "🟢"
        if text.startswith("🔵"):
            marker = "🔵"
        text = _scrub_inline(_strip_meta_lines(_clean_field(normalize_question_text(text))))
        if not text or is_meta_garbage(text) or not question_is_usable(text):
            continue
        key = question_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(f"{marker} {text}")
        if len(out) >= max_items:
            break
    return out


async def create_telegraph_questions(questions: list, title: str, author: str) -> str | None:
    """Публикует вопросы для обсуждения в Telegraph."""
    author = _clean_meta_line(author) or "Автор не указан"
    title  = _clean_meta_line(title)  or "Без названия"

    questions = _normalize_question_items(questions, max_items=20)
    if not questions:
        return None

    green = [q for q in questions if str(q).startswith("🟢")]
    blue  = [q for q in questions if str(q).startswith("🔵")]
    if not green and not blue:
        return None
    loop = asyncio.get_running_loop()

    def _join_question_lines(raw: str) -> str:
        parts = [chunk.strip() for chunk in raw.splitlines() if chunk.strip()]
        return " ".join(parts)

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
    """Публикует блок «Термины» в Telegraph."""
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
        valid_parts = []
        for t in parts:
            if ":" not in t:
                continue
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
        s = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+([.,!?\)])', r'\1\2', s)
        s = s.strip()
        if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', s):
            s = _fix_rtl_in_text(s)
        return s

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

    if concepts:
        _section_header("🧩 Понятия")
        for raw in concepts:
            term, explanation, why, times_str = _parse_line(raw, 4)
            term = _clean_text(term)
            explanation = _clean_text(explanation)
            why = _clean_text(why)
            times_str = _clean_text_safe(times_str)
            if not term:
                continue
            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [term]}]})
            if explanation:
                nodes.append({"tag": "p", "children": [explanation]})
            if why:
                why_clean = re.sub(r"^Почему важно:\s*", "", why, flags=re.IGNORECASE).strip()
                if why_clean:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [why_clean]}]})
            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    if scripture:
        _section_header("📖 Писание")
        for raw in scripture:
            ref, use, phrase, times_str = _parse_line(raw, 4)
            ref = _clean_text(ref)
            use = _clean_text(use)
            phrase = _clean_text(phrase)
            times_str = _clean_text_safe(times_str)
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

    if translations:
        _section_header("🌍 Переводы")
        for raw in translations:
            ref, focus, ru_raw, en_raw, observation, times_str = _parse_line(raw, 6)
            ref = _clean_text(ref)
            focus = _clean_text(focus)
            ru_raw = _clean_text(ru_raw)
            en_raw = _clean_text(en_raw)
            observation = _clean_text(observation)
            times_str = _clean_text_safe(times_str)
            if not ref:
                continue
            header = ref
            if focus:
                header += f" — «{focus}»"
            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [header]}]})
            if ru_raw:
                for variant in [v.strip() for v in ru_raw.split(";") if v.strip()]:
                    nodes.append({"tag": "p", "children": [variant]})
            if en_raw:
                for variant in [v.strip() for v in en_raw.split(";") if v.strip()]:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [variant]}]})
            if observation:
                observation = re.sub(r"^Наблюдение:\s*", "", observation, flags=re.IGNORECASE).strip()
                if observation:
                    nodes.append({"tag": "p", "children": [observation]})
            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    if lexicon_notes:
        _section_header("🔤 Словарные заметки")
        for raw in lexicon_notes:
            ref, ru_key, original_word, source_label, note, why, times_str, context_mention = _parse_line(raw, 8)
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
            header_parts = []
            if ref:
                header_parts.append(ref)
            if ru_key:
                header_parts.append(f"«{ru_key}»")
            header = " — ".join(header_parts) if header_parts else ""
            if header:
                nodes.append({"tag": "p", "children": [{"tag": "b", "children": [header]}]})
            _DICT_ABBREVS = {
                "HALOT": "HALOT (евр. словарь ВЗ)", "BDAG": "BDAG (греч. словарь НЗ)",
                "BDB": "BDB (евр. словарь ВЗ)", "TWOT": "TWOT (богосл. словарь ВЗ)",
                "TDNT": "TDNT (богосл. словарь НЗ)", "NIDNTT": "NIDNTT (богосл. словарь НЗ)",
                "NIDOTTE": "NIDOTTE (богосл. словарь ВЗ)", "LXX": "LXX (Септуагинта)",
                "NA28": "NA28 (греч. текст НЗ)", "BHS": "BHS (евр. текст ВЗ)",
            }
            def _expand_source(sl: str, _abbrevs=_DICT_ABBREVS) -> str:
                if not sl:
                    return sl
                for abbr, full in _abbrevs.items():
                    sl = re.sub(r'\b' + abbr + r'\b', full, sl)
                return sl
            if original_word:
                _source_display = _expand_source(source_label)
                if _source_display:
                    nodes.append({"tag": "p", "attrs": {"dir": "ltr"}, "children": [
                        {"tag": "b", "children": [original_word]}, f" — {_source_display}"]})
                else:
                    nodes.append({"tag": "p", "attrs": {"dir": "ltr"}, "children": [
                        {"tag": "b", "children": [original_word]}]})
            if note:
                nodes.append({"tag": "p", "children": [note]})
            if why:
                why = re.sub(r"^Почему важно:\s*", "", why, flags=re.IGNORECASE).strip()
                if why:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [why]}]})
            if context_mention:
                nodes.append({"tag": "p", "children": [context_mention]})
            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    if not nodes:
        return None
    return await _telegraph_post(f"Термины: {title}", author, nodes, loop)


async def _gemini_text_request(prompt: str, temperature: float = 0.4,
                                max_tokens: int = 8000, task: str = "telegraph_text",
                                video_id: str = "", thinking_level: str = "high",
                                response_mime_type: str | None = None,
                                response_schema=None,
                                allow_model_fallback: bool = False) -> str | None:
    """Run Telegraph semantic generation through the shared Gemini transport.

    There is no local key loop and no model-global quota blacklist. The generic
    transport owns the initial+2 transient budget across all configured clients.
    Structured-output fallback is compatibility-only: quota, overload, internal
    errors and timeouts are re-raised so they consume the shared transient budget
    instead of causing an immediate second GenerateContent request.
    """
    if not GEMINI_CLIENTS:
        return None

    _compacted = compact_prompt_for_generation(prompt)
    if _compacted.saved_chars:
        logger.info(
            "_gemini_text_request[%s]: prompt compacted %d -> %d chars (removed_lines=%d)",
            task, _compacted.original_chars, _compacted.compacted_chars, _compacted.removed_lines,
        )
    prompt = _compacted.text
    _start_time = time.time()
    _TIME_BUDGET = 180
    model_name = GEMINI_MODEL

    if allow_model_fallback:
        logger.warning(
            "_gemini_text_request[%s]: model fallback requested but ignored by strict semantic quality policy",
            task,
        )

    def _duration_ms() -> int:
        return int((time.time() - _start_time) * 1000)

    def _is_internal_error(exc: BaseException) -> bool:
        text = str(exc)
        return "500" in text or "INTERNAL" in text.upper()

    async def _one_client_request(client):
        if time.time() - _start_time > _TIME_BUDGET:
            raise asyncio.TimeoutError(
                f"Telegraph Gemini total time budget exceeded ({_TIME_BUDGET}s)"
            )
        config = make_text_config_smart(
            temperature=temperature,
            max_output_tokens=max_tokens,
            model_name=model_name,
            thinking_level=thinking_level,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
        )
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                ),
                timeout=min(600.0, max(1.0, _TIME_BUDGET - (time.time() - _start_time))),
            )
        except Exception as schema_err:
            if (
                response_schema is None
                or is_quota_error(schema_err)
                or is_overload_error(schema_err)
                or _is_internal_error(schema_err)
                or capacity_control.is_timeout_error(schema_err)
            ):
                raise
            logger.warning(
                "_gemini_text_request[%s]: structured output failed (%s: %s) — one compatibility retry without schema",
                model_name, type(schema_err).__name__, str(schema_err)[:180],
            )
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=make_text_config_smart(
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                        model_name=model_name,
                        thinking_level=thinking_level,
                    ),
                ),
                timeout=min(600.0, max(1.0, _TIME_BUDGET - (time.time() - _start_time))),
            )

        try:
            result = resp.text or ""
        except (ValueError, AttributeError):
            result = ""
            if getattr(resp, "candidates", None):
                for part in resp.candidates[0].content.parts:
                    if not getattr(part, "thought", False) and getattr(part, "text", None):
                        result = part.text
                        break
        if not result:
            logger.warning(
                "_gemini_text_request[%s]: пустой ответ (finish=%s)",
                model_name,
                resp.candidates[0].finish_reason if getattr(resp, "candidates", None) else "?",
            )
            return ""

        meta = getattr(resp, "usage_metadata", None)
        finish_reason = str(resp.candidates[0].finish_reason) if getattr(resp, "candidates", None) else "?"
        if meta:
            logger.info(
                "Gemini[%s] tokens: prompt=%s thoughts=%s output=%s finish=%s",
                model_name,
                getattr(meta, "prompt_token_count", "?"),
                getattr(meta, "thoughts_token_count", "?"),
                getattr(meta, "candidates_token_count", "?"),
                finish_reason,
            )
        if "MAX_TOKENS" in finish_reason.upper():
            logger.warning(
                "_gemini_text_request[%s]: ответ ОБРЕЗАН по max_tokens=%s (thinking съел бюджет)",
                model_name, max_tokens,
            )
        await alog_gemini_response(
            response=resp,
            task=task,
            video_id=video_id,
            model=model_name,
            thinking_level=thinking_level,
            duration_ms=_duration_ms(),
            retry_num=0,
            is_fallback=False,
            json_valid=True,
        )
        return result

    last_err = None
    try:
        result = await gemini_generate(
            GEMINI_CLIENTS,
            _one_client_request,
            model_name=model_name,
        )
        if result:
            return result
    except Exception as exc:
        last_err = exc
        logger.warning(
            "_gemini_text_request[%s]: shared transport exhausted after %.1fs: %s: %s",
            task, time.time() - _start_time, type(exc).__name__, str(exc)[:200],
        )

    await alog_gemini_run(
        task=task,
        video_id=video_id,
        model=model_name,
        thinking_level=thinking_level,
        duration_ms=_duration_ms(),
        retry_num=max(0, capacity_control.transient_attempt_limit() - 1),
        is_fallback=False,
        json_valid=False,
        error=(str(last_err)[:300] if last_err else "empty_response"),
    )
    return None


def _parse_expanded_json(text: str, max_depth: int = 50, max_iterations: int = 500_000) -> tuple[list, list] | None:
    """Парсит JSON {outline, sections} из ответа Gemini. Возвращает (outline, sections) или None."""
    text = text.strip()
    text = re.sub(r'[\u2066-\u2069\u202a-\u202e]', '', text)
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        data = json.loads(text)
        return data.get("outline", []), data.get("sections", [])
    except json.JSONDecodeError:
        pass
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e > s:
        try:
            data = json.loads(text[s:e+1])
            return data.get("outline", []), data.get("sections", [])
        except json.JSONDecodeError:
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
    if s != -1:
        chunk = text[s:]
        last_complete = -1
        depth = 0
        in_str = False
        escape = False
        iterations = 0
        for idx, ch in enumerate(chunk):
            iterations += 1
            if iterations > max_iterations:
                logger.warning(f"_parse_expanded_json: превышен лимит итераций ({max_iterations}), прерываем.")
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
                if depth > max_depth:
                    logger.warning(f"_parse_expanded_json: превышена максимальная глубина ({max_depth}), прерываем.")
                    break
            elif ch == "}":
                depth -= 1
                if depth == 1:
                    last_complete = idx
        if last_complete > 0:
            fixed = chunk[:last_complete+1] + "\n  ]\n}"
            try:
                data = json.loads(fixed)
                sections = data.get("sections", [])
                if sections:
                    outline = data.get("outline", [
                        {"title": s.get("title", ""), "time": s.get("time") or ""}
                        for s in sections
                    ])
                    logger.info(f"_parse_expanded_json: восстановлен обрезанный JSON, sections={len(sections)}")
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
    plain_scripture: bool = False,
    ai_data: dict | None = None,
    video_id: str = "",
) -> str | None:
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
        logger.info("Expanded publish depth=%d: %s (%d secs, %d nodes) — публикую...",
                    depth, sec_range, len(secs), len(body_nodes))
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
                s1 = {**sec, 'content': '\n\n'.join(paragraphs[:mid_p]), 'title': sec.get('title', '') + ' (ч.\u200b1)'}
                s2 = {**sec, 'content': '\n\n'.join(paragraphs[mid_p:]), 'title': sec.get('title', '') + ' (ч.\u200b2)'}
                if not await _publish_recursive([s1], depth + 1):
                    return False
                return await _publish_recursive([s2], depth + 1)
            logger.warning("Expanded publish depth=%d: %s один section (%d nodes) нельзя разбить — fail",
                           depth, sec_range, len(body_nodes))
            return False
        mid = max(1, len(secs) // 2)
        if not await _publish_recursive(secs[:mid], depth + 1):
            return False
        return await _publish_recursive(secs[mid:], depth + 1)

    success = await _publish_recursive(sections)
    if not success or not published_parts:
        return None
    parts = [p[0] for p in published_parts]
    parts_urls = [p[1] for p in published_parts]
    total = len(parts)

    async def _build_expanded_part_nodes(i: int, part_secs: list, part_title: str,
                                          *, include_outline: bool = True) -> list:
        _nodes: list = []
        if include_outline and i == 0:
            _nodes.extend(_build_toc_nodes_v2(outline, yt_url=yt_url, parts=parts, duration=duration))
        if total > 1 and i > 0:
            _nodes.extend(_build_nav_nodes_v2(i, total, parts_urls, leading_hr=False))
            _nodes.append({"tag": "hr"})
        for sec_idx, sec in enumerate(part_secs):
            if sec_idx > 0:
                _nodes.append({"tag": "hr"})
            _nodes.extend(_section_to_nodes_v2(sec, yt_url=yt_url,
                                                rutube_url=rutube_url, vk_url=vk_url,
                                                page_title=part_title, duration=duration,
                                                plain_scripture=plain_scripture))
        if total > 1:
            _nodes.extend(_build_nav_nodes_v2(i, total, parts_urls))
        if i == total - 1 and ai_data:
            try:
                _scripture_refs = extract_scripture_refs(ai_data)
                _related = await aget_related_materials(
                    author=author,
                    scripture=(_scripture_refs[0] if _scripture_refs else ""),
                    exclude_video_id=video_id,
                    limit=3
                )
                if _related:
                    _nodes.extend(_build_related_materials_nodes(_related))
            except Exception as _rel_err:
                logger.debug("Related materials block skip: %s", _rel_err)
        return _nodes

    for i, (page_url, part_secs) in enumerate(zip(parts_urls, parts)):
        part_num = i + 1
        if total > 1:
            _sfx = f" (часть {part_num}/{total})"
            part_title = str(page_title)[:256 - len(_sfx)] + _sfx
        else:
            part_title = page_title
        final_nodes = await _build_expanded_part_nodes(i, part_secs, part_title)
        if i == 0:
            await asyncio.sleep(2)
        ok = False
        for retry_attempt in range(3):
            ok = await _edit_telegraph_page(page_url, part_title, author, final_nodes, loop)
            if ok:
                break
            _backoff = 3 * (2 ** retry_attempt)
            logger.warning("Expanded publish: editPage часть %d/%d попытка %d/3 failed для %s, жду %dс...",
                           part_num, total, retry_attempt + 1, page_url, _backoff)
            await asyncio.sleep(_backoff)
        if not ok and include_toc:
            logger.warning("Expanded publish: editPage часть %d/%d упала с оглавлением — повтор без него (контент сохраняем)",
                           part_num, total)
            await asyncio.sleep(2)
            final_nodes = await _build_expanded_part_nodes(i, part_secs, part_title, include_outline=False)
            ok = await _edit_telegraph_page(page_url, part_title, author, final_nodes, loop)
        if ok:
            logger.info("Expanded publish: editPage часть %d/%d -> %s", part_num, total, page_url)
        else:
            logger.warning("Expanded publish: editPage часть %d/%d окончательно failed, page content may stay outdated: %s",
                           part_num, total, page_url)
    return parts_urls[0]


def _content_audit_retry_enabled() -> bool:
    return (os.getenv("EXPANDED_CONTENT_AUDIT_RETRY", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _audit_warning_count(issues) -> int:
    try:
        return sum(1 for i in issues or [] if has_content_audit_warnings([i]))
    except Exception:
        return len(issues or [])


async def _retry_expanded_sections_for_content_audit(
    *, label: str, original_prompt: str, outline: list, sections: list,
    issues: list, max_tokens: int, thinking_level: str, response_schema,
    expected_author: str,
) -> tuple[list, list, list] | None:
    if not _content_audit_retry_enabled() or not issues or not has_content_audit_warnings(issues):
        return None
    issue_summary = format_content_audit_issues(issues, limit=10)
    payload = json.dumps({"outline": outline or [], "sections": sections or []}, ensure_ascii=False)
    if len(payload) > 75_000:
        payload = payload[:75_000]
    repair_prompt = (
        "Ты получил JSON для Telegraph-страницы, но deterministic content-audit нашёл проблемы. "
        "Исправь ТОЛЬКО перечисленные проблемы и верни валидный JSON {outline, sections}. "
        "Не сокращай страницу, не удаляй сильные мысли, не добавляй новых источников вне уже разрешённого контекста. "
        "Если scripture/source/application/lexicon блок тонкий — дополни его grounded объяснением из исходного задания. "
        "ВАЖНО: если scripture блок уже имеет text с описанием, НЕ дублируй то же описание в role_in_argument. "
        "role_in_argument должен ДОПОЛНЯТЬ text, а не повторять его. Если text уже содержит полное объяснение, "
        "в role_in_argument добавь НОВУЮ информацию: параллельные тексты, контекст главы, связь с темой. "
        "Убери third-person wrappers: пиши тезис напрямую, без конструкции 'роль/имя автора + показывает/объясняет'. "
        "Сохрани порядок sections и таймкоды. Верни только JSON.\n\n"
        f"CONTENT_AUDIT_ISSUES:\n{issue_summary}\n\n"
        "ORIGINAL_TASK_CONTEXT:\n" + original_prompt[:24000] + "\n\n"
        "CURRENT_JSON:\n" + payload
    )
    logger.warning("%s: content audit retry requested: %s", label, issue_summary[:400])
    raw = await _gemini_text_request(
        repair_prompt, temperature=0.25,
        max_tokens=min(max(max_tokens * 2, 16000), 65000),
        task=f"telegraph_{label.lower()}_content_audit_retry",
        thinking_level=thinking_level,
        response_mime_type=("application/json" if response_schema else None),
        response_schema=response_schema, allow_model_fallback=False,
    )
    if not raw:
        logger.warning("%s: content audit retry returned empty response", label)
        return None
    parsed = _parse_expanded_json(raw)
    if parsed is None:
        logger.warning("%s: content audit retry returned broken JSON", label)
        return None
    retry_outline, retry_sections = parsed
    retry_sections = [s for s in retry_sections if isinstance(s, dict) and (s.get("title") or s.get("content") or s.get("blocks"))]
    if not retry_sections:
        return None
    retry_sections, retry_outline, retry_issues = audit_expanded_sections(
        retry_sections, retry_outline if isinstance(retry_outline, list) else [], label=label, expected_author=expected_author
    )
    before_warnings = _audit_warning_count(issues)
    after_warnings = _audit_warning_count(retry_issues)
    if after_warnings < before_warnings or (label != "StudyAnalysis" and after_warnings == before_warnings):
        logger.info("%s: content audit retry accepted warnings %d -> %d", label, before_warnings, after_warnings)
        return retry_sections, retry_outline, retry_issues
    logger.warning("%s: content audit retry rejected: warnings would increase %d -> %d",
                   label, before_warnings, after_warnings)
    return None


async def _run_expanded_pipeline(
    label: str, prompt: str, page_prefix: str, tg_title: str, author: str,
    yt_url: str = "", include_toc: bool = True, fallback_fn=None,
    max_tokens: int = 8000, rutube_url: str = "", vk_url: str = "",
    duration: int = 0, plain_scripture: bool = False,
    thinking_level: str = "high", ai_data: dict | None = None,
    video_id: str = "",
) -> str | None:
    try:
        logger.info("%s: запрос к Gemini", label)
        _task_name = f"telegraph_{label.lower()}"
        _adaptive = get_adaptive_text_generation_params(_task_name, 0.4, max_tokens)
        if _adaptive.reason != "baseline":
            logger.warning("%s: adaptive generation params: temperature=%.2f max_tokens=%d (%s)",
                           label, _adaptive.temperature, _adaptive.max_tokens, _adaptive.reason)
        _structured_schema = expanded_page_response_schema() if _expanded_structured_output_enabled() else None
        raw = await _gemini_text_request(
            prompt, temperature=_adaptive.temperature, max_tokens=_adaptive.max_tokens,
            task=_task_name, thinking_level=thinking_level,
            response_mime_type=("application/json" if _structured_schema else None),
            response_schema=_structured_schema,
        )
        if not raw:
            logger.warning("%s: Gemini вернул пустой ответ -- fallback", label)
            return await fallback_fn() if fallback_fn else None
        parsed = _parse_expanded_json(raw)
        if parsed is None:
            logger.warning("%s: сломанный JSON -- retry. Начало ответа: %.500s", label, raw)
            await asyncio.sleep(5)
            retry_prompt = (
                "Твой предыдущий ответ содержал сломанный JSON. Повтори строго: только валидный JSON {outline, sections}, "
                "без ```json, без текста до/после.\n\n" + prompt
            )
            _retry_tokens = min(max(_adaptive.max_tokens * 2, max_tokens * 2), 65000)
            raw2 = await _gemini_text_request(
                retry_prompt, max_tokens=_retry_tokens,
                task=f"telegraph_{label.lower()}_retry", thinking_level=thinking_level,
                response_mime_type=("application/json" if _structured_schema else None),
                response_schema=_structured_schema,
            )
            if raw2:
                parsed = _parse_expanded_json(raw2)
            if parsed is None:
                return await fallback_fn() if fallback_fn else None
        outline, sections = parsed
        if not isinstance(sections, list):
            return await fallback_fn() if fallback_fn else None
        sections = [s for s in sections if isinstance(s, dict) and (s.get("title") or s.get("content") or s.get("blocks"))]
        if not sections:
            return await fallback_fn() if fallback_fn else None
        _all_empty = all(not ((s.get("content") or "").strip() or s.get("blocks")) for s in sections if isinstance(s, dict))
        if _all_empty:
            return await fallback_fn() if fallback_fn else None

        sections, outline, _content_audit = audit_expanded_sections(
            sections, outline if isinstance(outline, list) else [], label=label, expected_author=author)
        _audit_mode = get_content_audit_mode()
        if _content_audit and _audit_mode != "off":
            _log_audit = logger.warning if has_content_audit_warnings(_content_audit) else logger.info
            _log_audit("%s: content audit before publish: %s", label, format_content_audit_issues(_content_audit))
        _retry_result = await _retry_expanded_sections_for_content_audit(
            label=label, original_prompt=prompt,
            outline=outline if isinstance(outline, list) else [], sections=sections,
            issues=_content_audit, max_tokens=_adaptive.max_tokens,
            thinking_level=thinking_level, response_schema=_structured_schema,
            expected_author=author,
        )
        if _retry_result is not None:
            sections, outline, _content_audit = _retry_result
        if _content_audit and should_abort_for_content_audit(_content_audit):
            return await fallback_fn() if fallback_fn else None
        if isinstance(outline, list) and outline and len(outline) == len(sections) and all(isinstance(oi, dict) for oi in outline):
            outline = [{"title": s.get("title", ""), "time": (oi.get("time") or s.get("time") or "")} for s, oi in zip(sections, outline)]
        else:
            outline = [{"title": s.get("title", ""), "time": s.get("time") or ""} for s in sections]
        try:
            est = _estimate_nodes_v2(sections, yt_url, rutube_url=rutube_url, vk_url=vk_url, duration=duration)
        except Exception:
            est = -1
        logger.info("%s: sections=%d outline=%d nodes_estimate=%d", label, len(sections), len(outline), est)
        if " — " in tg_title:
            _sep = ": "
        elif ":" in tg_title:
            _sep = " — "
        else:
            _sep = ": "
        _full_title = f"{page_prefix}{_sep}{tg_title}"[:256]
        url = await _publish_expanded_page(
            sections=sections, outline=outline, page_title=_full_title, tg_title=_full_title,
            author=author, yt_url=yt_url, include_toc=include_toc,
            rutube_url=rutube_url, vk_url=vk_url, duration=duration,
            plain_scripture=plain_scripture, ai_data=ai_data, video_id=video_id,
        )
        if url:
            return url
        return await fallback_fn() if fallback_fn else None
    except Exception as e:
        logger.error("%s: ❌ UNEXPECTED ERROR (%s: %s) — переключаюсь на COMPACT-FALLBACK.",
                     label, type(e).__name__, str(e)[:200])
        logger.exception("%s: traceback ↓", label)
        return await fallback_fn() if fallback_fn else None


async def create_telegraph_study_analysis(
    ai_data: dict | None, title: str, author: str, yt_url: str = "",
    compact_fn=None, rutube_url: str = "", vk_url: str = "",
    synopsis_outline: list | None = None, duration: float = 0,
    video_id: str = "",
) -> str | None:
    if not GEMINI_CLIENTS:
        logger.info("StudyAnalysis: нет GEMINI_CLIENTS -- fallback")
        return await compact_fn() if compact_fn else None
    _ai = ai_data or {}
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean  = _clean_meta_line(title or "") or "Без названия"
    real_author = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = title_case_fragment(normalize_title_text(_ai.get("real_title") or "") or title_clean)
    prompt_title = re.sub(r'(?<=\w)\.\s+(?=[А-ЯA-Z])', ': ', prompt_title)
    prompt_title = re.sub(r'\s+-\s+', ' — ', prompt_title)
    _fmt = _ai.get("format", "other") or "other"
    try:
        _duration_from_ai = float(_ai.get("duration") or 0)
    except (ValueError, TypeError):
        _duration_from_ai = 0
    effective_duration = _duration_from_ai or float(duration or 0)
    _key_cats = _ai.get("key_categories", []) or []
    key_cats_str = "\n".join(f"- {c}" for c in _key_cats[:12]) if _key_cats else "не указаны"
    _td = _ai.get("terms_data") or {}
    concepts = _td.get("concepts", []) or []
    scripture = _td.get("scripture", []) or []
    translations = _td.get("translations", []) or []
    lexicon_notes = _td.get("lexicon_notes", []) or []
    def _fmt_block(items, max_n=12):
        return "\n".join(f"- {str(x).split('||')[0].strip()}" for x in items[:max_n]) if items else "не указаны"
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
        _outline_lines = "\n".join(f"  {i+1}. {s.get('title', '')} [{s.get('time', '')}]" for i, s in enumerate(synopsis_outline))
        _synopsis_context = (
            f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\n"
            "НЕ ПОВТОРЯЙ содержание этих разделов. Твоя задача — дать то, чего в конспекте нет: "
            "исследовательскую глубину, богословский анализ, разбор терминов и Писания."
        )
    else:
        _synopsis_context = ""
    _steno = str(_ai.get("_synopsis_grounding") or "").strip()
    if _steno:
        _synopsis_context = ((_synopsis_context + "\n\n" if _synopsis_context else "") +
            "ФРАГМЕНТЫ ДОСЛОВНОЙ СТЕНОГРАММЫ РЕЧИ АВТОРА (главный источник формулировок: "
            "когда передаёшь мысль или слова автора — опирайся на этот текст, НЕ изобретай цитат):\n" + _steno)
    _source_pack = get_source_pack_for_ai_data(_ai)
    _study_profile = get_expanded_analysis_profile(effective_duration, page_kind="study")
    _profile_block = build_reasoning_first_block("study") + "\n\n" + _study_profile.prompt_block("study")
    _synopsis_context_for_prompt = _profile_block + ("\n\n" + _synopsis_context if _synopsis_context else "")
    prompt = STUDY_ANALYSIS_PROMPT.format(
        title=prompt_title, author=real_author,
        duration=format_timestamp(effective_duration) if effective_duration else "не указана",
        format_name=_fmt, hermeneutic_method=_hm_study,
        main_topic=(_ai.get("main_topic") or "").strip()[:800] or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указано",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800] or "не указан",
        key_categories=key_cats_str,
        timestamps=(_ai.get("timestamps") or "").strip()[:800] or "не указаны",
        concepts=_fmt_block(concepts, 12), scripture=_fmt_scripture(scripture, 12),
        translations=_fmt_block(translations, 8), lexicon_notes=_fmt_block(lexicon_notes, 8),
        synopsis_context=_synopsis_context_for_prompt, source_pack=_source_pack,
    )
    tg_title = join_title_author(prompt_title, real_author)[:256]
    return await _run_expanded_pipeline(
        label="StudyAnalysis", prompt=prompt, page_prefix="📖 Разбор материала",
        tg_title=tg_title, author=author_clean, yt_url=yt_url, include_toc=True,
        fallback_fn=compact_fn, max_tokens=_study_profile.max_tokens,
        rutube_url=rutube_url, vk_url=vk_url,
        duration=int(effective_duration) if effective_duration else 0,
        ai_data=_ai, video_id=video_id,
    )


async def create_telegraph_study_reflection_combined(
    ai_data: dict | None, questions: list, title: str, author: str,
    yt_url: str = "", study_compact_fn=None, reflection_compact_fn=None,
    rutube_url: str = "", vk_url: str = "", synopsis_outline: list | None = None,
    duration: float = 0,
) -> CombinedStudyReflectionResult | None:
    if not combined_study_reflection_enabled() or not GEMINI_CLIENTS:
        return None
    _ai = ai_data or {}
    if not questions:
        return None
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean = _clean_meta_line(title or "") or "Без названия"
    real_author = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = title_case_fragment(normalize_title_text(_ai.get("real_title") or "") or title_clean)
    prompt_title = re.sub(r'(?<=\w)\.\s+(?=[А-ЯA-Z])', ': ', prompt_title)
    prompt_title = re.sub(r'\s+-\s+', ' — ', prompt_title)
    try:
        effective_duration = float(_ai.get("duration") or duration or 0)
    except (TypeError, ValueError):
        effective_duration = float(duration or 0)
    duration_str = format_timestamp(effective_duration) if effective_duration else "не указана"
    _fmt = _ai.get("format", "other") or "other"
    _hm = _ai.get("hermeneutic_method") or "mixed"
    _key_cats = _ai.get("key_categories", []) or []
    key_cats_str = "\n".join(f"- {c}" for c in _key_cats[:12]) if _key_cats else "не указаны"
    _td = _ai.get("terms_data") or {}
    def _fmt_block(items, max_n=12):
        return "\n".join(f"- {str(x).split('||')[0].strip()}" for x in items[:max_n]) if items else "не указаны"
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
    if synopsis_outline is not None:
        _outline_lines = "\n".join(f"  {i + 1}. {sec.get('title', '')} [{sec.get('time', '')}]" for i, sec in enumerate(synopsis_outline))
        study_synopsis_context = f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\nНЕ ПОВТОРЯЙ содержание этих разделов. Дай исследовательскую глубину, богословский анализ, разбор терминов и Писания."
        reflection_synopsis_context = f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\nНЕ ПОВТОРЯЙ содержание этих разделов. Дай пасторское применение, самоиспытание, молитвенный отклик и личные вопросы."
    else:
        study_synopsis_context = reflection_synopsis_context = ""
    _source_pack = get_source_pack_for_ai_data(_ai)
    study_profile = get_expanded_analysis_profile(effective_duration, page_kind="study")
    reflection_profile = get_expanded_analysis_profile(effective_duration, page_kind="reflection")
    study_context = build_reasoning_first_block("study") + "\n\n" + study_profile.prompt_block("study")
    reflection_context = build_reasoning_first_block("reflection") + "\n\n" + reflection_profile.prompt_block("reflection")
    if study_synopsis_context:
        study_context += "\n\n" + study_synopsis_context
    if reflection_synopsis_context:
        reflection_context += "\n\n" + reflection_synopsis_context
    study_prompt = STUDY_ANALYSIS_PROMPT.format(
        title=prompt_title, author=real_author, duration=duration_str, format_name=_fmt,
        hermeneutic_method=_hm,
        main_topic=(_ai.get("main_topic") or "").strip()[:800] or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указано",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800] or "не указан",
        key_categories=key_cats_str,
        timestamps=(_ai.get("timestamps") or "").strip()[:800] or "не указаны",
        concepts=_fmt_block(_td.get("concepts", []) or [], 12),
        scripture=_fmt_scripture(_td.get("scripture", []) or [], 12),
        translations=_fmt_block(_td.get("translations", []) or [], 8),
        lexicon_notes=_fmt_block(_td.get("lexicon_notes", []) or [], 8),
        synopsis_context=study_context, source_pack=_source_pack,
    )
    _normalized_questions = _normalize_question_items(questions, max_items=15)
    green = [q for q in _normalized_questions if str(q).startswith("🟢")]
    blue = [q for q in _normalized_questions if str(q).startswith("🔵")]
    merged_questions = (green + blue)[:15]
    _question_marker_re = re.compile(r"^[🟢🔵]\s*")
    questions_block = "\n".join(f"- {_question_marker_re.sub('', str(q)).strip()}" for q in merged_questions)
    timestamps_block = "\n".join(f"- {line.strip()}" for line in str(_ai.get("timestamps") or "").splitlines() if line.strip()) or "не указаны"
    reflection_prompt = REFLECTION_APPLICATION_PROMPT.format(
        title=prompt_title, author=real_author, duration=duration_str,
        hermeneutic_method=_hm,
        main_topic=(_ai.get("main_topic") or "").strip()[:800] or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указана",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800] or "не указан",
        key_categories=key_cats_str, questions_block=questions_block,
        timestamps_block=timestamps_block, synopsis_context=reflection_context,
    )
    combined_prompt = (
        "QUALITY-FIRST EXPERIMENT: выполни ДВЕ независимые страницы за один вызов. Не сокращай глубину ради краткости. "
        "Верни только JSON вида {\"study\":{\"outline\":[],\"sections\":[]},\"reflection\":{\"outline\":[],\"sections\":[]}}. "
        "Каждая страница должна соответствовать своему заданию полностью.\n\n"
        "=== STUDY_ANALYSIS TASK ===\n" + study_prompt + "\n\n=== REFLECTION_APPLICATION TASK ===\n" + reflection_prompt
    )
    _max_prompt_chars = int(os.getenv("COMBINED_STUDY_REFLECTION_MAX_PROMPT_CHARS", "90000"))
    if len(combined_prompt) > _max_prompt_chars:
        return None
    schema = combined_expanded_pages_response_schema() if _expanded_structured_output_enabled() else None
    max_tokens = min(max(study_profile.max_tokens + reflection_profile.max_tokens, 32000), 65000)
    raw = await _gemini_text_request(
        combined_prompt, temperature=0.35, max_tokens=max_tokens,
        task="telegraph_studyreflection_combined", thinking_level="high",
        response_mime_type=("application/json" if schema else None),
        response_schema=schema, allow_model_fallback=False,
    )
    if not raw:
        return None
    parsed = _parse_combined_expanded_json(raw)
    if parsed is None:
        return None
    results: dict[str, str | None] = {"study": None, "reflection": None}
    for key, label, page_prefix, plain_scripture in [
        ("study", "StudyAnalysisCombined", "📖 Разбор материала", False),
        ("reflection", "ReflectionApplicationCombined", "🙏 Размышление и применение", True),
    ]:
        outline, sections = parsed.get(key, ([], []))
        sections = [s for s in sections if isinstance(s, dict) and (s.get("title") or s.get("content") or s.get("blocks"))]
        if not sections or not _combined_relevance_ok(sections, _ai, label=label):
            continue
        sections, outline, issues = audit_expanded_sections(sections, outline if isinstance(outline, list) else [], label=label, expected_author=author_clean)
        if issues and should_abort_for_content_audit(issues):
            continue
        if not outline or len(outline) != len(sections):
            outline = [{"title": s.get("title", ""), "time": s.get("time") or ""} for s in sections]
        tg_title = join_title_author(prompt_title, real_author)
        _sep = ": " if " — " in tg_title or ":" not in tg_title else " — "
        full_page_title = f"{page_prefix}{_sep}{tg_title}"[:256]
        results[key] = await _publish_expanded_page(
            sections, outline, full_page_title, full_page_title, author_clean,
            yt_url=yt_url, include_toc=True, rutube_url=rutube_url, vk_url=vk_url,
            duration=int(effective_duration) if effective_duration else 0,
            plain_scripture=plain_scripture,
        )
    if not (results["study"] and results["reflection"]):
        return None
    return CombinedStudyReflectionResult(study_url=results["study"], reflection_url=results["reflection"])


def _combined_relevance_ok(sections: list[dict], ai_data: dict | None, *, label: str = "combined") -> bool:
    def terms(text: str) -> set[str]:
        stop = {"когда", "котор", "чтобы", "этот", "такой", "через", "вопрос", "ответ", "применение"}
        return {w for w in re.findall(r"[А-Яа-яЁёA-Za-z]{5,}", str(text or "").lower().replace("ё", "е")) if w not in stop}
    ai = ai_data or {}
    source = " ".join([str(ai.get("real_title") or ""), str(ai.get("main_topic") or ""),
                       " ".join(str(x) for x in (ai.get("key_categories") or [])), str(ai.get("timestamps") or "")])
    source_terms = terms(source)
    if len(source_terms) < 4:
        return True
    page_text = " ".join(str(s.get("title") or "") + " " + str(s.get("content") or "") for s in sections if isinstance(s, dict))
    for sct in sections:
        for b in (sct.get("blocks") or []) if isinstance(sct, dict) else []:
            if isinstance(b, dict):
                page_text += " " + " ".join(str(b.get(k) or "") for k in ("text", "quote", "why_relevant", "role_in_argument", "lemma"))
    overlap = source_terms & terms(page_text)
    if len(overlap) < 2:
        logger.warning("%s: relevance audit failed for combined page; overlap=%s", label, sorted(overlap))
        return False
    return True


def _parse_combined_expanded_json(text: str) -> dict[str, tuple[list, list]] | None:
    raw = str(text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    out: dict[str, tuple[list, list]] = {}
    for key in ("study", "reflection"):
        page = data.get(key) if isinstance(data, dict) else None
        if not isinstance(page, dict):
            return None
        outline = page.get("outline", [])
        sections = page.get("sections", [])
        if not isinstance(outline, list) or not isinstance(sections, list):
            return None
        out[key] = (outline, sections)
    return out


async def create_telegraph_reflection_application(
    questions: list, title: str, author: str, ai_data: dict | None = None,
    duration: int = 0, yt_url: str = "", compact_fn=None,
    rutube_url: str = "", vk_url: str = "", synopsis_outline: list | None = None,
    video_id: str = "",
) -> str | None:
    if not GEMINI_CLIENTS:
        logger.info("ReflectionApplication: нет GEMINI_CLIENTS -- fallback")
        return await compact_fn() if compact_fn else None
    _ai = ai_data or {}
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean = _clean_meta_line(title or "") or "Без названия"
    real_author = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = title_case_fragment(normalize_title_text(_ai.get("real_title") or "") or title_clean)
    prompt_title = re.sub(r'(?<=\w)\.\s+(?=[А-ЯA-Z])', ': ', prompt_title)
    prompt_title = re.sub(r'\s+-\s+', ' — ', prompt_title)
    _ts = (_ai.get("timestamps") or "").strip()
    _ts_block = "\n".join(f"- {l.strip()}" for l in _ts.splitlines() if l.strip()) or "не указаны"
    duration_str = format_timestamp(duration) if duration else "не указана"
    if isinstance(questions, list) and questions:
        _normalized_questions = _normalize_question_items(questions, max_items=15)
        green = [q for q in _normalized_questions if str(q).startswith("🟢")]
        blue = [q for q in _normalized_questions if str(q).startswith("🔵")]
        merged = (green + blue)[:15]
    else:
        merged = []
    if not merged:
        return await compact_fn() if compact_fn else None
    def _strip_marker(q: str) -> str:
        return re.sub(r"^[🟢🔵]\s*", "", str(q)).strip()
    questions_block = "\n".join(f"- {_strip_marker(q)}" for q in merged)
    _key_cats_list = _ai.get("key_categories") or []
    _key_cats_str = "\n".join(f"- {c}" for c in _key_cats_list[:12]) if _key_cats_list else "не указаны"
    _hm = (_ai.get("hermeneutic_method") or "mixed") or "mixed"
    if synopsis_outline is not None:
        _outline_lines = "\n".join(f"  {i+1}. {s.get('title', '')} [{s.get('time', '')}]" for i, s in enumerate(synopsis_outline))
        _synopsis_context = f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\nНЕ ПОВТОРЯЙ содержание этих разделов. Твоя задача — дать то, чего в конспекте нет: пасторское применение, самоиспытание, молитвенный отклик и личные вопросы к читателю."
    else:
        _synopsis_context = ""
    _steno_r = str(_ai.get("_synopsis_grounding") or "").strip()
    if _steno_r:
        _synopsis_context = ((_synopsis_context + "\n\n" if _synopsis_context else "") +
            "ФРАГМЕНТЫ ДОСЛОВНОЙ СТЕНОГРАММЫ РЕЧИ АВТОРА (бери язык, образы и формулировки применений отсюда; цитаты автора — только из этого текста):\n" + _steno_r)
    _reflection_profile = get_expanded_analysis_profile(duration, page_kind="reflection")
    _profile_block = build_reasoning_first_block("reflection") + "\n\n" + _reflection_profile.prompt_block("reflection")
    _synopsis_context_for_prompt = _profile_block + ("\n\n" + _synopsis_context if _synopsis_context else "")
    prompt = REFLECTION_APPLICATION_PROMPT.format(
        title=prompt_title, author=real_author, duration=duration_str,
        hermeneutic_method=_hm,
        main_topic=(_ai.get("main_topic") or "").strip()[:800] or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указана",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800] or "не указан",
        key_categories=_key_cats_str, questions_block=questions_block,
        timestamps_block=_ts_block, synopsis_context=_synopsis_context_for_prompt,
    )
    tg_title = join_title_author(prompt_title, real_author)[:256]
    return await _run_expanded_pipeline(
        label="ReflectionApplication", prompt=prompt,
        page_prefix="🙏 Размышление и применение", tg_title=tg_title,
        author=author_clean, yt_url=yt_url, include_toc=True,
        fallback_fn=compact_fn, max_tokens=_reflection_profile.max_tokens,
        rutube_url=rutube_url, vk_url=vk_url,
        duration=int(duration) if duration else 0, plain_scripture=True,
        ai_data=_ai, video_id=video_id,
    )
