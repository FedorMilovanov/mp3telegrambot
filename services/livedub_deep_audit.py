#!/usr/bin/env python3
"""Install final LiveDub publication and QA integration guards."""
from __future__ import annotations

import logging
import threading

from services.livedub_publication_core import (
    build_publication_card,
    canonical_author,
    canonical_title,
    compatible_info_card,
    metadata_text,
    plain,
)
from services.livedub_qa_hardening import install_qa_hardening

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


def _install_publication() -> None:
    import services.livedub_publication as publication

    publication._canonical_title = canonical_title
    publication.build_publication_card = build_publication_card

    # The title helper and the old ENG Quick info card now share the same cache.
    from services import livedub_info as info

    current_build = info.build_livedub_info_card
    current_format = info.format_livedub_info_message
    if not getattr(current_build, "_mp3bot_deep_publication", False):
        async def unified_build(
            title_line, dub_srt_path=None, *, source_url="", force=False
        ):
            active_url = publication._source_url(
                source_url or publication._CURRENT_SOURCE_URL.get()
            )
            if active_url or force:
                card = await build_publication_card(str(title_line or ""), active_url)
                return compatible_info_card(card)
            return await current_build(
                title_line,
                dub_srt_path,
                source_url=source_url,
                force=force,
            )

        def unified_format(card: dict) -> str:
            if isinstance(card, dict) and card.get("source") == "deep_publication":
                return ""
            return current_format(card)

        unified_build._mp3bot_deep_publication = True  # type: ignore[attr-defined]
        unified_format._mp3bot_deep_publication = True  # type: ignore[attr-defined]
        info.build_livedub_info_card = unified_build
        info.format_livedub_info_message = unified_format

    import services.livedub_output_policy as output

    async def unified_translate(source_line: str):
        card = await build_publication_card(
            str(source_line or ""), publication._CURRENT_SOURCE_URL.get()
        )
        return str(card.get("title") or ""), str(card.get("author") or "")

    unified_translate._mp3bot_deep_publication = True  # type: ignore[attr-defined]
    output._translate_title_line = unified_translate

    # Intercept the internal marker before the older wrappers and bound metadata.
    from telegram import Bot

    def wrap_audio(cls: type) -> None:
        current = getattr(cls, "send_audio", None)
        if current is None or getattr(current, "_mp3bot_deep_publication", False):
            return

        async def wrapped(self, *args, **kwargs):
            if publication._is_livedub_audio_caption(kwargs.get("caption")):
                title = plain(kwargs.get("title"), 240)
                performer = plain(kwargs.get("performer"), 120)
                source_line = f"{title} - {performer}" if performer else title
                card = await build_publication_card(
                    source_line, publication._CURRENT_SOURCE_URL.get()
                )
                kwargs["title"] = metadata_text(
                    str(card.get("title") or title)
                ) or None
                kwargs["performer"] = metadata_text(
                    str(card.get("author") or canonical_author(performer))
                ) or None
                caption = publication.format_audio_caption(card)
                if caption:
                    kwargs["caption"], kwargs["parse_mode"] = caption, "HTML"
                else:
                    kwargs.pop("caption", None)
                    kwargs.pop("parse_mode", None)
            return await current(self, *args, **kwargs)

        wrapped._mp3bot_deep_publication = True  # type: ignore[attr-defined]
        setattr(cls, "send_audio", wrapped)

    wrap_audio(Bot)
    try:
        from telegram.ext import ExtBot

        if ExtBot.send_audio is not Bot.send_audio:
            wrap_audio(ExtBot)
    except Exception as exc:
        logger.debug("[LiveDubDeepAudit] ExtBot patch skipped: %s", exc)


def install_livedub_deep_audit() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_publication()
        install_qa_hardening()
        _INSTALLED = True
        logger.info(
            "🧩 LiveDub deep audit: one lite publication call, bounded cache, "
            "safe MP3 metadata and one-to-one audio QA enabled"
        )
