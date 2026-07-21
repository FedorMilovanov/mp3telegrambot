"""Transparent media fallback for the cloud Telegram Bot API.

The cloud Bot API rejects uploads above roughly 50 MiB, while the local Bot API
accepts much larger files.  A failed local server must not make an already-built
LiveDub video disappear.  This module installs a narrow runtime adapter:

* the business pipeline may continue past its pre-send size guard;
* when the effective Bot instance uses api.telegram.org, oversized local video
  or audio paths are transcoded once to a safe target size;
* local Bot API sends remain byte-for-byte untouched;
* successful compressed files are reused on repeated sends.

The adapter is installed after importing :mod:`main`, so all pipeline modules
already exist and can be patched without changing their large source files.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_INSTALL_LOCK = threading.Lock()
_TRANSCODE_LOCKS: dict[str, threading.Lock] = {}
_ORIGINAL_PIPELINE_LIMIT: Callable[[], int] | None = None


def _enabled() -> bool:
    return os.getenv("CLOUD_MEDIA_AUTO_COMPRESS", "1").strip().lower() in _TRUE


def _target_mb() -> float:
    try:
        value = float(os.getenv("CLOUD_MEDIA_TARGET_MB", "48.0").strip() or "48.0")
    except ValueError:
        value = 48.0
    return max(20.0, min(value, 49.0))


def _is_cloud_bot(bot: Any) -> bool:
    """Determine the actual request destination at send time.

    Reading LOCAL_BOT_API_URL is insufficient because main.py can fall back to
    the cloud after startup.  PTB's built Bot instance is the source of truth.
    """
    candidates = [
        getattr(bot, "base_url", ""),
        getattr(bot, "_base_url", ""),
    ]
    text = " ".join(str(x or "") for x in candidates).lower()
    if "api.telegram.org" in text:
        return True
    if any(host in text for host in ("127.0.0.1", "localhost", "::1")):
        return False
    # PTB's default is the cloud.  Unknown/custom URLs are left untouched unless
    # the bootstrap explicitly marked this run as cloud.
    return os.getenv("MP3BOT_EFFECTIVE_BOT_API", "").strip().lower() == "cloud"


def _path_from_media(value: Any) -> Path | None:
    if isinstance(value, Path):
        return value if value.exists() else None
    if isinstance(value, str):
        path = Path(value)
        return path if path.exists() else None
    return None


def _probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe не найден — невозможно рассчитать безопасный битрейт")
    proc = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe duration failed: {(proc.stderr or proc.stdout)[-400:]}")
    try:
        duration = float((proc.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe вернул неверную длительность: {proc.stdout!r}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"некорректная длительность медиа: {duration}")
    return duration


def _run(cmd: list[str], *, timeout: int) -> None:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1600:]
        raise RuntimeError(f"ffmpeg rc={proc.returncode}: {tail}")


def _video_height_for_bitrate(video_kbps: int) -> int:
    # Talking-head/theology videos remain readable at these conservative pairs.
    if video_kbps < 190:
        return 360
    if video_kbps < 360:
        return 480
    if video_kbps < 850:
        return 720
    return 1080


def _safe_output_path(source: Path, suffix: str) -> Path:
    return source.with_name(f"{source.stem}.{suffix}{source.suffix.lower()}")


def _under_limit(path: Path, max_mb: float) -> bool:
    try:
        return path.exists() and path.stat().st_size <= int(max_mb * 1024 * 1024)
    except OSError:
        return False


def _transcode_video(source: Path, max_mb: float) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден — резервное сжатие невозможно")
    output = _safe_output_path(source, f"cloud{max_mb:g}")
    if _under_limit(output, max_mb) and output.stat().st_mtime >= source.stat().st_mtime:
        return output

    lock = _TRANSCODE_LOCKS.setdefault(str(source.resolve()), threading.Lock())
    with lock:
        if _under_limit(output, max_mb) and output.stat().st_mtime >= source.stat().st_mtime:
            return output

        duration = _probe_duration(source)
        # Reserve ten percent for the MP4 container, timestamps and bitrate drift.
        target_bytes = max_mb * 1024 * 1024 * 0.90
        total_kbps = max(120, int(target_bytes * 8 / duration / 1000))
        audio_kbps = 48 if duration >= 20 * 60 else 64
        video_kbps = max(80, total_kbps - audio_kbps - 10)
        height = _video_height_for_bitrate(video_kbps)
        scale = f"scale=-2:{height}:force_original_aspect_ratio=decrease"
        passlog = str(Path(tempfile.gettempdir()) / f"mp3bot-cloud-{os.getpid()}-{abs(hash(str(source))) & 0xFFFFFF:x}")
        null_sink = "NUL" if os.name == "nt" else "/dev/null"
        timeout = max(900, int(duration * 4))

        logger.warning(
            "[CloudMediaFallback] Local Bot API недоступен: сжимаю %s %.1fMB → <=%.1fMB "
            "(%dk video + %dk audio, %dp)",
            source.name, source.stat().st_size / (1024 * 1024), max_mb,
            video_kbps, audio_kbps, height,
        )
        try:
            _run([
                ffmpeg, "-y", "-i", str(source), "-map", "0:v:0",
                "-vf", scale, "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", f"{video_kbps}k", "-pass", "1", "-passlogfile", passlog,
                "-an", "-f", "mp4", null_sink,
            ], timeout=timeout)
            _run([
                ffmpeg, "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", scale, "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", f"{video_kbps}k", "-pass", "2", "-passlogfile", passlog,
                "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "2",
                "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(output),
            ], timeout=timeout)
        finally:
            for candidate in Path(tempfile.gettempdir()).glob(Path(passlog).name + "*"):
                try:
                    candidate.unlink()
                except OSError:
                    pass

        if not _under_limit(output, max_mb):
            size_mb = output.stat().st_size / (1024 * 1024) if output.exists() else 0
            raise RuntimeError(
                f"резервное видео всё ещё превышает лимит: {size_mb:.1f} МБ > {max_mb:.1f} МБ"
            )
        logger.info(
            "[CloudMediaFallback] Готово: %s (%.1fMB)",
            output.name, output.stat().st_size / (1024 * 1024),
        )
        return output


def _transcode_audio(source: Path, max_mb: float) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден — резервное сжатие аудио невозможно")
    output = _safe_output_path(source, f"cloud{max_mb:g}")
    if _under_limit(output, max_mb) and output.stat().st_mtime >= source.stat().st_mtime:
        return output

    lock = _TRANSCODE_LOCKS.setdefault(str(source.resolve()), threading.Lock())
    with lock:
        if _under_limit(output, max_mb) and output.stat().st_mtime >= source.stat().st_mtime:
            return output
        duration = _probe_duration(source)
        target_bytes = max_mb * 1024 * 1024 * 0.90
        bitrate = max(32, min(128, int(target_bytes * 8 / duration / 1000)))
        timeout = max(600, int(duration * 2))
        logger.warning(
            "[CloudMediaFallback] Сжимаю аудио %s %.1fMB → <=%.1fMB (%dk)",
            source.name, source.stat().st_size / (1024 * 1024), max_mb, bitrate,
        )
        _run([
            ffmpeg, "-y", "-i", str(source), "-vn", "-map_metadata", "0",
            "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", str(output),
        ], timeout=timeout)
        if not _under_limit(output, max_mb):
            size_mb = output.stat().st_size / (1024 * 1024) if output.exists() else 0
            raise RuntimeError(
                f"резервное аудио всё ещё превышает лимит: {size_mb:.1f} МБ > {max_mb:.1f} МБ"
            )
        return output


def _append_fallback_note(kwargs: dict[str, Any]) -> None:
    note = "\n\n⚠️ Локальный Bot API недоступен: видео автоматически сжато под облачный лимит 50 МБ."
    caption = kwargs.get("caption")
    if isinstance(caption, str) and len(caption) + len(note) <= 1024:
        kwargs["caption"] = caption + note


def _wrap_send_method(cls: type, method_name: str, media_pos: int, kind: str) -> None:
    original = getattr(cls, method_name, None)
    if original is None or getattr(original, "_mp3bot_cloud_fallback", False):
        return

    async def wrapped(self, *args, **kwargs):
        if not _enabled() or not _is_cloud_bot(self):
            return await original(self, *args, **kwargs)

        mutable_args = list(args)
        media = kwargs.get(kind)
        from_kwargs = media is not None
        if media is None and len(mutable_args) > media_pos:
            media = mutable_args[media_pos]
        path = _path_from_media(media)
        max_mb = _target_mb()
        if path is None or _under_limit(path, max_mb):
            return await original(self, *args, **kwargs)

        if kind == "video":
            replacement = await asyncio.to_thread(_transcode_video, path, max_mb)
            _append_fallback_note(kwargs)
        else:
            replacement = await asyncio.to_thread(_transcode_audio, path, max_mb)

        if from_kwargs:
            kwargs[kind] = replacement
        else:
            mutable_args[media_pos] = replacement
        return await original(self, *mutable_args, **kwargs)

    wrapped._mp3bot_cloud_fallback = True  # type: ignore[attr-defined]
    setattr(cls, method_name, wrapped)


def _install_pipeline_limit_adapter() -> None:
    """Let the LiveDub pipeline reach Bot.send_video where compression occurs."""
    global _ORIGINAL_PIPELINE_LIMIT
    try:
        import pipelines.main_pipeline as pipeline
    except Exception as exc:
        logger.warning("[CloudMediaFallback] main_pipeline adapter skipped: %s", exc)
        return
    current = getattr(pipeline, "get_max_file_size_mb", None)
    if not callable(current) or getattr(current, "_mp3bot_cloud_fallback", False):
        return
    _ORIGINAL_PIPELINE_LIMIT = current

    def processing_limit() -> int:
        real = int(current())
        if not _enabled() or not shutil.which("ffmpeg"):
            return real
        return max(real, 2000)

    processing_limit._mp3bot_cloud_fallback = True  # type: ignore[attr-defined]
    pipeline.get_max_file_size_mb = processing_limit


def install_cloud_media_fallback() -> None:
    """Install the adapter once. Safe to call repeatedly."""
    if not _enabled():
        return
    with _INSTALL_LOCK:
        from telegram import Bot
        _wrap_send_method(Bot, "send_video", media_pos=1, kind="video")
        _wrap_send_method(Bot, "send_audio", media_pos=1, kind="audio")
        try:
            from telegram.ext import ExtBot
            if ExtBot.send_video is not Bot.send_video:
                _wrap_send_method(ExtBot, "send_video", media_pos=1, kind="video")
            if ExtBot.send_audio is not Bot.send_audio:
                _wrap_send_method(ExtBot, "send_audio", media_pos=1, kind="audio")
        except Exception:
            pass
        _install_pipeline_limit_adapter()
        logger.info(
            "☁️ Cloud media fallback: ✅ автосжатие видео/аудио до %.1f МБ при недоступном Local Bot API",
            _target_mb(),
        )
