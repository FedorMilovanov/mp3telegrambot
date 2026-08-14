#!/usr/bin/env python3
"""Quality-first info cards for ENG Quick / LiveDub videos.

These fields are user-visible semantic output: Telegram/YouTube copy, compact
meaning summaries, theological terms and Scripture references.  The module owns
the production route directly so correctness does not depend on a later runtime
monkey-patch or import order: Gemini 3.6 Flash with HIGH thinking, no semantic
3.5/Lite fallback.
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

from core.globals import GEMINI_CLIENTS, make_text_config_smart
from core.text_utils import (
    _scrub_inline,
    _strip_meta_lines,
    normalize_common_typos,
    normalize_hashtag,
    normalize_title_text,
    title_case_fragment,
)
from converters.md_telegraph import safe_trim_caption
from services.livedub_qa import srt_to_timed_text

logger = logging.getLogger(__name__)

DEFAULT_INFO_MODEL = "gemini-3.6-flash"
# Compatibility alias for older diagnostics/tests that imported this symbol.
DEFAULT_LIGHT_MODEL = DEFAULT_INFO_MODEL


def livedub_info_enabled() -> bool:
    return os.getenv("LIVEDUB_INFO_CARD", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def get_light_model() -> str:
    """Return the approved semantic model (legacy helper name kept for callers)."""
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
    """Never downgrade user-visible info-card semantics to the utility lane."""
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
                "type": "array",
                "items": {"type": "string"},
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
            "telegram_description",
            "youtube_title",
            "youtube_description",
            "compact_subtitles",
            "hashtags",
            "key_theological_terms",
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
    """Return a request-local client order without mutating the shared registry."""
    return tuple(GEMINI_CLIENTS)


async def build_livedub_info_card(
    title_line: str,
    dub_srt_path: Path | None = None,
    *,
    source_url: str = "",
    force: bool = False,
) -> dict | None:
    """Build a quality-first reusable description pack for translated LiveDub."""
    if not (force or livedub_info_enabled()):
        return None
    fallback = _fallback_card(title_line, source_url)
    timed_text = ""
    try:
        if dub_srt_path and Path(dub_srt_path).exists():
            timed_text = srt_to_timed_text(Path(dub_srt_path), max_chars=7000)
    except Exception as exc:
        logger.info("[LiveDubInfo] SRT read failed: %s", str(exc)[:120])
        timed_text = ""

    clients = _gemini_clients_snapshot()
    if not clients:
        return fallback

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
  "scripture_references": [
    {{
      "ref": "ссылка на Писание (например, Иоанна 3:16)",
      "text_ru": "текст стиха на РУССКОМ языке (Синодальный перевод)"
    }}
  ]
}}
""".strip()

    models = [get_light_model(), *get_light_model_fallbacks()]
    last_error: Exception | None = None
    for client_index, client in enumerate(clients, start=1):
        for model in models:
            try:
                cfg = make_text_config_smart(
                    max_output_tokens=1200,
                    model_name=model,
                    thinking_level="high",
                    response_mime_type="application/json",
                    response_schema=livedub_info_response_schema(),
                )
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=cfg,
                    ),
                    timeout=90.0,
                )
                raw = _strip_json_fence(getattr(resp, "text", "") or "")
                data = json.loads(raw)
                card = _normalize_card(data, title_line, source_url)
                card["model"] = model
                return card
            except Exception as exc:
                last_error = exc
                logger.info(
                    "[LiveDubInfo] client %d model %s failed: %s",
                    client_index,
                    model,
                    str(exc)[:120],
                )

    if last_error:
        logger.info(
            "[LiveDubInfo] all clients/models failed (%s) — deterministic fallback",
            str(last_error)[:160],
        )
    return fallback


# Native request-local multi-client support is concurrency-safe.
build_livedub_info_card._mp3bot_all_clients = True  # type: ignore[attr-defined]


def _h(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def format_livedub_info_message(card: dict) -> str:
    """Pretty, Telegram-safe HTML message with copy-ready publication text."""
    if not isinstance(card, dict):
        return ""
    tg = _safe_text(card.get("telegram_description"), 700)
    yt_title = _safe_text(card.get("youtube_title"), 100)
    yt_desc = _safe_text(card.get("youtube_description"), 1800)
    compact = card.get("compact_subtitles") or []
    hashtags = [str(h) for h in (card.get("hashtags") or [])[:8] if str(h).strip()]
    source_url = _safe_text(card.get("source_url"), 500)
    scripture = card.get("scripture_references") or []

    lines: list[str] = ["✨ <b>Готовое описание к переводу</b>"]
    if tg:
        lines += ["", "📝 <b>Кратко для Telegram</b>", f"<i>{_h(tg)}</i>"]
    if source_url:
        lines += [
            f"🔗 <a href=\"{html.escape(source_url, quote=True)}\">Оригинал на YouTube</a>"
        ]

    clean_compact = [_safe_text(x, 180) for x in compact[:8] if _safe_text(x, 180)]
    if clean_compact:
        lines += ["", "💬 <b>Компактные тезисы / субтитры</b>"]
        lines += ["• " + _h(x) for x in clean_compact]

    terms = card.get("key_theological_terms") or []
    if terms:
        lines += ["", "🧠 <b>Богословские термины</b>"]
        tags = []
        for term in terms[:6]:
            tag_body = "".join(word.capitalize() for word in str(term).split())
            tags.append(f"#{tag_body}")
        lines.append(" ".join(tags))

    if scripture:
        lines += ["", "📖 <b>Упомянутые места Писания</b>"]
        for item in scripture[:5]:
            if not isinstance(item, dict):
                continue
            ref = _h(item.get("ref", ""))
            text = _h(item.get("text_ru", ""))
            if ref and text:
                lines.append(f"<b>{ref}</b>: {text}")
            elif ref:
                lines.append(f"<b>{ref}</b>")

    if yt_title or yt_desc or hashtags:
        yt_block_parts: list[str] = []
        if yt_desc:
            yt_block_parts.append(yt_desc)
        if source_url:
            yt_block_parts.append(f"Оригинал: {source_url}")
        if hashtags:
            yt_block_parts.append(" ".join(hashtags))
        yt_block = "\n\n".join(yt_block_parts).strip()

        lines += ["", "▶️ <b>Для YouTube</b>"]
        if yt_title:
            lines += ["<b>Название:</b>", f"<code>{_h(yt_title)}</code>"]
        if yt_block:
            lines += ["<b>Описание:</b>", f"<pre>{_h(yt_block)}</pre>"]

    return safe_trim_caption("\n".join(lines).strip(), 3900)
