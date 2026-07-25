#!/usr/bin/env python3
"""Final user-facing policy for the two LiveDub MP3 variants.

Installed after the legacy publication/deep-audit wrappers. It consumes the
private companion marker once, publishes an explicit variant label, and then
passes a marker-free caption inward so older one-MP3 formatters cannot collapse
both files into an indistinguishable result.
"""
from __future__ import annotations

import html
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False

_CLEAN_MARKER = "чистая аудиодорожка русского перевода яндекса"
_MIXED_MARKER = "аудиоверсия финального дубляжа"
_LABELS = {
    "clean": "🎧 Чистый русский перевод",
    "mixed": "🎚 Финальный объединённый микс",
}


def _plain(value: Any) -> str:
    return " ".join(str(value or "").split())


def _variant(value: Any) -> str:
    low = _plain(value).casefold()
    if _CLEAN_MARKER in low:
        return "clean"
    if _MIXED_MARKER in low:
        return "mixed"
    return ""


def _is_local_upload(value: Any) -> bool:
    if isinstance(value, Path):
        return value.is_file()
    if isinstance(value, str):
        try:
            return Path(value).is_file()
        except (OSError, ValueError):
            return False
    return hasattr(value, "read")


def _variant_caption(label: str, body: str) -> str:
    heading = f"<b>{html.escape(label, quote=False)}</b>"
    return f"{heading}\n\n{body}" if body else heading


def _wrap_send_audio(cls: type) -> None:
    current = getattr(cls, "send_audio", None)
    if current is None or getattr(current, "_mp3bot_dual_audio_policy", False):
        return

    async def wrapped(self, *args, **kwargs):
        variant = _variant(kwargs.get("caption"))
        if not variant:
            return await current(self, *args, **kwargs)

        title = _plain(kwargs.get("title"))
        performer = _plain(kwargs.get("performer"))
        try:
            import services.livedub_publication as publication
            from services.livedub_publication_core import (
                build_publication_card,
                canonical_author,
                metadata_text,
            )

            source_line = f"{title} - {performer}" if performer else title
            card = await build_publication_card(
                source_line,
                publication._CURRENT_SOURCE_URL.get(),
            )
            kwargs["title"] = metadata_text(str(card.get("title") or title)) or None
            kwargs["performer"] = metadata_text(
                str(card.get("author") or canonical_author(performer))
            ) or None
            body = publication.format_audio_caption(card)
        except Exception as exc:
            logger.info("[LiveDubDualAudio] publication fallback: %s", str(exc)[:160])
            body = ""

        kwargs["caption"] = _variant_caption(_LABELS[variant], body)
        kwargs["parse_mode"] = "HTML"
        if not _is_local_upload(kwargs.get("audio")):
            kwargs.pop("filename", None)
        return await current(self, *args, **kwargs)

    wrapped._mp3bot_dual_audio_policy = True  # type: ignore[attr-defined]
    setattr(cls, "send_audio", wrapped)


def install_livedub_dual_audio_policy() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from telegram import Bot

        _wrap_send_audio(Bot)
        try:
            from telegram.ext import ExtBot

            if ExtBot.send_audio is not Bot.send_audio:
                _wrap_send_audio(ExtBot)
        except Exception as exc:
            logger.debug("[LiveDubDualAudio] ExtBot patch skipped: %s", exc)
        _INSTALLED = True
        logger.info(
            "🎧 LiveDub dual audio policy: clean RU + final combined mix published separately"
        )
