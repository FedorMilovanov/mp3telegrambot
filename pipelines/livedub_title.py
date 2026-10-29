#!/usr/bin/env python3
"""Title and author normalization for LiveDub Telegram captions.

The main pipeline keeps compatibility wrappers/re-exports for existing callers.
The implementation lives here so caption metadata policy, its bounded cache and
Gemini fallback can be tested without importing the delivery orchestration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, cast

from core.bounded_cache import BoundedLRUDict
from core.database import GEMINI_MODEL as GEMINI_MODEL
from core.globals import GEMINI_CLIENTS
from core.person_names import (
    KNOWN_AUTHOR_RU as _KNOWN_AUTHOR_RU,
    known_author_from_text as _known_author_from_text,
    known_ru_author_from_text as _known_ru_author_from_text,
    looks_like_author_list as _looks_like_author_list,
)
from core.text_utils import (
    normalize_author_name,
    normalize_common_typos,
    normalize_title_text,
    title_case_fragment,
)

logger = logging.getLogger(__name__)


def _cache_max_entries(raw: str | None) -> int:
    """Parse the cache limit without letting malformed environment input win."""
    try:
        return int((raw or "256").strip() or "256")
    except ValueError:
        return 256


_LIVEDUB_TITLE_CACHE_MAX = _cache_max_entries(os.getenv("LIVEDUB_TITLE_CACHE_MAX", "256"))
_LIVEDUB_TITLE_CACHE: BoundedLRUDict = BoundedLRUDict(max_entries=_LIVEDUB_TITLE_CACHE_MAX)


def _clean_livedub_meta_text(text: str | None) -> str:
    text = str(text or "")
    text = re.sub(r"(?i)#\s*(shorts?|ytshorts|youtube|sermon|preaching)\b", "", text)
    text = re.sub(r"(?i)\b(shorts?|youtube shorts?)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -—–|:;\t\n\r")
    return text.strip()


def _replace_known_author_names(text: str) -> str:
    out = str(text or "")
    for en, ru in _KNOWN_AUTHOR_RU.items():
        out = re.sub(rf"(?i)\b{re.escape(en)}\b", ru, out)
    out = re.sub(r"\s+(?:and|&)\s+", " & ", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip(" -—–|,;")
    return normalize_author_name(out)


def _split_title_author_line(line: str) -> tuple[str, str]:
    """Нормализует одну строку в порядок «Название - Автор».

    YouTube часто даёт author-first: «R.C. Sproul: True ...» или
    «Р. Ч. Спроул - True ...». Для DUB-caption/download filename нам нужен
    стабильный порядок title-first.
    """
    line = _clean_livedub_meta_text(line)
    if not line:
        return "", ""
    for sep in (": ", " - ", " — ", " – ", " | ", " // "):
        if sep not in line:
            continue
        left, right = [x.strip() for x in line.split(sep, 1)]
        if not (left and right):
            continue
        left_known = _known_author_from_text(left) or _known_ru_author_from_text(left)
        right_known = _known_author_from_text(right) or _known_ru_author_from_text(right)
        if left_known:
            return right, _replace_known_author_names(left) or left_known
        if right_known:
            return left, (_replace_known_author_names(right) if _looks_like_author_list(right) else right_known)
        if _looks_like_author_list(left) and not _looks_like_author_list(right):
            return right, _replace_known_author_names(left)
        if _looks_like_author_list(right):
            return left, _replace_known_author_names(right)
    return line, ""


def _title_part_has_cyrillic(line: str) -> bool:
    title_part, _author = _split_title_author_line(line)
    # 2026-06-11: Титл считается «русским», только если в самой части НАЗВАНИЯ
    # есть кириллица. Если кириллица только в имени автора (которое мы
    # подставляем из базы), а название всё ещё английское — нужно переводить.
    target = title_part if title_part else line
    return bool(re.search(r"[А-Яа-яЁё]", target))


def _split_livedub_title_author(
    full_title: str, parsed_title: str, parsed_performer: str, channel_name: str
) -> tuple[str, str]:
    full_title = _clean_livedub_meta_text(full_title)
    parsed_title = _clean_livedub_meta_text(parsed_title)
    parsed_performer = _clean_livedub_meta_text(parsed_performer)
    channel_name = _clean_livedub_meta_text(channel_name)

    title_from_line, author_from_line = _split_title_author_line(full_title)
    if author_from_line:
        return title_from_line, author_from_line

    author = _known_author_from_text(full_title, parsed_performer, channel_name)
    if not author and parsed_performer and not _looks_like_author_list(parsed_title):
        author = parsed_performer

    title = parsed_title or full_title
    if _looks_like_author_list(title) and full_title:
        title = full_title
    return title, author


def _fallback_livedub_ru_title(
    full_title: str, parsed_title: str, parsed_performer: str, channel_name: str
) -> tuple[str, str]:
    raw_title, raw_author = _split_livedub_title_author(full_title, parsed_title, parsed_performer, channel_name)
    title_ru = title_case_fragment(normalize_title_text(_replace_known_author_names(raw_title)) or raw_title)
    author_ru = _replace_known_author_names(raw_author) or _known_author_from_text(
        full_title, parsed_performer, channel_name
    )
    if author_ru and author_ru.lower() in {"grace to you", "heartcry", "heartcry missionary society"}:
        author_ru = _known_author_from_text(author_ru) or author_ru
    return title_ru.strip(), author_ru.strip()


async def _translate_livedub_title_for_caption(
    full_title: str,
    parsed_title: str,
    parsed_performer: str,
    channel_name: str,
    analysis_title: str = "",
    analysis_author: str = "",
    *,
    _gemini_clients: list[Any] | None = None,
) -> tuple[str, str]:
    """Return (Russian title, Russian author) for all ENG/LiveDub captions.

    Cost policy:
      • ENG Full/cache-hit: use existing Gemini analysis fields real_title/real_author.
      • ENG Quick: no extra Gemini by default; deterministic fallback from YouTube metadata.
      • Extra Gemini title-only translation is opt-in: LIVEDUB_TITLE_TRANSLATE=1.
    """
    clients = GEMINI_CLIENTS if _gemini_clients is None else _gemini_clients
    cache_key = (
        str(full_title or ""),
        str(parsed_title or ""),
        str(channel_name or ""),
        str(analysis_title or ""),
        str(analysis_author or ""),
    )
    if cache_key in _LIVEDUB_TITLE_CACHE:
        return cast(tuple[str, str], _LIVEDUB_TITLE_CACHE[cache_key])

    fallback_title, fallback_author = _fallback_livedub_ru_title(
        full_title, parsed_title, parsed_performer, channel_name
    )

    # Best path: use already-paid analysis result (ENG Full / cache-hit), no extra AI call.
    analysis_title = normalize_common_typos(normalize_title_text(analysis_title) or str(analysis_title or "")).strip()
    analysis_author = normalize_author_name(analysis_author).strip()
    if analysis_title or analysis_author:
        result = (analysis_title or fallback_title, analysis_author or fallback_author)
        _LIVEDUB_TITLE_CACHE[cache_key] = result
        return result

    # ENG Quick/default title translation: use the lightweight info pipeline,
    # not the heavy audio-analysis model directly. This keeps the DUB caption
    # Russian while still preferring cheap/free Flash-Lite models.
    if clients and os.getenv("LIVEDUB_TITLE_TRANSLATE", "1").strip().lower() in {"1", "true", "yes", "on", "always"}:
        try:
            from services.livedub_info import build_livedub_info_card

            base_line = _format_livedub_title_line(fallback_title, fallback_author)
            card = await build_livedub_info_card(base_line, None, source_url="", force=True)
            yt_title = _clean_livedub_meta_text((card or {}).get("youtube_title") or "")
            if yt_title:
                yt_title_norm, yt_author_norm = _split_title_author_line(yt_title)
                yt_line = (
                    f"{yt_title_norm.strip()} - {yt_author_norm.strip()}" if yt_author_norm else yt_title_norm.strip()
                )
                if _title_part_has_cyrillic(yt_line):
                    result = (yt_line, "")
                    _LIVEDUB_TITLE_CACHE[cache_key] = result
                    return result
                logger.info("[LiveDubTitle] light title is not Russian enough — trying main title prompt")
        except Exception as _light_title_err:
            logger.info("[LiveDubTitle] light title translation failed: %s", str(_light_title_err)[:120])

    # If lightweight title translation is disabled/unavailable, deterministic metadata fallback.
    if not clients or os.getenv("LIVEDUB_TITLE_TRANSLATE", "1").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
        "always",
    }:
        _LIVEDUB_TITLE_CACHE[cache_key] = (fallback_title, fallback_author)
        return fallback_title, fallback_author

    prompt = (
        "Переведи название и автора христианского видео на РУССКИЙ язык для публикации в Telegram.\n"
        'Верни СТРОГО JSON: {"title_ru":"...","author_ru":"..."}.\n'
        "ПРАВИЛА:\n"
        "- title_ru: ОБЯЗАТЕЛЬНО на русском языке; естественное название, без #shorts, без слова Shorts, без кавычек;\n"
        "- author_ru: имя автора/спикера на русском, если оно явно есть в названии/канале "
        "или общеизвестно по каналу; иначе пустая строка;\n"
        "- не добавляй отсебятину, не меняй богословский смысл;\n"
        "- ПЕРЕВОДИ ВСЁ на русский, не оставляй английских слов в названии;\n"
        "- известные имена: John MacArthur=Джон МакАртур, Paul Washer=Пол Вошер, "
        "Abner Chou=Абнер Чау, Costi Hinn=Кости Хинн, R.C. Sproul=Р. Ч. Спроул;\n"
        "- Grace to You обычно означает Джон МакАртур; HeartCry часто означает Пол Вошер, "
        "но если в title указан другой спикер — выбери его.\n\n"
        f"title: {full_title}\n"
        f"parsed_title: {parsed_title}\n"
        f"parsed_performer: {parsed_performer}\n"
        f"channel: {channel_name}\n"
    )
    try:
        from core.globals import make_text_config_smart
        from services.gemini_router import gemini_generate

        cfg = make_text_config_smart(
            temperature=0.1,
            max_output_tokens=512,
            thinking_level="minimal",
            response_mime_type="application/json",
        )

        async def _generate_title(client: Any) -> Any:
            return await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=cfg,
            )

        resp = await asyncio.wait_for(
            gemini_generate(
                clients,
                _generate_title,
                model_name=GEMINI_MODEL,
            ),
            timeout=30.0,  # total SLA for this tiny title translation
        )
        raw = (getattr(resp, "text", "") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        title_ru = title_case_fragment(normalize_title_text(str(data.get("title_ru") or "")))
        author_ru = _replace_known_author_names(str(data.get("author_ru") or ""))
        if not title_ru:
            title_ru = fallback_title
        if not author_ru:
            author_ru = fallback_author
        result = (title_ru[:180].strip(), author_ru[:80].strip())
    except Exception as e:
        logger.info("[LiveDubTitle] fallback title translation: %s", str(e)[:120])
        result = (fallback_title, fallback_author)

    _LIVEDUB_TITLE_CACHE[cache_key] = result
    return result


def _format_livedub_title_line(title_ru: str, author_ru: str) -> str:
    raw_title = normalize_title_text(title_ru) or str(title_ru or "").strip()
    author_ru = normalize_author_name(author_ru).strip()
    # If the light model already returned a complete line, normalize order:
    # author-first («Р. Ч. Спроул - True...») -> title-first.
    if raw_title and not author_ru and re.search(r"\s[-–—]\s|:\s", raw_title):
        split_title, split_author = _split_title_author_line(raw_title)
        if split_author:
            # Уже готовая строка от light-model: не sentence-case'им весь title,
            # чтобы не портить собственные имена/богословские термины.
            return f"{split_title.strip()} - {split_author}".strip(" -")
        return normalize_common_typos(raw_title).strip()
    title_ru = title_case_fragment(raw_title).strip()
    if title_ru and author_ru and author_ru.lower() not in title_ru.lower():
        return f"{title_ru} - {author_ru}"
    return title_ru or author_ru or "Переведённое видео"
