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
from core.text_utils import normalize_common_typos, normalize_title_text
from services.livedub_qa import srt_to_timed_text

logger = logging.getLogger(__name__)

DEFAULT_LIGHT_MODEL = "gemini-3.1-flash-lite"


def livedub_info_enabled() -> bool:
    return os.getenv("LIVEDUB_INFO_CARD", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_light_model() -> str:
    return (os.getenv("GEMINI_LIGHT_MODEL", DEFAULT_LIGHT_MODEL) or DEFAULT_LIGHT_MODEL).strip()


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_text(value: Any, limit: int = 1500) -> str:
    text = normalize_common_typos(str(value or "").strip())
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
        "telegram_description": _safe_text(data.get("telegram_description") or fb["telegram_description"], 700),
        "youtube_title": _safe_text(data.get("youtube_title") or fb["youtube_title"], 100),
        "youtube_description": _safe_text(data.get("youtube_description") or fb["youtube_description"], 1800),
        "compact_subtitles": [_safe_text(x, 160) for x in compact[:8] if _safe_text(x, 160)],
        "hashtags": [],
        "source": "gemini_light",
    }
    for tag in hashtags[:8]:
        t = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]+", "", str(tag or "").strip().lstrip("#"))
        if t:
            out["hashtags"].append("#" + t)
    return out


async def build_livedub_info_card(title_line: str, dub_srt_path: Path | None = None) -> dict | None:
    """Build a small reusable description pack for a translated LiveDub video.

    Returns a dict or None. Never raises.
    """
    if not livedub_info_enabled():
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
  "telegram_description": "1-2 предложения для Telegram: о чём ролик и почему посмотреть",
  "youtube_title": "короткий YouTube title до 100 символов, формат Название - Автор если автор есть",
  "youtube_description": "2-4 предложения для YouTube description, без таймкодов если их мало",
  "compact_subtitles": ["5-8 коротких тезисов/субтитров по смыслу, не длиннее 120 символов"],
  "hashtags": ["до 6 хэштегов без пробелов"]
}}
""".strip()
    try:
        cfg = make_text_config_smart(
            temperature=0.2,
            max_output_tokens=1200,
            model_name=get_light_model(),
            thinking_level="minimal",
            response_mime_type="application/json",
        )
        resp = await GEMINI_CLIENTS[0].aio.models.generate_content(
            model=get_light_model(),
            contents=prompt,
            config=cfg,
        )
        raw = _strip_json_fence(getattr(resp, "text", "") or "")
        data = json.loads(raw)
        return _normalize_card(data, title_line)
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

    return "\n".join(lines).strip()[:3900]
