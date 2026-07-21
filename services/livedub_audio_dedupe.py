#!/usr/bin/env python3
"""Prevent duplicate MP3 delivery in ENG Full while keeping a safe fallback.

Why this exists
---------------
ENG Full runs the normal analysis pipeline, which sends the source MP3, and the
LiveDub audio companion later sends a second MP3 containing the Russian track.
To the user these look like duplicate files with the same title.

This runtime adapter defers only the *main source MP3* for ENG Full.  If the
LiveDub video and its Russian MP3 companion are delivered successfully, the
deferred source file is discarded.  If LiveDub fails, the companion fails, or
nothing finishes before the safety timeout, the original MP3 is sent instead.

ENG Quick is unaffected: it never reaches the normal source-MP3 branch, so it
continues to return the translated video plus one Russian MP3 companion.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_INSTALL_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_PENDING: dict[tuple[str, str], dict[str, Any]] = {}
_COMPANION_OK: set[tuple[str, str]] = set()

_MAIN_MP3_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}(?:_64)?\.mp3$", re.IGNORECASE)
_FAILURE_TEXT_MARKERS = (
    "живой перевод яндекса не получился",
    "перевод «живые голоса» не получился",
    "перевод \"живые голоса\" не получился",
    "не удалось отправить видео с переводом",
    "кэшированный перевод устарел",
    "translation not available",
)


def _enabled() -> bool:
    return (
        os.getenv("LIVEDUB_AUDIO_DEDUPE", "1").strip().lower() in _TRUE
        and os.getenv("LIVEDUB_SEND_AUDIO", "1").strip().lower() in _TRUE
    )


def _key(chat_id: Any, reply_to: Any) -> tuple[str, str]:
    return str(chat_id or ""), str(reply_to or "")


def _message_user_mode(message: Any) -> str:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not user_id:
        return ""
    try:
        from core.database import _db_conn

        with _db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (f"user_mode_{user_id}",),
            ).fetchone()
        return str(row[0] if row else "").strip().lower()
    except Exception as exc:
        logger.debug("[LiveDubAudioDedupe] user mode lookup failed: %s", exc)
        return ""


def _audio_argument(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if kwargs.get("audio") is not None:
        return kwargs.get("audio")
    return args[0] if args else None


def _path_from_media(value: Any) -> Path | None:
    if isinstance(value, Path):
        return value if value.is_file() else None
    if isinstance(value, str):
        path = Path(value)
        return path if path.is_file() else None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        path = Path(name)
        return path if path.is_file() else None
    return None


def _is_main_source_mp3(path: Path) -> bool:
    try:
        from core.globals import DOWNLOAD_DIR

        if path.resolve().parent != Path(DOWNLOAD_DIR).resolve():
            return False
    except Exception:
        return False
    return bool(_MAIN_MP3_RE.fullmatch(path.name))


def _temp_copy_path(source: Path, chat_id: Any, reply_to: Any) -> Path:
    root = Path(tempfile.gettempdir()) / "mp3bot_livedub_deferred"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{chat_id}_{reply_to}_{uuid.uuid4().hex[:8]}_{source.name}"


def _cleanup_entry(entry: dict[str, Any]) -> None:
    task = entry.get("timeout_task")
    current = asyncio.current_task()
    if task is not None and task is not current and not task.done():
        task.cancel()
    try:
        Path(entry.get("audio_path") or "").unlink(missing_ok=True)
    except OSError:
        pass


async def _send_pending(entry: dict[str, Any], reason: str) -> bool:
    kwargs = dict(entry.get("send_kwargs") or {})
    bot = entry.get("bot")
    if bot is None or not kwargs:
        _cleanup_entry(entry)
        return False
    try:
        await bot.send_audio(**kwargs)
        logger.info("[LiveDubAudioDedupe] source MP3 fallback sent (%s)", reason)
        return True
    except Exception as exc:
        logger.warning(
            "[LiveDubAudioDedupe] source MP3 fallback failed (%s): %s",
            reason,
            str(exc)[:180],
        )
        return False
    finally:
        _cleanup_entry(entry)


async def _flush(key: tuple[str, str], reason: str) -> bool:
    with _STATE_LOCK:
        entry = _PENDING.pop(key, None)
    if not entry:
        return False
    return await _send_pending(entry, reason)


def _discard(key: tuple[str, str], reason: str) -> None:
    with _STATE_LOCK:
        entry = _PENDING.pop(key, None)
    if entry:
        _cleanup_entry(entry)
        logger.info("[LiveDubAudioDedupe] source MP3 suppressed (%s)", reason)


async def _timeout_flush(key: tuple[str, str], delay: int) -> None:
    try:
        await asyncio.sleep(delay)
        await _flush(key, "safety timeout")
    except asyncio.CancelledError:
        pass


async def _defer_source_audio(message: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    source = _path_from_media(_audio_argument(args, kwargs))
    if source is None:
        raise RuntimeError("source MP3 path unavailable")

    chat_id = getattr(message, "chat_id", None) or getattr(
        getattr(message, "chat", None), "id", None
    )
    reply_to = getattr(message, "message_id", None)
    key = _key(chat_id, reply_to)
    destination = _temp_copy_path(source, chat_id, reply_to)
    await asyncio.to_thread(shutil.copy2, source, destination)

    send_kwargs = dict(kwargs)
    send_kwargs["chat_id"] = chat_id
    send_kwargs["audio"] = destination
    send_kwargs["reply_to_message_id"] = reply_to
    # The main pipeline passes an in-memory thumbnail which is closed later.
    # The fallback remains useful without it and is safer to persist this way.
    send_kwargs.pop("thumbnail", None)
    send_kwargs.pop("reply_parameters", None)

    try:
        bot = message.get_bot()
    except Exception:
        bot = getattr(message, "_bot", None)
    if bot is None:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Telegram bot instance unavailable")

    timeout = max(300, int(os.getenv("LIVEDUB_AUDIO_DEDUPE_TIMEOUT", "2100") or "2100"))
    entry: dict[str, Any] = {
        "bot": bot,
        "audio_path": destination,
        "send_kwargs": send_kwargs,
        "saved_at": time.time(),
    }
    entry["timeout_task"] = asyncio.create_task(_timeout_flush(key, timeout))

    with _STATE_LOCK:
        previous = _PENDING.pop(key, None)
        _PENDING[key] = entry
    if previous:
        _cleanup_entry(previous)

    logger.info(
        "[LiveDubAudioDedupe] ENG Full source MP3 deferred until LiveDub result: %s",
        source.name,
    )
    # main_pipeline only reads message.audio.file_id through getattr; an empty
    # placeholder keeps that code path safe without caching the source MP3.
    return SimpleNamespace(audio=SimpleNamespace(file_id=""))


def _wrap_reply_audio(cls: type) -> None:
    original = getattr(cls, "reply_audio", None)
    if original is None or getattr(original, "_mp3bot_livedub_dedupe", False):
        return

    async def wrapped(self, *args, **kwargs):
        if _enabled() and _message_user_mode(self) == "eng":
            source = _path_from_media(_audio_argument(args, kwargs))
            if source is not None and _is_main_source_mp3(source):
                try:
                    return await _defer_source_audio(self, args, kwargs)
                except Exception as exc:
                    logger.warning(
                        "[LiveDubAudioDedupe] defer failed, sending normally: %s",
                        str(exc)[:180],
                    )
        return await original(self, *args, **kwargs)

    wrapped._mp3bot_livedub_dedupe = True  # type: ignore[attr-defined]
    setattr(cls, "reply_audio", wrapped)


def _patch_companion_success_hooks() -> None:
    try:
        import services.livedub_audio_companion as companion
    except Exception as exc:
        logger.debug("[LiveDubAudioDedupe] companion import failed: %s", exc)
        return

    for name in ("_send_new_audio", "_send_cached_audio"):
        original = getattr(companion, name, None)
        if original is None or getattr(original, "_mp3bot_dedupe_hook", False):
            continue

        async def wrapped(*args, __original=original, **kwargs):
            ok = await __original(*args, **kwargs)
            if ok:
                success_key = _key(kwargs.get("chat_id"), kwargs.get("reply_to"))
                with _STATE_LOCK:
                    _COMPANION_OK.add(success_key)
            return ok

        wrapped._mp3bot_dedupe_hook = True  # type: ignore[attr-defined]
        setattr(companion, name, wrapped)


def _wrap_send_video(cls: type) -> None:
    original = getattr(cls, "send_video", None)
    if original is None or getattr(original, "_mp3bot_livedub_dedupe", False):
        return

    async def wrapped(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id")
        if chat_id is None and args:
            chat_id = args[0]
        reply_to = kwargs.get("reply_to_message_id")
        key = _key(chat_id, reply_to)
        try:
            result = await original(self, *args, **kwargs)
        except Exception:
            await _flush(key, "LiveDub video send failed")
            raise

        try:
            from services.livedub_audio_companion import _is_livedub_caption

            is_livedub = _is_livedub_caption(kwargs.get("caption"))
        except Exception:
            is_livedub = False
        if not is_livedub:
            return result

        with _STATE_LOCK:
            companion_ok = key in _COMPANION_OK
            _COMPANION_OK.discard(key)
        if companion_ok:
            _discard(key, "Russian MP3 companion delivered")
        else:
            await _flush(key, "Russian MP3 companion unavailable")
        return result

    wrapped._mp3bot_livedub_dedupe = True  # type: ignore[attr-defined]
    setattr(cls, "send_video", wrapped)


def _wrap_send_message(cls: type) -> None:
    original = getattr(cls, "send_message", None)
    if original is None or getattr(original, "_mp3bot_livedub_dedupe", False):
        return

    async def wrapped(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        text = str(kwargs.get("text") or "").lower()
        if text and any(marker in text for marker in _FAILURE_TEXT_MARKERS):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and args:
                chat_id = args[0]
            await _flush(
                _key(chat_id, kwargs.get("reply_to_message_id")),
                "LiveDub failure notice",
            )
        return result

    wrapped._mp3bot_livedub_dedupe = True  # type: ignore[attr-defined]
    setattr(cls, "send_message", wrapped)


def install_livedub_audio_dedupe() -> None:
    """Install after ``install_livedub_audio_companion`` in bot_new.py."""
    if not _enabled():
        return
    with _INSTALL_LOCK:
        from telegram import Bot, Message

        _patch_companion_success_hooks()
        _wrap_reply_audio(Message)
        _wrap_send_video(Bot)
        _wrap_send_message(Bot)
        try:
            from telegram.ext import ExtBot

            if ExtBot.send_video is not Bot.send_video:
                _wrap_send_video(ExtBot)
            if ExtBot.send_message is not Bot.send_message:
                _wrap_send_message(ExtBot)
        except Exception as exc:
            logger.debug("[LiveDubAudioDedupe] ExtBot patch skipped: %s", exc)
        logger.info(
            "🎧 LiveDub audio dedupe: ✅ one Russian MP3; source MP3 only on fallback"
        )
