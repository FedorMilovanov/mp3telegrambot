#!/usr/bin/env python3
"""Enforce an all-or-nothing contract for newly rendered LiveDub MP3 sets.

The base companion historically built its expected count from the sources that
happened to be found. In dual mode a missing clean Russian track therefore
silently degraded to one mixed MP3. A later Telegram failure could also leave the
first MP3 visible and cached even though the advertised two-file set had failed.

This runtime is installed after clean-track quality guards and before
quality-independent dedupe/deep-audit wrappers capture ``_send_new_audio``. It
requires two distinct, role-correct sources in dual mode, rolls back already-sent
MP3 messages best-effort on any failure, and commits Telegram file IDs only after
the whole set is visible.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


def _path_identity(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _is_valid_clean_source(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        from services.livedub_audio_quality_guard import is_derived_audio_artifact

        return not is_derived_audio_artifact(path)
    except Exception:
        return True


async def _send_variant_uncommitted(
    companion: Any,
    bot: Any,
    *,
    variant: str,
    source: Path,
    video_path: Path,
    title: str,
    performer: str,
    chat_id: Any,
    reply_to: Any,
    thumbnail: Any,
    reference_duration: int,
) -> tuple[Any, str, dict[str, Any]]:
    """Validate and send one local variant without mutating the file-id cache."""
    ok, duration = await asyncio.to_thread(companion._probe_audio, source)
    if not ok:
        raise RuntimeError(f"{companion._VARIANT_LABELS[variant]}: MP3 не прошёл ffprobe")
    if not companion._duration_compatible(reference_duration, duration):
        raise RuntimeError(
            f"{companion._VARIANT_LABELS[variant]}: длительность {duration}с "
            f"не совпадает с видео {reference_duration}с"
        )

    filename = companion._safe_filename(video_path, title, variant)
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "audio": source,
        "filename": filename,
        "title": title,
        "performer": performer or None,
        "duration": duration,
        "caption": companion._VARIANT_CAPTIONS[variant],
        "reply_to_message_id": reply_to,
        "write_timeout": 600,
        "read_timeout": 600,
        "connect_timeout": 60,
    }
    thumb_path = companion._media_path(thumbnail)
    if thumb_path is not None:
        kwargs["thumbnail"] = thumb_path

    message = await bot.send_audio(**kwargs)
    audio_file_id = str(getattr(getattr(message, "audio", None), "file_id", "") or "")
    cache_meta = {
        "title": title,
        "performer": performer,
        "filename": filename,
        "duration": duration,
    }
    logger.info(
        "[LiveDubNewAtomicity] %s MP3 sent but not committed yet: %s duration=%ss",
        variant,
        filename,
        duration,
    )
    return message, audio_file_id, cache_meta


async def _rollback_sent(bot: Any, chat_id: Any, messages: list[Any]) -> int:
    try:
        from services.livedub_cached_delivery_atomicity import _rollback_messages

        return await _rollback_messages(bot, chat_id, messages)
    except Exception as exc:
        logger.warning("[LiveDubNewAtomicity] rollback unavailable: %s", str(exc)[:180])
        return 0


def _install_strict_new_audio() -> None:
    import services.livedub_audio_companion as companion

    current = companion._send_new_audio
    if getattr(current, "_mp3bot_new_atomicity", False):
        return

    async def send_new_atomic(
        self,
        *,
        chat_id: Any,
        video_path: Path,
        caption: str,
        reply_to: Any,
        thumbnail: Any,
        video_file_id: str,
    ) -> bool:
        title, performer = companion._title_parts(caption, video_path.stem)
        video_ok, video_duration = await asyncio.to_thread(companion._probe_audio, video_path)
        if not video_ok:
            raise RuntimeError("финальное LiveDub-видео не содержит проверяемой аудиодорожки")

        dual = companion._dual_enabled()
        clean = await asyncio.to_thread(companion._find_clean_ru_track, video_path)
        if not _is_valid_clean_source(clean):
            clean = None
        if dual and clean is None:
            raise RuntimeError(
                "режим двух MP3 включён, но чистая русская дорожка не найдена; "
                "один смешанный MP3 не считается полным комплектом"
            )

        mixed = await asyncio.to_thread(companion._extract_mix_mp3, video_path)
        if dual:
            assert clean is not None
            if _path_identity(clean) == _path_identity(mixed):
                raise RuntimeError(
                    "чистый русский перевод и финальный микс указывают на один файл; "
                    "дубликат нельзя выдавать за две версии"
                )
            sources: list[tuple[str, Path]] = [("clean", clean), ("mixed", mixed)]
        elif clean is not None:
            sources = [("clean", clean)]
        else:
            sources = [("mixed", mixed)]

        sent_messages: list[Any] = []
        pending_cache: list[tuple[str, str, dict[str, Any]]] = []
        try:
            for variant, source in sources:
                message, audio_file_id, meta = await _send_variant_uncommitted(
                    companion,
                    self,
                    variant=variant,
                    source=source,
                    video_path=video_path,
                    title=title,
                    performer=performer,
                    chat_id=chat_id,
                    reply_to=reply_to,
                    thumbnail=thumbnail,
                    reference_duration=video_duration,
                )
                sent_messages.append(message)
                pending_cache.append((variant, audio_file_id, meta))
        except BaseException as exc:
            deleted = await _rollback_sent(self, chat_id, sent_messages)
            companion._cache_drop(video_file_id)
            logger.error(
                "[LiveDubNewAtomicity] new MP3 set rolled back=%d/%d after %s",
                deleted,
                len(sent_messages),
                type(exc).__name__,
            )
            raise

        for variant, audio_file_id, meta in pending_cache:
            if video_file_id and audio_file_id:
                companion._cache_put_variant(
                    video_file_id,
                    variant,
                    audio_file_id,
                    **meta,
                )

        logger.info(
            "[LiveDubNewAtomicity] complete new companion set committed: %d/%d",
            len(sent_messages),
            len(sources),
        )
        return len(sent_messages) == len(sources) and bool(sources)

    send_new_atomic._mp3bot_new_atomicity = True  # type: ignore[attr-defined]
    send_new_atomic.__wrapped__ = current  # type: ignore[attr-defined]
    companion._send_new_audio = send_new_atomic


def install_livedub_new_delivery_atomicity() -> None:
    """Install after quality guards and before wrappers capture callables."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_strict_new_audio()
        _INSTALLED = True
        logger.info(
            "🧾 LiveDub new delivery: strict distinct sources + transactional MP3 commit"
        )
