#!/usr/bin/env python3
"""Lightweight info cards for ENG Quick / LiveDub videos.

Purpose: produce small text assets (Telegram description, YouTube description,
compact subtitle-like bullets) without running the heavy audio-analysis pipeline.
Uses a cheap/light Gemini model by default: GEMINI_LIGHT_MODEL=gemini-3.1-flash-lite.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.globals import GEMINI_CLIENTS, make_text_config_smart
from core.database import GEMINI_MODEL
from core.text_utils import (
    _scrub_inline, _strip_meta_lines, normalize_common_typos,
    normalize_hashtag, normalize_title_text, title_case_fragment,
)
from converters.md_telegraph import safe_trim_caption
from services.livedub_qa import srt_to_timed_text

logger = logging.getLogger(__name__)

DEFAULT_LIGHT_MODEL = "gemini-3.1-flash-lite"


def livedub_info_enabled() -> bool:
    return os.getenv("LIVEDUB_INFO_CARD", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_light_model() -> str:
    return (os.getenv("GEMINI_LIGHT_MODEL", DEFAULT_LIGHT_MODEL) or DEFAULT_LIGHT_MODEL).strip()


def get_light_model_fallbacks() -> list[str]:
    raw = os.getenv("GEMINI_LIGHT_FALLBACK_MODELS", "gemini-3.1-flash-lite-preview,gemini-2.5-flash-lite").strip()
    out: list[str] = []
    for item in raw.split(","):
        model = item.strip()
        if model and model not in out and model != get_light_model():
            out.append(model)
    if os.getenv("GEMINI_LIGHT_ALLOW_MAIN_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}:
        if GEMINI_MODEL and GEMINI_MODEL not in out and GEMINI_MODEL != get_light_model():
            out.append(GEMINI_MODEL)
    return out


def livedub_info_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "telegram_description": {"type": "string"},
            "youtube_title": {"type": "string"},
            "youtube_description": {"type": "string"},
            "compact_subtitles": {"type": "array", "items": {"type": "string"}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["telegram_description", "youtube_title", "youtube_description", "compact_subtitles", "hashtags"],
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


def _fallback_card(title_line: str) -> dict:
    title = _safe_text(title_line, 180) or "Переведённое видео"
    return {
        "telegram_description": title,
        "youtube_title": title,
        "youtube_description": title,
        "compact_subtitles": [],
        "hashtags": [],
        "source": "metadata_fallback",
    }


def _normalize_card(data: dict, fallback_title: str) -> dict:
    fb = _fallback_card(fallback_title)
    if not isinstance(data, dict):
        return fb
    hashtags = data.get("hashtags") or []
    if not isinstance(hashtags, list):
        hashtags = []
    compact = data.get("compact_subtitles") or []
    if not isinstance(compact, list):
        compact = []
    out = {
        "telegram_description": _safe_text(data.get("telegram_description") or fb["telegram_description"], 520),
        "youtube_title": title_case_fragment(_safe_text(data.get("youtube_title") or fb["youtube_title"], 100)),
        "youtube_description": _safe_text(data.get("youtube_description") or fb["youtube_description"], 1200),
        "compact_subtitles": [_safe_text(x, 140) for x in compact[:6] if _safe_text(x, 140)],
        "hashtags": [],
        "source": "gemini_light",
    }
    for tag in hashtags[:8]:
        norm = normalize_hashtag(str(tag or ""))
        if norm and norm not in out["hashtags"]:
            out["hashtags"].append(norm)
    return out


async def build_livedub_info_card(title_line: str, dub_srt_path: Path | None = None, *, force: bool = False) -> dict | None:
    """Build a small reusable description pack for a translated LiveDub video.

    Returns a dict or None. Never raises.
    """
    if not (force or livedub_info_enabled()):
        return None
    fallback = _fallback_card(title_line)
    timed_text = ""
    try:
        if dub_srt_path and Path(dub_srt_path).exists():
            timed_text = srt_to_timed_text(Path(dub_srt_path), max_chars=7000)
    except Exception as e:
        logger.info("[LiveDubInfo] SRT read failed: %s", str(e)[:120])
        timed_text = ""

    if not GEMINI_CLIENTS:
        return fallback

    prompt = f"""
Ты готовишь короткие текстовые материалы для русскоязычной публикации переведённого видео.
Используй ТОЛЬКО данные ниже. Не выдумывай факты, имена, даты и цитаты.

Название/автор: {title_line}
Текст перевода с таймкодами (может быть пустым):
{timed_text[:7000]}

Верни строго JSON:
{{
  "telegram_description": "1-2 живых предложения: о чём ролик и почему его стоит посмотреть; без канцелярита",
  "youtube_title": "короткий YouTube title до 100 символов, формат Название - Автор если автор есть",
  "youtube_description": "2-4 предложения для YouTube: тема, главный тезис, польза зрителю; без выдуманных фактов",
  "compact_subtitles": ["4-6 коротких тезисов/субтитров по смыслу, до 100-120 символов"],
  "hashtags": ["до 6 русских/английских хэштегов без пробелов"]
}}
""".strip()
    try:
        last_error: Exception | None = None
        for model in [get_light_model(), *get_light_model_fallbacks()]:
            try:
                cfg = make_text_config_smart(
                    temperature=0.2,
                    max_output_tokens=1200,
                    model_name=model,
                    thinking_level="minimal",
                    response_mime_type="application/json",
                    response_schema=livedub_info_response_schema(),
                )
                resp = await GEMINI_CLIENTS[0].aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=cfg,
                )
                raw = _strip_json_fence(getattr(resp, "text", "") or "")
                data = json.loads(raw)
                card = _normalize_card(data, title_line)
                card["model"] = model
                return card
            except Exception as e:
                last_error = e
                logger.info("[LiveDubInfo] light model %s failed: %s", model, str(e)[:120])
        if last_error:
            raise last_error
    except Exception as e:
        logger.info("[LiveDubInfo] light model failed (%s) — fallback", str(e)[:160])
        return fallback


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

    lines: list[str] = ["✨ <b>Готовое описание к переводу</b>"]
    if tg:
        lines += ["", "📝 <b>Кратко для Telegram</b>", f"<i>{_h(tg)}</i>"]

    clean_compact = [_safe_text(x, 180) for x in compact[:8] if _safe_text(x, 180)]
    if clean_compact:
        lines += ["", "💬 <b>Компактные тезисы / субтитры</b>"]
        lines += ["• " + _h(x) for x in clean_compact]

    if yt_title or yt_desc or hashtags:
        yt_block_parts: list[str] = []
        if yt_desc:
            yt_block_parts.append(yt_desc)
        if hashtags:
            yt_block_parts.append(" ".join(hashtags))
        yt_block = "\n\n".join(yt_block_parts).strip()

        lines += ["", "▶️ <b>Для YouTube</b>"]
        if yt_title:
            lines += ["<b>Название:</b>", f"<code>{_h(yt_title)}</code>"]
        if yt_block:
            lines += ["<b>Описание:</b>", f"<pre>{_h(yt_block)}</pre>"]

    return safe_trim_caption("\n".join(lines).strip(), 3900)
