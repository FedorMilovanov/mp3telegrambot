#!/usr/bin/env python3
"""Best-effort rollback for incomplete cached LiveDub video + dual-MP3 sets.

Telegram file IDs can expire independently. The legacy cached path sent variants
sequentially and returned ``False`` when a later ID failed, leaving the already
sent video/MP3 visible before the pipeline rebuilt the pair. This runtime keeps the
existing cache format but treats the user-visible resend as one transaction:

* previously sent cached MP3 messages are deleted when any required variant fails;
* the cached video message is deleted before deep-audit raises the rebuild signal;
* the expired variant is removed from the file-id cache as before.

Deletion is best effort. Failure to delete never hides the original delivery error.
"""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


async def _delete_message_best_effort(bot: Any, chat_id: Any, message: Any) -> bool:
    message_id = getattr(message, "message_id", None)
    if not message_id or chat_id is None or not hasattr(bot, "delete_message"):
        return False
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        logger.warning(
            "[LiveDubCacheAtomicity] rollback delete failed chat=%s message=%s: %s",
            chat_id,
            message_id,
            str(exc)[:160],
        )
        return False


async def _rollback_messages(bot: Any, chat_id: Any, messages: list[Any]) -> int:
    deleted = 0
    for message in reversed(messages):
        deleted += int(await _delete_message_best_effort(bot, chat_id, message))
    return deleted


def _install_strict_cached_audio() -> None:
    import services.livedub_audio_companion as companion

    current = companion._send_cached_audio
    if getattr(current, "_mp3bot_cached_atomicity", False):
        return

    async def send_cached_atomic(
        self,
        *,
        chat_id: Any,
        video_file_id: str,
        reply_to: Any,
    ) -> bool:
        cached = companion._cache_get(video_file_id)
        variants = (cached or {}).get("variants") or {}
        if not variants:
            return False
        if companion._dual_enabled() and any(
            name not in variants for name in companion._VARIANTS
        ):
            logger.info(
                "[LiveDubCacheAtomicity] incomplete cache entry requires rebuild"
            )
            return False

        required = [name for name in companion._VARIANTS if name in variants]
        messages: list[Any] = []
        failures: list[str] = []
        for variant in required:
            meta = variants[variant]
            try:
                message = await self.send_audio(
                    chat_id=chat_id,
                    audio=meta["audio_file_id"],
                    title=meta.get("title") or None,
                    performer=meta.get("performer") or None,
                    caption=companion._VARIANT_CAPTIONS[variant],
                    reply_to_message_id=reply_to,
                    write_timeout=300,
                    read_timeout=300,
                    connect_timeout=60,
                )
                messages.append(message)
            except Exception as exc:
                companion._cache_drop_variant(video_file_id, variant)
                failures.append(
                    f"{companion._VARIANT_LABELS[variant]}: {str(exc)[:180]}"
                )
                logger.info(
                    "[LiveDubCacheAtomicity] cached %s file_id rejected: %s",
                    variant,
                    str(exc)[:180],
                )

        if failures:
            deleted = await _rollback_messages(self, chat_id, messages)
            raise RuntimeError(
                "кэшированный комплект MP3 неполон; "
                f"rollback={deleted}/{len(messages)}; "
                + "; ".join(failures)
            )
        return len(messages) == len(required) and bool(required)

    send_cached_atomic._mp3bot_cached_atomicity = True  # type: ignore[attr-defined]
    companion._send_cached_audio = send_cached_atomic


def _atomic_video_guard(cls: type) -> None:
    """Replacement installed into deep-audit before it patches Bot classes."""
    import services.livedub_deep_audit as deep

    current = getattr(cls, "send_video", None)
    if current is None or getattr(current, "_mp3bot_requires_mp3", False):
        return

    async def wrapped(self, *args, **kwargs):
        token = deep._MP3_COMPANION_FAILED.set(False)
        try:
            result = await current(self, *args, **kwargs)
            if deep._MP3_COMPANION_FAILED.get():
                video_value = kwargs.get("video")
                if deep._is_local_audio_upload(video_value):
                    return SimpleNamespace(video=SimpleNamespace(file_id=""))

                chat_id = kwargs.get("chat_id")
                if chat_id is None and args:
                    chat_id = args[0]
                rolled_back = await _delete_message_best_effort(
                    self, chat_id, result
                )
                logger.warning(
                    "[LiveDubCacheAtomicity] cached video rollback=%s before rebuild",
                    "ok" if rolled_back else "unavailable",
                )
                raise RuntimeError(
                    "cached LiveDub video has no usable paired MP3; "
                    "cached messages rolled back and pair must be rebuilt"
                )
            return result
        finally:
            deep._MP3_COMPANION_FAILED.reset(token)

    wrapped._mp3bot_requires_mp3 = True  # type: ignore[attr-defined]
    setattr(cls, "send_video", wrapped)


def install_livedub_cached_delivery_atomicity() -> None:
    """Install after audio companion and before deep-audit captures callables."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_strict_cached_audio()

        import services.livedub_deep_audit as deep

        deep._wrap_video_requires_mp3 = _atomic_video_guard
        _INSTALLED = True
        logger.info(
            "♻️ LiveDub cached delivery: transactional video + dual-MP3 rollback"
        )
