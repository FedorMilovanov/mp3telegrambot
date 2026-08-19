#!/usr/bin/env python3
"""Quality-first info cards for ENG Quick / LiveDub videos.

These fields are user-visible semantic output: Telegram/YouTube copy, compact
meaning summaries, theological terms and Scripture references. The module owns
the exact Gemini 3.7/HIGH semantic route but delegates transport/retry/capacity
to core.globals.gemini_generate, so it cannot start an independent retry storm.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.globals import GEMINI_CLIENTS, gemini_generate, make_text_config_smart
from core.text_utils import (
    _scrub_inline,
    _strip_meta_lines,
    normalize_common_typos,
    normalize_hashtag,
    title_case_fragment,
)
from services import livedub_info_presentation_policy as presentation_policy
from services.livedub_info_evidence import (
    full_srt_evidence,
    sampled_srt_to_timed_text,
    sanitize_card,
)

logger = logging.getLogger(__name__)

DEFAULT_INFO_MODEL = "gemini-3.7-flash"
DEFAULT_LIGHT_MODEL = DEFAULT_INFO_MODEL


def livedub_info_enabled() -> bool:
    return os.getenv("LIVEDUB_INFO_CARD", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def get_light_model() -> str:
    configured = (
        os.getenv("LIVEDUB_INFO_MODEL", DEFAULT_INFO_MODEL) or DEFAULT_INFO_MODEL
    ).strip()
    if configured != DEFAULT_INFO_MODEL:
        logger.warning(
            "[LiveDubInfo] refusing semantic model override %r; using %s",
            configured,
            DEFAULT_INFO_MODEL,
        )
    return DEFAULT_INFO_MODEL


def get_light_model_fallbacks() -> list[str]:
    configured = os.getenv("LIVEDUB_INFO_FALLBACK_MODELS", "").strip()
    if configured:
        logger.warning(
            "[LiveDubInfo] ignoring semantic fallback models %r; quality route is %s only",
            configured,
            DEFAULT_INFO_MODEL,
        )
    return []


def livedub_info_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "telegram_description": {"type": "string"},
            "youtube_title": {"type": "string"},
            "youtube_description": {"type": "string"},
            "compact_subtitles": {"type": "array", "items": {"type": "string"}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "key_theological_terms": {
                "type": "array", "items": {"type": "string"},
            },
            "scripture_references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "text_ru": {"type": "string"},
                    },
                    "required": ["ref", "text_ru"],
                },
            },
        },
        "required": [
            "telegram_description", "youtube_title", "youtube_description",
            "compact_subtitles", "hashtags", "key_theological_terms",
            "scripture_references",
        ],
    }


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_text(value: Any, limit: int = 1500) -> str:
    text = _scrub_inline(_strip_meta_lines(str(value or "").strip()))
    text = normalize_common_typos(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _fallback_card(title_line: str, source_url: str = "") -> dict:
    title = _safe_text(title_line, 180) or "Переведённое видео"
    return {
        "telegram_description": title,
        "youtube_title": title,
        "youtube_description": title,
        "compact_subtitles": [],
        "hashtags": [],
        "source_url": _safe_text(source_url, 500),
        "source": "metadata_fallback",
    }


def _normalize_card(data: dict, fallback_title: str, source_url: str = "") -> dict:
    fb = _fallback_card(fallback_title, source_url)
    if not isinstance(data, dict):
        return fb
    hashtags = data.get("hashtags") or []
    if not isinstance(hashtags, list):
        hashtags = []
    compact = data.get("compact_subtitles") or []
    if not isinstance(compact, list):
        compact = []
    scripture = data.get("scripture_references") or []
    if not isinstance(scripture, list):
        scripture = []
    terms = data.get("key_theological_terms") or []
    if not isinstance(terms, list):
        terms = []

    out = {
        "telegram_description": _safe_text(
            data.get("telegram_description") or fb["telegram_description"], 520
        ),
        "youtube_title": title_case_fragment(
            _safe_text(data.get("youtube_title") or fb["youtube_title"], 100)
        ),
        "youtube_description": _safe_text(
            data.get("youtube_description") or fb["youtube_description"], 1200
        ),
        "compact_subtitles": [
            _safe_text(x, 140) for x in compact[:6] if _safe_text(x, 140)
        ],
        "hashtags": [],
        "key_theological_terms": [
            _safe_text(x, 60) for x in terms[:5] if _safe_text(x, 60)
        ],
        "scripture_references": scripture[:5],
        "source_url": _safe_text(source_url or fb.get("source_url"), 500),
        "source": "gemini_quality",
    }
    for tag in hashtags[:8]:
        norm = normalize_hashtag(str(tag or ""))
        if norm and norm not in out["hashtags"]:
            out["hashtags"].append(norm)

    if fallback_title and " - " in fallback_title:
        author_part = fallback_title.split(" - ", 1)[1]
        author_tag = normalize_hashtag(author_part)
        if author_tag and author_tag not in out["hashtags"]:
            out["hashtags"].insert(0, author_tag)
    return out


def _gemini_clients_snapshot() -> tuple[Any, ...]:
    return tuple(GEMINI_CLIENTS)


async def _finalize_info_card(
    card: dict | None,
    *,
    title_line: str,
    source_url: str,
    evidence: str,
) -> dict:
    guarded = sanitize_card(card, str(title_line or ""), evidence)
    return await presentation_policy.apply_card_presentation(
        guarded,
        str(title_line or ""),
        source_url=source_url,
    )


async def build_livedub_info_card(
    title_line: str,
    dub_srt_path: Path | None = None,
    *,
    source_url: str = "",
    force: bool = False,
) -> dict | None:
    if not (force or livedub_info_enabled()):
        return None
    fallback = _fallback_card(title_line, source_url)
    timed_text = ""
    evidence = ""
    try:
        if dub_srt_path and Path(dub_srt_path).exists():
            srt_path = Path(dub_srt_path)
            timed_text = sampled_srt_to_timed_text(srt_path, max_chars=7000)
            evidence = full_srt_evidence(srt_path)
    except Exception as exc:
        logger.info("[LiveDubInfo] SRT read failed: %s", str(exc)[:120])

    clients = _gemini_clients_snapshot()
    if not clients:
        return await _finalize_info_card(
            fallback, title_line=title_line, source_url=source_url, evidence=evidence
        )

    prompt = f"""
Ты готовишь короткие текстовые материалы для русскоязычной публикации переведённого видео.
Ты — эксперт в реформатском богословии и библеистике.
Используй ТОЛЬКО данные ниже. Не выдумывай факты, имена, даты и цитаты.
Пиши по-русски. YouTube title тоже переведи на русский; английскими оставляй только
общеупотребимые термины/имена, если без них нельзя. Если исходник author-first
(«R.C. Sproul: True ...»), верни порядок строго «Название - Автор».
Известные имена: R.C. Sproul=Р. Ч. Спроул, John MacArthur=Джон МакАртур,
Paul Washer=Пол Вошер, Abner Chou=Абнер Чау, Costi Hinn=Кости Хинн.
В блоке key_theological_terms используй точные русские теологические эквиваленты.

Название/автор: {title_line}
Оригинал YouTube: {source_url}
Текст перевода с таймкодами (может быть пустым):
{timed_text[:7000]}

Верни строго JSON:
{{
  "telegram_description": "1-2 живых предложения: о чём ролик и почему его стоит посмотреть; без канцелярита",
  "youtube_title": "короткий YouTube title до 100 символов, формат Название - Автор если автор есть",
  "youtube_description": "2-4 предложения для YouTube: тема, главный тезис, польза зрителю; без выдуманных фактов",
  "compact_subtitles": ["4-6 коротких тезисов/субтитров по смыслу, до 100-120 символов"],
  "hashtags": ["до 6 русских/английских хэштегов без пробелов"],
  "key_theological_terms": ["3-5 ключевых богословских терминов из видео на русском"],
  "scripture_references": [{{"ref":"Иоанна 3:16","text_ru":"текст стиха на русском"}}]
}}
""".strip()

    model = get_light_model()
    cfg = make_text_config_smart(
        max_output_tokens=1200,
        model_name=model,
        thinking_level="high",
        response_mime_type="application/json",
        response_schema=livedub_info_response_schema(),
    )

    async def _call(client):
        return await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=cfg,
            ),
            timeout=90.0,
        )

    try:
        resp = await gemini_generate(list(clients), _call, model_name=model)
        raw = _strip_json_fence(getattr(resp, "text", "") or "")
        data = json.loads(raw)
        card = _normalize_card(data, title_line, source_url)
        card["model"] = model
        return await _finalize_info_card(
            card, title_line=title_line, source_url=source_url, evidence=evidence
        )
    except Exception as exc:
        logger.info(
            "[LiveDubInfo] shared Gemini route failed (%s: %s) — deterministic fallback",
            type(exc).__name__,
            str(exc)[:160],
        )
        return await _finalize_info_card(
            fallback, title_line=title_line, source_url=source_url, evidence=evidence
        )


def _h(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def format_livedub_info_message(card: dict) -> str:
    return presentation_policy.format_card_message(card)
