#!/usr/bin/env python3
"""Send a Russian MP3 companion after each successful LiveDub video.

ENG Quick previously returned immediately after the translated video, so the
project's normal MP3 branch was never reached.  This adapter attaches to the
actual ``Bot.send_video`` call instead of duplicating the large pipeline:

* it runs only for captions that explicitly identify Yandex LiveDub;
* it prefers the clean Russian track retained by the pro-mix;
* otherwise it extracts the final translated mix to an atomic MP3;
* cloud mode reuses ``cloud_media_fallback`` for byte-safe audio compression;
* Telegram video/audio file_ids are paired so cached video re-sends also return
  the matching MP3 after a process restart.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_INSTALL_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_LIVEDUB_MARKERS = (
    "живые голоса яндекса",
    "перевод яндекса (обычные голоса)",
)


def _enabled() -> bool:
    return os.getenv("LIVEDUB_SEND_AUDIO", "1").strip().lower() in _TRUE


def _media_path(value: Any) -> Path | None:
    if isinstance(value, Path):
        return value if value.is_file() else None
    if isinstance(value, str):
        path = Path(value)
        return path if path.is_file() else None
    return None


def _is_livedub_caption(value: Any) -> bool:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text).lower()
    return any(marker in text for marker in _LIVEDUB_MARKERS)


def _title_parts(caption: str, fallback: str) -> tuple[str, str]:
    match = re.search(r"<b>(.*?)</b>", str(caption or ""), flags=re.IGNORECASE | re.DOTALL)
    line = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1) if match else ""))
    line = re.sub(r"\s+", " ", line).strip() or fallback
    for separator in (" - ", " — ", " – "):
        if separator in line:
            title, performer = line.rsplit(separator, 1)
            if title.strip() and performer.strip() and len(performer.strip()) <= 100:
                return title.strip()[:180], performer.strip()[:100]
    return line[:180], ""


def _safe_filename(video_path: Path, title: str) -> str:
    stem = str(title or video_path.stem or "Переведённое аудио")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")[:140] or "Переведённое аудио"
    return f"{stem}.mp3"


def _probe_audio(path: Path) -> tuple[bool, int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.is_file() or path.stat().st_size <= 1024:
        return False, 0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return False, 0
        data = json.loads(proc.stdout or "{}")
        has_audio = any(stream.get("codec_type") == "audio" for stream in (data.get("streams") or []))
        duration = int(round(float((data.get("format") or {}).get("duration") or 0)))
        return bool(has_audio and duration > 0), max(0, duration)
    except Exception:
        return False, 0


def _find_clean_ru_track(video_path: Path) -> Path | None:
    try:
        from services.livedub_mix import find_pro_tracks

        _original, russian = find_pro_tracks(video_path.parent)
        if russian and russian.is_file() and russian.suffix.lower() == ".mp3":
            ok, _duration = _probe_audio(russian)
            if ok:
                return russian
    except Exception as exc:
        logger.debug("[LiveDubAudio] clean RU track lookup failed: %s", exc)
    return None


def _extract_mix_mp3(video_path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден")
    output = video_path.with_name(f"{video_path.stem}.ru-audio.mp3")
    temp = output.with_name(f".{output.stem}.{os.getpid()}.part.mp3")
    temp.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i", str(video_path),
                "-map", "0:a:0",
                "-vn",
                "-map_metadata", "0",
                "-c:a", "libmp3lame",
                "-b:a", os.getenv("LIVEDUB_AUDIO_BITRATE", "160k").strip() or "160k",
                "-ar", "44100",
                str(temp),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(900, int((_probe_audio(video_path)[1] or 0) * 2)),
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg error")[-700:])
        ok, _duration = _probe_audio(temp)
        if not ok:
            raise RuntimeError("созданный MP3 не прошёл ffprobe")
        os.replace(temp, output)
        return output
    finally:
        temp.unlink(missing_ok=True)


def _cache_path() -> Path:
    root = os.getenv("LOCALAPPDATA", "").strip()
    base = Path(root) / "MP3Bot" if root else Path.home() / ".mp3bot"
    return base / "livedub-audio-file-ids.json"


def _load_cache() -> dict[str, dict[str, Any]]:
    path = _cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: dict[str, dict[str, Any]]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        newest = sorted(
            data.items(),
            key=lambda item: float((item[1] or {}).get("saved_at") or 0),
            reverse=True,
        )[:500]
        payload = dict(newest)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        logger.debug("[LiveDubAudio] cache save failed: %s", exc)


def _cache_get(video_file_id: str) -> dict[str, Any] | None:
    if not video_file_id:
        return None
    with _CACHE_LOCK:
        value = _load_cache().get(video_file_id)
        return dict(value) if isinstance(value, dict) else None


def _cache_put(video_file_id: str, audio_file_id: str, **meta: Any) -> None:
    if not video_file_id or not audio_file_id:
        return
    with _CACHE_LOCK:
        data = _load_cache()
        data[video_file_id] = {
            "audio_file_id": audio_file_id,
            "saved_at": time.time(),
            **meta,
        }
        _save_cache(data)


def _cache_drop(video_file_id: str) -> None:
    if not video_file_id:
        return
    with _CACHE_LOCK:
        data = _load_cache()
        if data.pop(video_file_id, None) is not None:
            _save_cache(data)


async def _send_cached_audio(self, *, chat_id: Any, video_file_id: str, reply_to: Any) -> bool:
    cached = _cache_get(video_file_id)
    if not cached or not cached.get("audio_file_id"):
        return False
    try:
        await self.send_audio(
            chat_id=chat_id,
            audio=cached["audio_file_id"],
            title=cached.get("title") or None,
            performer=cached.get("performer") or None,
            caption="🎧 Аудиоверсия русского перевода Яндекса",
            reply_to_message_id=reply_to,
            write_timeout=300,
            read_timeout=300,
            connect_timeout=60,
        )
        return True
    except Exception as exc:
        logger.info("[LiveDubAudio] cached audio file_id expired: %s", str(exc)[:180])
        _cache_drop(video_file_id)
        return False


async def _send_new_audio(
    self,
    *,
    chat_id: Any,
    video_path: Path,
    caption: str,
    reply_to: Any,
    thumbnail: Any,
    video_file_id: str,
) -> bool:
    title, performer = _title_parts(caption, video_path.stem)
    source = _find_clean_ru_track(video_path)
    pure_russian = source is not None
    if source is None:
        source = await asyncio.to_thread(_extract_mix_mp3, video_path)
    ok, duration = await asyncio.to_thread(_probe_audio, source)
    if not ok:
        raise RuntimeError("аудиодорожка не прошла проверку целостности")

    audio_caption = (
        "🎧 Чистая аудиодорожка русского перевода Яндекса"
        if pure_russian
        else "🎧 Аудиоверсия финального дубляжа (русский перевод + тихий оригинал)"
    )
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "audio": source,
        "filename": _safe_filename(video_path, title),
        "title": title,
        "performer": performer or None,
        "duration": duration,
        "caption": audio_caption,
        "reply_to_message_id": reply_to,
        "write_timeout": 600,
        "read_timeout": 600,
        "connect_timeout": 60,
    }
    thumb_path = _media_path(thumbnail)
    if thumb_path is not None:
        kwargs["thumbnail"] = thumb_path
    audio_message = await self.send_audio(**kwargs)
    audio_file_id = getattr(getattr(audio_message, "audio", None), "file_id", "")
    if video_file_id and audio_file_id:
        _cache_put(
            video_file_id,
            audio_file_id,
            title=title,
            performer=performer,
            filename=kwargs["filename"],
        )
    logger.info("[LiveDubAudio] MP3 sent: %s (pure_ru=%s)", kwargs["filename"], pure_russian)
    return True


def _wrap_send_video(cls: type) -> None:
    original = getattr(cls, "send_video", None)
    if original is None or getattr(original, "_mp3bot_livedub_audio", False):
        return

    async def wrapped(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if not _enabled() or not _is_livedub_caption(kwargs.get("caption")):
            return result

        mutable = list(args)
        video_value = kwargs.get("video")
        if video_value is None and len(mutable) > 1:
            video_value = mutable[1]
        chat_id = kwargs.get("chat_id")
        if chat_id is None and mutable:
            chat_id = mutable[0]
        reply_to = kwargs.get("reply_to_message_id")
        video_file_id = getattr(getattr(result, "video", None), "file_id", "")
        video_path = _media_path(video_value)

        try:
            if video_path is not None:
                await _send_new_audio(
                    self,
                    chat_id=chat_id,
                    video_path=video_path,
                    caption=str(kwargs.get("caption") or ""),
                    reply_to=reply_to,
                    thumbnail=kwargs.get("thumbnail"),
                    video_file_id=video_file_id,
                )
            else:
                await _send_cached_audio(
                    self,
                    chat_id=chat_id,
                    video_file_id=video_file_id or str(video_value or ""),
                    reply_to=reply_to,
                )
        except Exception as exc:
            logger.exception("[LiveDubAudio] MP3 companion failed: %s", exc)
            try:
                await self.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ Видео с переводом отправлено, но отдельный MP3 создать не удалось. "
                        f"Причина: {str(exc)[:220]}"
                    ),
                    reply_to_message_id=reply_to,
                )
            except Exception:
                pass
        return result

    wrapped._mp3bot_livedub_audio = True  # type: ignore[attr-defined]
    setattr(cls, "send_video", wrapped)


def install_livedub_audio_companion() -> None:
    """Install after cloud-media fallback so send_audio inherits its safety net."""
    if not _enabled():
        return
    with _INSTALL_LOCK:
        from telegram import Bot

        _wrap_send_video(Bot)
        try:
            from telegram.ext import ExtBot

            if ExtBot.send_video is not Bot.send_video:
                _wrap_send_video(ExtBot)
        except Exception as exc:
            logger.debug("[LiveDubAudio] ExtBot patch skipped: %s", exc)
        logger.info("🎧 LiveDub audio companion: ✅ чистый RU MP3 + cached file_id pairing")
