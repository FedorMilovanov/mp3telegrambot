#!/usr/bin/env python3
"""Publication policy for LiveDub titles, captions and audio metadata.

The processing pipeline can legitimately keep an English ``real_title`` from the
main audio analysis.  That value used to win before the lightweight title
translator was reached, so both the LiveDub video and its MP3 companion could be
published with an English title.  This module owns pure title/author and caption normalization helpers used by
the explicit LiveDub publication path. It does not intercept Telegram methods or
rebind pipeline functions at runtime.
"""
from __future__ import annotations

from core.media_title_policy import canonical_media_title

import asyncio
import html
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_TITLE_CACHE: dict[str, tuple[str, str]] = {}

# Local additions that are not yet guaranteed to exist in the shared registry.
_AUTHOR_OVERRIDES = {
    "tim conway": "Тим Конвей",
    "r.c. sproul": "Р. Ч. Спроул",
    "r. c. sproul": "Р. Ч. Спроул",
    "rc sproul": "Р. Ч. Спроул",
    "john macarthur": "Джон МакАртур",
    "paul washer": "Пол Вошер",
    "abner chou": "Абнер Чау",
    "costi hinn": "Кости Хинн",
}

_VIDEO_SERVICE_LINES = {
    "🎬 живые голоса яндекса",
    "🎬 перевод яндекса (обычные голоса)",
}
_AUDIO_CAPTION_REPLACEMENTS = {
    "🎧 Аудиоверсия русского перевода Яндекса": "🎧 Русская аудиоверсия",
    "🎧 Чистая аудиодорожка русского перевода Яндекса": "🎧 Русская аудиоверсия",
    "🎧 Аудиоверсия финального дубляжа (русский перевод + тихий оригинал)":
        "🎧 Русская аудиоверсия с тихим оригиналом",
}

# These words must never be capitalised merely because an English-style title
# formatter touched the Russian heading.
_RU_SERVICE_WORDS = {
    "а", "без", "в", "во", "для", "до", "за", "и", "из", "или", "к", "ко",
    "на", "над", "не", "но", "о", "об", "от", "по", "под", "при", "про", "с",
    "со", "у", "через",
}


def _enabled() -> bool:
    return (
        str(__import__("os").getenv("LIVEDUB_OUTPUT_POLICY", "1") or "1")
        .strip().lower() in _TRUE
    )


def _plain(value: Any, limit: int = 500) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-—–|;:")[:limit].strip()


def _canonical_author(value: str) -> str:
    text = _plain(value, 120)
    if not text:
        return ""
    override = _AUTHOR_OVERRIDES.get(text.casefold())
    if override:
        return override
    try:
        from core.person_names import canonical_person_name, known_author_from_text

        known = known_author_from_text(text)
        normalized = canonical_person_name(known or text)
        return _AUTHOR_OVERRIDES.get(normalized.casefold(), normalized)
    except Exception:
        return text


def _looks_like_author(value: str) -> bool:
    text = _plain(value, 120)
    if not text:
        return False
    if text.casefold() in _AUTHOR_OVERRIDES:
        return True
    try:
        from core.person_names import (
            known_author_from_text,
            known_ru_author_from_text,
            looks_like_author_list,
        )

        return bool(
            known_author_from_text(text)
            or known_ru_author_from_text(text)
            or looks_like_author_list(text)
        )
    except Exception:
        words = text.split()
        return 2 <= len(words) <= 6 and all(word[:1].isupper() for word in words)


def _split_title_author(line: str) -> tuple[str, str]:
    text = _plain(line, 280)
    if not text:
        return "", ""
    for separator in (" - ", " — ", " – ", " | "):
        if separator not in text:
            continue
        left, right = [part.strip() for part in text.rsplit(separator, 1)]
        if left and right and (_looks_like_author(right) or right.casefold() in _AUTHOR_OVERRIDES):
            return left, _canonical_author(right)
        if left and right and (_looks_like_author(left) or left.casefold() in _AUTHOR_OVERRIDES):
            return right, _canonical_author(left)
    return text, ""


def _has_cyrillic_title(line: str) -> bool:
    title, _author = _split_title_author(line)
    return bool(re.search(r"[А-Яа-яЁё]", title))


def _russian_heading_case(value: str) -> str:
    """Apply the canonical project title policy at the output owner."""
    return canonical_media_title(value)


def _clean_video_caption(value: Any) -> str:
    caption = str(value or "")
    if not caption:
        return caption
    lines: list[str] = []
    for line in caption.splitlines():
        visible = _plain(line, 300).casefold()
        if visible in _VIDEO_SERVICE_LINES:
            continue
        lines.append(line.rstrip())
    # Collapse the blank line that can remain after deleting the service label.
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def _clean_audio_caption(value: Any) -> str:
    caption = str(value or "")
    for source, replacement in _AUDIO_CAPTION_REPLACEMENTS.items():
        caption = caption.replace(source, replacement)
    return caption


async def _ensure_html_caption_title_ru(caption: str) -> str:
    match = re.search(r"<b>(.*?)</b>", str(caption or ""), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return caption
    raw_line = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
    raw_line = re.sub(r"\s+", " ", raw_line).strip()
    if not raw_line:
        return caption

    translated = await _translate_title_line(raw_line)
    if not translated:
        return caption
    title, author = translated
    final_line = f"{title} - {author}" if author else title
    escaped = html.escape(final_line, quote=False)
    return caption[:match.start(1)] + escaped + caption[match.end(1):]


def _is_livedub_audio_caption(value: Any) -> bool:
    low = _plain(value, 300).casefold()
    return any(
        marker in low
        for marker in (
            "аудиоверсия русского перевода яндекса",
            "чистая аудиодорожка русского перевода яндекса",
            "аудиоверсия финального дубляжа",
            "русская аудиоверсия",
        )
    )
