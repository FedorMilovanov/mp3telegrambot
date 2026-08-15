#!/usr/bin/env python3
"""Explicit LiveDub publication/delivery orchestration.

This module is deliberately *not* an installer.  It never mutates Telegram,
pipeline or service classes/functions.  ``pipelines.main_pipeline`` calls it at
the two real business boundaries: after a newly rendered LiveDub video is sent,
and after a cached LiveDub video is sent.

The coordinator owns the cross-cutting guarantees that used to be distributed
across a stack of runtime monkey-patches:

* ENG Full source MP3 is deferred until the LiveDub outcome is known;
* new dual-MP3 delivery is all-or-nothing and file IDs are committed only after
  both role-correct messages are visible;
* cached dual-MP3 delivery validates role identity and rolls back partial sends;
* a cached video is rolled back when its paired MP3 set is unusable;
* a new video remains useful if companion delivery fails, but its video file ID
  is never committed as a complete cached pair;
* audio publication metadata/captions are formatted explicitly rather than by
  intercepting ``Bot.send_audio``;
* request-local single-flight prevents duplicate companion delivery without
  replacing Telegram methods globally.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_SOURCE_LOCK = threading.Lock()
_COMPANION_LOCK = threading.Lock()
_COMPANION_INFLIGHT: dict[tuple[str, ...], Future[bool]] = {}
_COMPANION_SENT: dict[tuple[str, ...], float] = {}


def _truthy(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE


def _request_key(kind: str, chat_id: Any, reply_to: Any, identity: Any) -> tuple[str, ...]:
    return kind, str(chat_id or ""), str(reply_to or ""), str(identity or "")


def _prune_companion_success(now: float, ttl: int = 900) -> None:
    for key, saved_at in list(_COMPANION_SENT.items()):
        if now - saved_at > ttl:
            _COMPANION_SENT.pop(key, None)


async def _singleflight(key: tuple[str, ...], operation) -> bool:
    """Share one companion transaction per request and cache only real success."""
    now = time.monotonic()
    with _COMPANION_LOCK:
        _prune_companion_success(now)
        if key in _COMPANION_SENT:
            logger.info("[LiveDubDelivery] duplicate companion transaction suppressed: %s", key[0])
            return True
        pending = _COMPANION_INFLIGHT.get(key)
        if pending is None:
            pending = Future()
            _COMPANION_INFLIGHT[key] = pending
            leader = True
        else:
            leader = False

    if not leader:
        return bool(await asyncio.wrap_future(pending))

    try:
        success = bool(await operation())
    except BaseException:
        with _COMPANION_LOCK:
            if _COMPANION_INFLIGHT.get(key) is pending:
                _COMPANION_INFLIGHT.pop(key, None)
        if not pending.done():
            pending.set_result(False)
        raise

    with _COMPANION_LOCK:
        if _COMPANION_INFLIGHT.get(key) is pending:
            _COMPANION_INFLIGHT.pop(key, None)
        if success:
            _COMPANION_SENT[key] = time.monotonic()
    if not pending.done():
        pending.set_result(success)
    return success


async def delete_message_best_effort(bot: Any, chat_id: Any, message: Any) -> bool:
    message_id = getattr(message, "message_id", None)
    if not message_id or chat_id is None or not hasattr(bot, "delete_message"):
        return False
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        logger.warning(
            "[LiveDubDelivery] rollback delete failed chat=%s message=%s: %s",
            chat_id,
            message_id,
            str(exc)[:160],
        )
        return False


async def _rollback_messages(bot: Any, chat_id: Any, messages: list[Any]) -> int:
    deleted = 0
    for message in reversed(messages):
        deleted += int(await delete_message_best_effort(bot, chat_id, message))
    return deleted


def _path_identity(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _variant_caption(variant: str, body: str) -> str:
    import html

    labels = {
        "clean": "🎧 Чистый русский перевод",
        "mixed": "🎚 Финальный объединённый микс",
    }
    heading = f"<b>{html.escape(labels[variant], quote=False)}</b>"
    return f"{heading}\n\n{body}" if body else heading


async def _publication_audio_fields(card: dict[str, Any], variant: str) -> dict[str, Any]:
    from services.livedub_publication import format_audio_caption
    from services.livedub_publication_core import canonical_author, metadata_text

    title = metadata_text(str(card.get("title") or "Переведённое видео")) or "Переведённое видео"
    author = metadata_text(str(card.get("author") or canonical_author(""))) or ""
    body = format_audio_caption(card)
    return {
        "title": title,
        "performer": author or None,
        "caption": _variant_caption(variant, body),
        "parse_mode": "HTML",
    }


async def _send_local_variant_uncommitted(
    bot: Any,
    *,
    variant: str,
    source: Path,
    video_path: Path,
    publication_card: dict[str, Any],
    chat_id: Any,
    reply_to: Any,
    thumbnail: Any,
    reference_duration: int,
) -> tuple[Any, str, dict[str, Any]]:
    import services.livedub_audio_companion as companion

    ok, duration = await asyncio.to_thread(companion._probe_audio, source)
    if not ok:
        raise RuntimeError(f"{companion._VARIANT_LABELS[variant]}: MP3 не прошёл ffprobe")
    if not companion._duration_compatible(reference_duration, duration):
        raise RuntimeError(
            f"{companion._VARIANT_LABELS[variant]}: длительность {duration}с "
            f"не совпадает с видео {reference_duration}с"
        )

    fields = await _publication_audio_fields(publication_card, variant)
    filename = companion._safe_filename(video_path, fields["title"], variant)
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "audio": source,
        "filename": filename,
        "title": fields["title"],
        "performer": fields["performer"],
        "duration": duration,
        "caption": fields["caption"],
        "parse_mode": fields["parse_mode"],
        "reply_to_message_id": reply_to,
        "write_timeout": 600,
        "read_timeout": 600,
        "connect_timeout": 60,
    }
    thumb_path = companion._media_path(thumbnail)
    if thumb_path is not None:
        kwargs["thumbnail"] = thumb_path

    message = await bot.send_audio(**kwargs)
    audio_file_id = str(getattr(getattr(message, "audio", None), "file_id", "") or "").strip()
    if not audio_file_id:
        raise RuntimeError(
            f"{companion._VARIANT_LABELS[variant]}: Telegram не вернул audio.file_id"
        )
    cache_meta = {
        "title": fields["title"],
        "performer": fields["performer"] or "",
        "filename": filename,
        "duration": duration,
    }
    return message, audio_file_id, cache_meta


async def deliver_new_companions(
    bot: Any,
    *,
    chat_id: Any,
    video_path: Path,
    publication_card: dict[str, Any],
    reply_to: Any,
    thumbnail: Any,
    video_file_id: str,
) -> bool:
    """Deliver and commit a new clean/mixed companion set atomically."""
    if not _truthy("LIVEDUB_SEND_AUDIO"):
        return True

    async def operation() -> bool:
        import services.livedub_audio_companion as companion
        from services.livedub_audio_quality_guard import (
            is_derived_audio_artifact,
            select_clean_translation_mp3,
        )

        video_path_obj = Path(video_path)
        video_ok, video_duration = await asyncio.to_thread(companion._probe_audio, video_path_obj)
        if not video_ok:
            raise RuntimeError("финальное LiveDub-видео не содержит проверяемой аудиодорожки")

        dual = companion._dual_enabled()
        clean = await asyncio.to_thread(select_clean_translation_mp3, video_path_obj.parent)
        if clean is not None and is_derived_audio_artifact(clean):
            clean = None
        if dual and clean is None:
            raise RuntimeError(
                "режим двух MP3 включён, но чистая русская дорожка не найдена; "
                "один смешанный MP3 не считается полным комплектом"
            )

        mixed = await asyncio.to_thread(companion._extract_mix_mp3, video_path_obj)
        if dual:
            assert clean is not None
            if _path_identity(clean) == _path_identity(mixed):
                raise RuntimeError(
                    "чистый русский перевод и финальный микс указывают на один файл"
                )
            sources = [("clean", clean), ("mixed", mixed)]
        elif clean is not None:
            sources = [("clean", clean)]
        else:
            sources = [("mixed", mixed)]

        sent_messages: list[Any] = []
        pending_cache: list[tuple[str, str, dict[str, Any]]] = []
        try:
            for variant, source in sources:
                message, audio_file_id, meta = await _send_local_variant_uncommitted(
                    bot,
                    variant=variant,
                    source=Path(source),
                    video_path=video_path_obj,
                    publication_card=publication_card,
                    chat_id=chat_id,
                    reply_to=reply_to,
                    thumbnail=thumbnail,
                    reference_duration=video_duration,
                )
                sent_messages.append(message)
                pending_cache.append((variant, audio_file_id, meta))

            for variant, audio_file_id, meta in pending_cache:
                companion._cache_put_variant(video_file_id, variant, audio_file_id, **meta)
            if video_file_id:
                persisted = companion._cache_get(video_file_id) or {}
                variants = persisted.get("variants") or {}
                for variant, expected_id, _meta in pending_cache:
                    actual = str((variants.get(variant) or {}).get("audio_file_id") or "")
                    if actual != expected_id:
                        raise RuntimeError(
                            f"{variant}: persistent companion cache verification failed"
                        )
        except BaseException:
            deleted = await _rollback_messages(bot, chat_id, sent_messages)
            companion._cache_drop(video_file_id)
            logger.error(
                "[LiveDubDelivery] new companion transaction rolled back=%d/%d",
                deleted,
                len(sent_messages),
            )
            raise

        logger.info("[LiveDubDelivery] new companion transaction committed: %d/%d", len(sent_messages), len(sources))
        return bool(sources) and len(sent_messages) == len(sources)

    key = _request_key("new", chat_id, reply_to, Path(video_path))
    return await _singleflight(key, operation)


async def deliver_cached_companions(
    bot: Any,
    *,
    chat_id: Any,
    video_file_id: str,
    publication_card: dict[str, Any],
    reply_to: Any,
) -> bool:
    """Deliver a cached companion set atomically; stale role data fails closed."""
    if not _truthy("LIVEDUB_SEND_AUDIO"):
        return True

    async def operation() -> bool:
        import services.livedub_audio_companion as companion

        cached = companion._cache_get(video_file_id)
        variants = (cached or {}).get("variants") or {}
        if not variants:
            return False

        if companion._dual_enabled():
            required = list(companion._VARIANTS)
            if any(name not in variants for name in required):
                return False
        elif "clean" in variants:
            required = ["clean"]
        elif "mixed" in variants:
            required = ["mixed"]
        else:
            return False

        role_ids: list[str] = []
        for variant in required:
            meta = variants.get(variant)
            audio_file_id = str(meta.get("audio_file_id") if isinstance(meta, dict) else "").strip()
            if not audio_file_id:
                companion._cache_drop(video_file_id)
                raise RuntimeError(f"cached {variant} companion has no audio_file_id")
            role_ids.append(audio_file_id)
        if len(role_ids) > 1 and len(set(role_ids)) != len(role_ids):
            companion._cache_drop(video_file_id)
            raise RuntimeError("cached clean/mixed roles point to the same audio_file_id")

        messages: list[Any] = []
        try:
            for variant in required:
                meta = variants[variant]
                fields = await _publication_audio_fields(publication_card, variant)
                message = await bot.send_audio(
                    chat_id=chat_id,
                    audio=meta["audio_file_id"],
                    title=fields["title"],
                    performer=fields["performer"],
                    caption=fields["caption"],
                    parse_mode=fields["parse_mode"],
                    reply_to_message_id=reply_to,
                    write_timeout=300,
                    read_timeout=300,
                    connect_timeout=60,
                )
                messages.append(message)
        except BaseException:
            deleted = await _rollback_messages(bot, chat_id, messages)
            companion._cache_drop(video_file_id)
            logger.error(
                "[LiveDubDelivery] cached companion transaction rolled back=%d/%d",
                deleted,
                len(messages),
            )
            raise
        return len(messages) == len(required)

    key = _request_key("cached", chat_id, reply_to, video_file_id)
    return await _singleflight(key, operation)


@dataclass
class SourceAudioDeferral:
    """Explicit ENG Full source-MP3 fallback owned by one pipeline request."""

    bot: Any
    chat_id: Any
    reply_to: Any
    enabled: bool
    _audio_path: Path | None = None
    _send_kwargs: dict[str, Any] | None = None
    _timeout_task: asyncio.Task | None = None

    def _cleanup(self) -> None:
        task = self._timeout_task
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
        self._timeout_task = None
        path = self._audio_path
        self._audio_path = None
        self._send_kwargs = None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    async def send_or_defer(
        self,
        message: Any,
        *,
        audio: Any,
        fallback_path: Path | str | None = None,
        **kwargs: Any,
    ) -> Any:
        if not self.enabled:
            return await message.reply_audio(audio=audio, **kwargs)

        source = Path(fallback_path) if fallback_path else None
        if source is None or not source.is_file():
            name = getattr(audio, "name", None)
            if isinstance(name, str) and Path(name).is_file():
                source = Path(name)
        if source is None or not source.is_file():
            raise RuntimeError("ENG Full source MP3 cannot be deferred without a local file")

        root = Path(tempfile.gettempdir()) / "mp3bot_livedub_deferred"
        root.mkdir(parents=True, exist_ok=True)
        destination = root / (
            f"{self.chat_id}_{self.reply_to}_{uuid.uuid4().hex[:8]}_{source.name}"
        )
        await asyncio.to_thread(shutil.copy2, source, destination)

        send_kwargs = dict(kwargs)
        send_kwargs["chat_id"] = self.chat_id
        send_kwargs["audio"] = destination
        send_kwargs["reply_to_message_id"] = self.reply_to
        send_kwargs.pop("thumbnail", None)
        send_kwargs.pop("reply_parameters", None)

        with _SOURCE_LOCK:
            self._cleanup()
            self._audio_path = destination
            self._send_kwargs = send_kwargs
            timeout = max(300, int(os.getenv("LIVEDUB_AUDIO_DEDUPE_TIMEOUT", "2100") or "2100"))
            self._timeout_task = asyncio.create_task(self._timeout_flush(timeout))

        logger.info("[LiveDubDelivery] ENG Full source MP3 deferred: %s", source.name)
        return SimpleNamespace(audio=SimpleNamespace(file_id=""))

    async def _timeout_flush(self, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
            await self.flush("safety timeout")
        except asyncio.CancelledError:
            pass

    async def flush(self, reason: str) -> bool:
        kwargs = dict(self._send_kwargs or {})
        if not kwargs:
            return False
        try:
            await self.bot.send_audio(**kwargs)
            logger.info("[LiveDubDelivery] source MP3 fallback sent (%s)", reason)
            return True
        except Exception as exc:
            logger.warning(
                "[LiveDubDelivery] source MP3 fallback failed (%s): %s",
                reason,
                str(exc)[:180],
            )
            return False
        finally:
            self._cleanup()

    def discard(self, reason: str) -> None:
        if self._send_kwargs:
            logger.info("[LiveDubDelivery] source MP3 suppressed (%s)", reason)
        self._cleanup()

    @property
    def has_pending(self) -> bool:
        return bool(self._send_kwargs)


def create_source_audio_deferral(
    *,
    bot: Any,
    chat_id: Any,
    reply_to: Any,
    enabled: bool,
) -> SourceAudioDeferral:
    return SourceAudioDeferral(
        bot=bot,
        chat_id=chat_id,
        reply_to=reply_to,
        enabled=bool(enabled and _truthy("LIVEDUB_AUDIO_DEDUPE")),
    )
