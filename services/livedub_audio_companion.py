#!/usr/bin/env python3
"""Deliver two validated MP3 companions for every successful LiveDub video.

The delivery contract is explicit:

* ``clean`` — the isolated Russian Yandex translation retained by the pro-mix;
* ``mixed`` — audio extracted from the exact final video that was sent to Telegram
  (Russian translation plus the controlled original-language bed, including QA
  auto-fixes when the video was rebuilt).

Both variants are validated independently with ffprobe, sent independently, and
cached independently.  Legacy one-file cache entries remain readable and are
migrated on the next successful delivery.
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
_VARIANTS = ("clean", "mixed")
_VARIANT_LABELS = {
    "clean": "Чистый русский перевод",
    "mixed": "Финальный объединённый микс",
}
_VARIANT_CAPTIONS = {
    "clean": "🎧 Чистая аудиодорожка русского перевода Яндекса",
    "mixed": "🎧 Аудиоверсия финального дубляжа (русский перевод + тихий оригинал)",
}


def _enabled() -> bool:
    return os.getenv("LIVEDUB_SEND_AUDIO", "1").strip().lower() in _TRUE


def _dual_enabled() -> bool:
    return os.getenv("LIVEDUB_SEND_DUAL_AUDIO", "1").strip().lower() in _TRUE


def _public_error_text(exc: BaseException, limit: int = 260) -> str:
    """Return one bounded user-safe error without credentials or bot tokens."""
    text = str(exc or "").strip() or type(exc).__name__
    try:
        from core.utils import mask_api_key

        text = mask_api_key(text)
    except Exception:
        token = os.getenv("BOT_TOKEN", "").strip()
        if token:
            text = text.replace(token, "***BOT_TOKEN***")
    text = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+):[^\s/@]+@", r"\1:***@", text)
    return text[: max(32, int(limit))]


def _media_path(value: Any) -> Path | None:
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


def _safe_stem(value: str, fallback: str = "Переведённое аудио") -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(value or ""))
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")[:120]
    return stem or fallback


def _safe_filename(video_path: Path, title: str, variant: str = "mixed") -> str:
    stem = _safe_stem(title or video_path.stem)
    suffix = "чистый RU" if variant == "clean" else "финальный микс"
    return f"{stem} — {suffix}.mp3"


def _run_text(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


def _probe_audio(path: Path) -> tuple[bool, int]:
    """Return ``(valid_audio, rounded_duration_seconds)`` for one local file."""
    ffprobe = shutil.which("ffprobe")
    try:
        if not ffprobe or not path.is_file() or path.stat().st_size <= 1024:
            return False, 0
        proc = _run_text(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type",
                "-of", "json",
                str(path),
            ],
            timeout=60,
        )
        if proc.returncode != 0:
            logger.warning(
                "[LiveDubAudio] ffprobe rejected %s (rc=%s): %s",
                path.name,
                proc.returncode,
                (proc.stderr or "")[-300:],
            )
            return False, 0
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
        duration = int(round(float((data.get("format") or {}).get("duration") or 0)))
        return bool(has_audio and duration > 0), max(0, duration)
    except Exception as exc:
        logger.warning("[LiveDubAudio] probe failed for %s: %s", path.name, str(exc)[:180])
        return False, 0


def _duration_compatible(reference: int, candidate: int) -> bool:
    if reference <= 0 or candidate <= 0:
        return True
    tolerance = max(3, int(round(reference * 0.015)))
    return abs(reference - candidate) <= tolerance


def _find_clean_ru_track(video_path: Path) -> Path | None:
    from services.livedub_audio_quality_guard import select_clean_translation_mp3

    candidate = select_clean_translation_mp3(Path(video_path).parent)
    if candidate is None:
        return None
    ok, _duration = _probe_audio(candidate)
    return candidate if ok else None


def _extract_mix_mp3(video_path: Path) -> Path:
    """Atomically extract the audio from the exact final LiveDub video."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден")
    output = video_path.with_name(f"{video_path.stem}.final-mix.mp3")
    temp = output.with_name(f".{output.stem}.{os.getpid()}.{threading.get_ident()}.part.mp3")
    temp.unlink(missing_ok=True)
    try:
        _video_ok, video_duration = _probe_audio(video_path)
        timeout = max(900, int((video_duration or 0) * 2))
        proc = _run_text(
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
                "-ac", "2",
                str(temp),
            ],
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg error")[-900:])
        ok, mp3_duration = _probe_audio(temp)
        if not ok:
            raise RuntimeError("созданный объединённый MP3 не прошёл ffprobe")
        if not _duration_compatible(video_duration, mp3_duration):
            raise RuntimeError(
                f"длительность объединённого MP3 {mp3_duration}с не совпадает с видео {video_duration}с"
            )
        os.replace(temp, output)
        return output
    finally:
        temp.unlink(missing_ok=True)


def _cache_path() -> Path:
    root = os.getenv("LOCALAPPDATA", "").strip()
    base = Path(root) / "MP3Bot" if root else Path.home() / ".mp3bot"
    return base / "livedub-audio-file-ids.json"


def _load_cache() -> dict[str, dict[str, Any]]:
    from services.livedub_audio_cache_recovery import load_recoverable_cache

    data = load_recoverable_cache(_cache_path())
    return data if isinstance(data, dict) else {}


def _save_cache(data: dict[str, dict[str, Any]]) -> None:
    from services.livedub_audio_cache_recovery import save_recoverable_cache

    try:
        save_recoverable_cache(_cache_path(), data)
    except Exception as exc:
        logger.warning("[LiveDubAudio] recoverable cache save failed: %s", str(exc)[:180])


def _normalise_cache_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    entry = dict(value)
    variants = entry.get("variants")
    if not isinstance(variants, dict):
        variants = {}
    variants = {
        str(name): dict(meta)
        for name, meta in variants.items()
        if name in _VARIANTS and isinstance(meta, dict) and meta.get("audio_file_id")
    }
    legacy_id = str(entry.get("audio_file_id") or "")
    if legacy_id and "clean" not in variants:
        variants["clean"] = {
            "audio_file_id": legacy_id,
            "title": entry.get("title"),
            "performer": entry.get("performer"),
            "filename": entry.get("filename"),
        }
    entry["variants"] = variants
    entry["schema_version"] = 2
    return entry


def _cache_get(video_file_id: str) -> dict[str, Any] | None:
    if not video_file_id:
        return None
    with _CACHE_LOCK:
        return _normalise_cache_entry(_load_cache().get(video_file_id))


def _cache_put_variant(video_file_id: str, variant: str, audio_file_id: str, **meta: Any) -> None:
    if not video_file_id or not audio_file_id or variant not in _VARIANTS:
        return
    with _CACHE_LOCK:
        data = _load_cache()
        entry = _normalise_cache_entry(data.get(video_file_id)) or {
            "schema_version": 2,
            "variants": {},
        }
        entry["variants"][variant] = {
            "audio_file_id": audio_file_id,
            **meta,
        }
        entry["saved_at"] = time.time()
        data[video_file_id] = entry
        _save_cache(data)


def _cache_drop_variant(video_file_id: str, variant: str) -> None:
    if not video_file_id:
        return
    with _CACHE_LOCK:
        data = _load_cache()
        entry = _normalise_cache_entry(data.get(video_file_id))
        if not entry:
            return
        entry["variants"].pop(variant, None)
        if entry["variants"]:
            entry["saved_at"] = time.time()
            data[video_file_id] = entry
        else:
            data.pop(video_file_id, None)
        _save_cache(data)


def _cache_drop(video_file_id: str) -> None:
    if not video_file_id:
        return
    with _CACHE_LOCK:
        data = _load_cache()
        if data.pop(video_file_id, None) is not None:
            _save_cache(data)


async def _send_cached_audio(
    self,
    *,
    chat_id: Any,
    video_file_id: str,
    reply_to: Any,
) -> bool:
    cached = _cache_get(video_file_id)
    variants = (cached or {}).get("variants") or {}
    if not variants:
        return False
    if _dual_enabled() and any(name not in variants for name in _VARIANTS):
        logger.info("[LiveDubAudio] legacy/incomplete cache entry requires rebuild")
        return False

    sent = 0
    required = [name for name in _VARIANTS if name in variants]
    for variant in required:
        meta = variants[variant]
        try:
            await self.send_audio(
                chat_id=chat_id,
                audio=meta["audio_file_id"],
                title=meta.get("title") or None,
                performer=meta.get("performer") or None,
                caption=_VARIANT_CAPTIONS[variant],
                reply_to_message_id=reply_to,
                write_timeout=300,
                read_timeout=300,
                connect_timeout=60,
            )
            sent += 1
        except Exception as exc:
            logger.info(
                "[LiveDubAudio] cached %s file_id expired: %s",
                variant,
                str(exc)[:180],
            )
            _cache_drop_variant(video_file_id, variant)
    return sent == len(required)


async def _send_variant(
    self,
    *,
    variant: str,
    source: Path,
    video_path: Path,
    title: str,
    performer: str,
    chat_id: Any,
    reply_to: Any,
    thumbnail: Any,
    video_file_id: str,
    reference_duration: int,
) -> bool:
    ok, duration = await asyncio.to_thread(_probe_audio, source)
    if not ok:
        raise RuntimeError(f"{_VARIANT_LABELS[variant]}: MP3 не прошёл ffprobe")
    if not _duration_compatible(reference_duration, duration):
        raise RuntimeError(
            f"{_VARIANT_LABELS[variant]}: длительность {duration}с не совпадает с видео "
            f"{reference_duration}с"
        )

    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "audio": source,
        "filename": _safe_filename(video_path, title, variant),
        "title": title,
        "performer": performer or None,
        "duration": duration,
        "caption": _VARIANT_CAPTIONS[variant],
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
        _cache_put_variant(
            video_file_id,
            variant,
            audio_file_id,
            title=title,
            performer=performer,
            filename=kwargs["filename"],
            duration=duration,
        )
    logger.info(
        "[LiveDubAudio] %s MP3 sent: %s duration=%ss",
        variant,
        kwargs["filename"],
        duration,
    )
    return True


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
    video_ok, video_duration = await asyncio.to_thread(_probe_audio, video_path)
    if not video_ok:
        raise RuntimeError("финальное LiveDub-видео не содержит проверяемой аудиодорожки")

    sources: list[tuple[str, Path]] = []
    clean = await asyncio.to_thread(_find_clean_ru_track, video_path)
    if clean is not None:
        sources.append(("clean", clean))
    else:
        logger.warning("[LiveDubAudio] clean RU track unavailable; mixed MP3 will still be sent")

    mixed = await asyncio.to_thread(_extract_mix_mp3, video_path)
    sources.append(("mixed", mixed))
    if not _dual_enabled():
        sources = [("clean", clean)] if clean is not None else [("mixed", mixed)]

    sent = 0
    failures: list[str] = []
    for variant, source in sources:
        try:
            if await _send_variant(
                self,
                variant=variant,
                source=source,
                video_path=video_path,
                title=title,
                performer=performer,
                chat_id=chat_id,
                reply_to=reply_to,
                thumbnail=thumbnail,
                video_file_id=video_file_id,
                reference_duration=video_duration,
            ):
                sent += 1
        except Exception as exc:
            failures.append(f"{_VARIANT_LABELS[variant]}: {str(exc)[:180]}")
            logger.exception("[LiveDubAudio] %s variant failed: %s", variant, exc)

    expected = len(sources)
    if sent == expected:
        logger.info("[LiveDubAudio] complete companion set delivered: %d/%d", sent, expected)
        return True
    if sent:
        raise RuntimeError(
            f"отправлен неполный комплект MP3 ({sent}/{expected}); " + "; ".join(failures)
        )
    raise RuntimeError("оба MP3 не отправлены: " + "; ".join(failures))



def validate_livedub_audio_companion() -> str:
    """Compatibility/startup validator; performs no Telegram mutation."""
    if not callable(_cache_get) or not callable(_cache_put_variant):
        raise RuntimeError("LiveDub companion cache surface is incomplete")
    return "source-owned companion helpers; explicit coordinator delivery"


def install_livedub_audio_companion() -> str:
    """Deprecated compatibility name; no longer patches Bot/ExtBot methods."""
    return validate_livedub_audio_companion()
