#!/usr/bin/env python3
"""Install final LiveDub publication and QA integration guards."""
from __future__ import annotations

import contextvars
import logging
import threading
from pathlib import Path

from services.livedub_publication_core import (
    build_publication_card,
    canonical_author,
    canonical_title,
    compatible_info_card,
    metadata_text,
    plain,
    safe_audio_filename,
)
from services.livedub_qa_hardening import install_qa_hardening

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False
_MP3_COMPANION_FAILED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mp3bot_livedub_companion_failed", default=False
)


def _is_local_audio_upload(value) -> bool:
    if isinstance(value, Path):
        return value.is_file()
    if isinstance(value, str):
        try:
            return Path(value).is_file()
        except (OSError, ValueError):
            return False
    return hasattr(value, "read")


def _release_audio_claim(key) -> None:
    try:
        import services.livedub_quality_runtime as quality

        with quality._AUDIO_LOCK:
            quality._AUDIO_SENT.pop(key, None)
    except Exception as exc:
        logger.debug("[LiveDubDeepAudit] audio claim release skipped: %s", exc)


def _install_retry_safe_audio_claims() -> None:
    """A failed/no-op MP3 attempt must not poison dedupe for fifteen minutes."""
    import services.livedub_audio_companion as companion
    import services.livedub_quality_runtime as quality

    def wrap(name: str, kind: str) -> None:
        current = getattr(companion, name)
        if getattr(current, "_mp3bot_retry_safe_claim", False):
            return

        async def retry_safe(*args, **kwargs):
            key = quality._audio_key(kind, kwargs)
            try:
                result = await current(*args, **kwargs)
            except Exception:
                _release_audio_claim(key)
                _MP3_COMPANION_FAILED.set(True)
                raise
            if not result:
                _release_audio_claim(key)
                _MP3_COMPANION_FAILED.set(True)
            return result

        retry_safe._mp3bot_retry_safe_claim = True  # type: ignore[attr-defined]
        setattr(companion, name, retry_safe)

    wrap("_send_new_audio", "new")
    wrap("_send_cached_audio", "cached")


def _wrap_video_requires_mp3(cls: type) -> None:
    """Prevent an incomplete video-only result from entering the file_id cache."""
    current = getattr(cls, "send_video", None)
    if current is None or getattr(current, "_mp3bot_requires_mp3", False):
        return

    async def wrapped(self, *args, **kwargs):
        token = _MP3_COMPANION_FAILED.set(False)
        try:
            result = await current(self, *args, **kwargs)
            if _MP3_COMPANION_FAILED.get():
                # The video may already be visible, but raising here prevents the
                # pipeline from storing a video file_id that can never reproduce
                # its missing MP3. The next request will rebuild the complete pair.
                raise RuntimeError(
                    "LiveDub video delivered without its required paired MP3; "
                    "result is intentionally not cacheable"
                )
            return result
        finally:
            _MP3_COMPANION_FAILED.reset(token)

    wrapped._mp3bot_requires_mp3 = True  # type: ignore[attr-defined]
    setattr(cls, "send_video", wrapped)


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

    old_clean_audio_caption = output._clean_audio_caption

    def clean_audio_caption(value):
        low = plain(value, 500).casefold()
        if any(
            marker in low
            for marker in (
                "аудиоверсия русского перевода яндекса",
                "чистая аудиодорожка русского перевода яндекса",
                "аудиоверсия финального дубляжа",
                "русская аудиоверсия",
            )
        ):
            return ""
        return old_clean_audio_caption(value)

    output._clean_audio_caption = clean_audio_caption

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
                # Telegram file_id resends are not uploads; passing a synthetic
                # filename there is unnecessary and has varied across Bot API/PTB
                # versions. Only name a real local/file-like upload.
                if _is_local_audio_upload(kwargs.get("audio")):
                    kwargs["filename"] = safe_audio_filename(
                        str(card.get("title") or title)
                    )
                else:
                    kwargs.pop("filename", None)
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
    _wrap_video_requires_mp3(Bot)
    try:
        from telegram.ext import ExtBot

        if ExtBot.send_audio is not Bot.send_audio:
            wrap_audio(ExtBot)
        if ExtBot.send_video is not Bot.send_video:
            _wrap_video_requires_mp3(ExtBot)
    except Exception as exc:
        logger.debug("[LiveDubDeepAudit] ExtBot patch skipped: %s", exc)


def install_livedub_deep_audit() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_retry_safe_audio_claims()
        _install_publication()
        install_qa_hardening()
        _INSTALLED = True
        logger.info(
            "🧩 LiveDub deep audit: one lite publication call, bounded cache, "
            "complete video+MP3 caching and one-to-one audio QA enabled"
        )
