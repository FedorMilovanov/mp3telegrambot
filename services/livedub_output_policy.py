#!/usr/bin/env python3
"""Publication policy for LiveDub titles, captions and audio metadata.

The processing pipeline can legitimately keep an English ``real_title`` from the
main audio analysis.  That value used to win before the lightweight title
translator was reached, so both the LiveDub video and its MP3 companion could be
published with an English title.  This runtime adapter adds a final, cheap title
translation pass and keeps provider/implementation labels out of user-facing
captions.

It is intentionally installed *after* ``main`` is imported and *before* the
LiveDub audio companion.  The companion can still recognise the original
internal caption marker, while the actual Telegram API receives the clean
publication caption produced here.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INSTALL_LOCK = threading.Lock()
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
    """Preserve proper names but force Russian function words to lowercase."""
    text = re.sub(r"\s+", " ", _plain(value, 220)).strip()
    if not text:
        return ""

    # Uppercase the first alphabetic character only; do not title-case the rest.
    chars = list(text)
    for index, char in enumerate(chars):
        if char.isalpha():
            chars[index] = char.upper()
            break
    text = "".join(chars)

    tokens = text.split(" ")
    for index in range(1, len(tokens)):
        token = tokens[index]
        match = re.match(r"^([^A-Za-zА-Яа-яЁё]*)([A-Za-zА-Яа-яЁё]+)(.*)$", token)
        if not match:
            continue
        prefix, word, suffix = match.groups()
        if word.casefold() in _RU_SERVICE_WORDS:
            tokens[index] = prefix + word.lower() + suffix
    return " ".join(tokens)


async def _translate_title_line(source_line: str) -> tuple[str, str] | None:
    source = _plain(source_line, 300)
    if not source:
        return None
    if _has_cyrillic_title(source):
        title, author = _split_title_author(source)
        return _russian_heading_case(title), _canonical_author(author)

    cache_key = source.casefold()
    cached = _TITLE_CACHE.get(cache_key)
    if cached:
        return cached

    try:
        import services.livedub_info_presentation as presentation

        # Make the same canonical spelling available to its parser/translator.
        presentation._AUTHOR_OVERRIDES.update(_AUTHOR_OVERRIDES)  # type: ignore[attr-defined]
        translated = await presentation._translate_title_second_chance(source)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.info("[LiveDubOutput] light title translation unavailable: %s", str(exc)[:160])
        translated = None

    if not translated:
        return None
    title, author = translated
    title = _russian_heading_case(title)
    author = _canonical_author(author)
    if not title or not re.search(r"[А-Яа-яЁё]", title):
        return None
    result = (title, author)
    _TITLE_CACHE[cache_key] = result
    return result


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


def _patch_pipeline_title() -> None:
    import pipelines.main_pipeline as pipeline

    original = getattr(pipeline, "_translate_livedub_title_for_caption", None)
    if original is None or getattr(original, "_mp3bot_output_policy", False):
        return

    async def wrapped(*args, **kwargs):
        title, author = await original(*args, **kwargs)
        current = f"{title} - {author}" if author else str(title or "")
        if _has_cyrillic_title(current):
            clean_title, split_author = _split_title_author(current)
            return _russian_heading_case(clean_title), _canonical_author(author or split_author)

        full_title = str(args[0] if args else kwargs.get("full_title") or "")
        parsed_title = str(args[1] if len(args) > 1 else kwargs.get("parsed_title") or "")
        parsed_performer = str(args[2] if len(args) > 2 else kwargs.get("parsed_performer") or "")
        source = full_title or (
            f"{parsed_title} - {parsed_performer}" if parsed_performer else parsed_title
        ) or current
        translated = await _translate_title_line(source)
        if translated:
            return translated
        return title, _canonical_author(author)

    wrapped._mp3bot_output_policy = True  # type: ignore[attr-defined]
    pipeline._translate_livedub_title_for_caption = wrapped


def _wrap_send_video(cls: type) -> None:
    original = getattr(cls, "send_video", None)
    if original is None or getattr(original, "_mp3bot_output_policy", False):
        return

    async def wrapped(self, *args, **kwargs):
        caption = str(kwargs.get("caption") or "")
        if caption:
            caption = await _ensure_html_caption_title_ru(caption)
            kwargs["caption"] = _clean_video_caption(caption)
        return await original(self, *args, **kwargs)

    wrapped._mp3bot_output_policy = True  # type: ignore[attr-defined]
    setattr(cls, "send_video", wrapped)


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


def _wrap_send_audio(cls: type) -> None:
    original = getattr(cls, "send_audio", None)
    if original is None or getattr(original, "_mp3bot_output_policy", False):
        return

    async def wrapped(self, *args, **kwargs):
        caption = kwargs.get("caption")
        livedub_audio = _is_livedub_audio_caption(caption)
        if caption is not None:
            kwargs["caption"] = _clean_audio_caption(caption)

        if livedub_audio:
            title = _plain(kwargs.get("title"), 200)
            performer = _plain(kwargs.get("performer"), 120)
            source = f"{title} - {performer}" if performer else title
            translated = await _translate_title_line(source)
            if translated:
                kwargs["title"], translated_author = translated
                kwargs["performer"] = translated_author or _canonical_author(performer) or None
        return await original(self, *args, **kwargs)

    wrapped._mp3bot_output_policy = True  # type: ignore[attr-defined]
    setattr(cls, "send_audio", wrapped)


def install_livedub_output_policy() -> None:
    """Install title and caption publication guards once."""
    if not _enabled():
        return
    with _INSTALL_LOCK:
        _patch_pipeline_title()
        from telegram import Bot

        _wrap_send_video(Bot)
        _wrap_send_audio(Bot)
        try:
            from telegram.ext import ExtBot

            if ExtBot.send_video is not Bot.send_video:
                _wrap_send_video(ExtBot)
            if ExtBot.send_audio is not Bot.send_audio:
                _wrap_send_audio(ExtBot)
        except Exception as exc:
            logger.debug("[LiveDubOutput] ExtBot patch skipped: %s", exc)
        logger.info("🇷🇺 LiveDub output policy: Russian titles + neutral captions enabled")


def harden_livedub_audio_dedupe() -> None:
    """Relax the source-MP3 detector after the dedupe adapter is installed.

    The old detector accepted only a narrow ASCII media-id filename pattern.
    Any harmless naming change made ENG Full send the English source MP3 before
    the Russian companion.  The directory itself is the reliable boundary.
    """
    try:
        import services.livedub_audio_dedupe as dedupe
        from core.globals import DOWNLOAD_DIR
    except Exception as exc:
        logger.info("[LiveDubOutput] audio dedupe hardening unavailable: %s", str(exc)[:160])
        return

    def is_main_source_mp3(path: Path) -> bool:
        try:
            candidate = Path(path)
            if candidate.suffix.casefold() != ".mp3":
                return False
            if candidate.resolve().parent != Path(DOWNLOAD_DIR).resolve():
                return False
            low = candidate.stem.casefold()
            excluded = ("ru-audio", "pro_dub", "livedub", "live_dub", "translation", "translated")
            return not any(token in low for token in excluded)
        except Exception:
            return False

    dedupe._is_main_source_mp3 = is_main_source_mp3
    logger.info("🎧 LiveDub audio dedupe: relaxed DOWNLOAD_DIR source detector enabled")
