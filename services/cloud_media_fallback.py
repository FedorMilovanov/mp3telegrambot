"""Transparent, verified media fallback for the cloud Telegram Bot API.

The cloud API rejects large uploads while the local Bot API accepts much larger
files.  This module keeps delivery reliable when local mode genuinely fails:

* only the actual Bot endpoint decides whether compression is needed;
* local sends remain byte-for-byte untouched;
* cloud video/audio is written atomically, validated with ffprobe and cached;
* the original public filename and accurate media metadata are preserved;
* per-source locks are reference-counted and do not grow forever.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_INSTALL_LOCK = threading.Lock()
_TRANSCODE_LOCKS_GUARD = threading.Lock()
_TRANSCODE_LOCKS: dict[str, tuple[threading.Lock, int]] = {}
_ORIGINAL_PIPELINE_LIMIT: Callable[[], int] | None = None


class CloudMediaFallbackError(RuntimeError):
    """Raised when an oversized cloud media file cannot be made sendable."""


def _enabled() -> bool:
    return os.getenv("CLOUD_MEDIA_AUTO_COMPRESS", "1").strip().lower() in _TRUE


def _target_mb() -> float:
    """Return a byte-safe target and honour an explicit smaller project limit."""
    try:
        value = float(os.getenv("CLOUD_MEDIA_TARGET_MB", "47.0").strip() or "47.0")
    except ValueError:
        value = 47.0
    value = max(5.0, min(value, 47.5))

    try:
        from core.database import get_max_file_size_mb

        effective = float(get_max_file_size_mb())
        if effective < 50:
            value = min(value, max(5.0, effective - 1.0))
    except Exception:
        pass
    return value


def _is_cloud_bot(bot: Any) -> bool:
    """Determine the actual request destination at send time."""
    candidates = [
        getattr(bot, "base_url", ""),
        getattr(bot, "_base_url", ""),
    ]
    text = " ".join(str(x or "") for x in candidates).lower()
    if "api.telegram.org" in text:
        return True
    if any(host in text for host in ("127.0.0.1", "localhost", "::1")):
        return False
    return os.getenv("MP3BOT_EFFECTIVE_BOT_API", "").strip().lower() == "cloud"


def _path_from_media(value: Any) -> Path | None:
    if isinstance(value, Path):
        return value if value.exists() else None
    if isinstance(value, str):
        path = Path(value)
        return path if path.exists() else None
    return None


def _run(cmd: list[str], *, timeout: int) -> None:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloudMediaFallbackError(
            f"ffmpeg/ffprobe превысил таймаут {timeout}с"
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1800:]
        raise CloudMediaFallbackError(f"ffmpeg/ffprobe rc={proc.returncode}: {tail}")


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise CloudMediaFallbackError(
            "ffprobe не найден — невозможно проверить длительность и целостность"
        )
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloudMediaFallbackError("ffprobe завис более чем на 60с") from exc
    if proc.returncode != 0:
        raise CloudMediaFallbackError(
            f"ffprobe media failed: {(proc.stderr or proc.stdout)[-500:]}"
        )
    try:
        payload = json.loads(proc.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CloudMediaFallbackError("ffprobe вернул повреждённые метаданные") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise CloudMediaFallbackError(f"некорректная длительность медиа: {duration}")

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    return {
        "duration": duration,
        "has_video": video_stream is not None,
        "has_audio": audio_stream is not None,
        "width": int((video_stream or {}).get("width") or 0),
        "height": int((video_stream or {}).get("height") or 0),
    }


def _probe_duration(path: Path) -> float:
    return float(_probe_media(path)["duration"])


def _video_height_for_bitrate(video_kbps: int) -> int:
    if video_kbps < 70:
        return 240
    if video_kbps < 190:
        return 360
    if video_kbps < 360:
        return 480
    if video_kbps < 850:
        return 720
    return 1080


def _safe_output_path(
    source: Path,
    suffix: str,
    *,
    extension: str | None = None,
) -> Path:
    ext = extension or source.suffix.lower()
    if not ext.startswith("."):
        ext = "." + ext
    return source.with_name(f"{source.stem}.{suffix}{ext}")


def _under_limit(path: Path, max_mb: float) -> bool:
    try:
        return (
            path.is_file()
            and path.stat().st_size > 1024
            and path.stat().st_size <= int(max_mb * 1024 * 1024)
        )
    except OSError:
        return False


def _same_duration(source_duration: float, output_duration: float) -> bool:
    tolerance = max(3.0, source_duration * 0.02)
    return abs(source_duration - output_duration) <= tolerance


def _valid_cached_output(
    source: Path,
    output: Path,
    max_mb: float,
    *,
    kind: str,
    source_info: dict[str, Any] | None = None,
) -> bool:
    try:
        if not _under_limit(output, max_mb):
            return False
        if output.stat().st_mtime_ns < source.stat().st_mtime_ns:
            return False
        source_info = source_info or _probe_media(source)
        output_info = _probe_media(output)
        if not _same_duration(
            float(source_info["duration"]),
            float(output_info["duration"]),
        ):
            return False
        if kind == "video":
            return bool(
                output_info["has_video"]
                and output_info["width"] > 0
                and output_info["height"] > 0
            )
        return bool(output_info["has_audio"])
    except (OSError, CloudMediaFallbackError):
        return False


@contextmanager
def _source_lock(source: Path) -> Iterator[None]:
    """Serialize one source without retaining a lock forever."""
    key = str(source.resolve())
    with _TRANSCODE_LOCKS_GUARD:
        entry = _TRANSCODE_LOCKS.get(key)
        if entry is None:
            lock = threading.Lock()
            refs = 1
        else:
            lock, refs = entry
            refs += 1
        _TRANSCODE_LOCKS[key] = (lock, refs)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _TRANSCODE_LOCKS_GUARD:
            current = _TRANSCODE_LOCKS.get(key)
            if current is not None and current[0] is lock:
                remaining = current[1] - 1
                if remaining <= 0:
                    _TRANSCODE_LOCKS.pop(key, None)
                else:
                    _TRANSCODE_LOCKS[key] = (lock, remaining)


def _temp_output(output: Path) -> Path:
    token = hashlib.sha1(
        f"{output}:{os.getpid()}:{threading.get_ident()}".encode("utf-8")
    ).hexdigest()[:10]
    return output.with_name(f".{output.stem}.{token}.part{output.suffix}")


def _cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _video_bitrate_plan(
    duration: float,
    max_mb: float,
    *,
    budget_fraction: float = 0.84,
) -> tuple[int, int, int]:
    """Return strict (video kbps, audio kbps, height) for one-file fallback."""
    target_bytes = max_mb * 1024 * 1024 * budget_fraction
    total_kbps = max(24, int(target_bytes * 8 / duration / 1000))
    overhead_kbps = max(4, int(total_kbps * 0.04))

    if total_kbps >= 220:
        audio_kbps = 64
    elif total_kbps >= 140:
        audio_kbps = 48
    elif total_kbps >= 90:
        audio_kbps = 32
    elif total_kbps >= 55:
        audio_kbps = 24
    else:
        audio_kbps = 16

    video_kbps = total_kbps - audio_kbps - overhead_kbps
    if video_kbps < 8:
        audio_kbps = max(8, total_kbps - overhead_kbps - 8)
        video_kbps = max(8, total_kbps - audio_kbps - overhead_kbps)
    return video_kbps, audio_kbps, _video_height_for_bitrate(video_kbps)


def _encode_video_attempt(
    source: Path,
    output: Path,
    source_info: dict[str, Any],
    max_mb: float,
    *,
    budget_fraction: float,
) -> tuple[int, int, int]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CloudMediaFallbackError("ffmpeg не найден — резервное сжатие невозможно")

    duration = float(source_info["duration"])
    video_kbps, audio_kbps, height = _video_bitrate_plan(
        duration,
        max_mb,
        budget_fraction=budget_fraction,
    )
    scale = f"scale=-2:{height}:force_original_aspect_ratio=decrease"
    pass_id = hashlib.sha1(
        f"{source.resolve()}:{output}:{budget_fraction}".encode("utf-8")
    ).hexdigest()[:16]
    passlog = str(
        Path(tempfile.gettempdir()) / f"mp3bot-cloud-{os.getpid()}-{pass_id}"
    )
    null_sink = "NUL" if os.name == "nt" else "/dev/null"
    timeout = max(900, int(duration * 4))
    channels = "1" if audio_kbps < 32 else "2"
    sample_rate = "24000" if audio_kbps < 32 else "44100"

    logger.warning(
        "[CloudMediaFallback] Сжимаю %s %.1fMB → <=%.1fMB "
        "(%dk video + %dk audio, %dp, budget=%.0f%%)",
        source.name,
        source.stat().st_size / (1024 * 1024),
        max_mb,
        video_kbps,
        audio_kbps,
        height,
        budget_fraction * 100,
    )
    try:
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-vf",
                scale,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                f"{video_kbps}k",
                "-maxrate",
                f"{video_kbps}k",
                "-bufsize",
                f"{max(64, video_kbps * 2)}k",
                "-pass",
                "1",
                "-passlogfile",
                passlog,
                "-an",
                "-f",
                "null",
                null_sink,
            ],
            timeout=timeout,
        )
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                scale,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                f"{video_kbps}k",
                "-maxrate",
                f"{video_kbps}k",
                "-bufsize",
                f"{max(64, video_kbps * 2)}k",
                "-pass",
                "2",
                "-passlogfile",
                passlog,
                "-c:a",
                "aac",
                "-b:a",
                f"{audio_kbps}k",
                "-ac",
                channels,
                "-ar",
                sample_rate,
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                "-f",
                "mp4",
                str(output),
            ],
            timeout=timeout,
        )
    finally:
        for candidate in Path(tempfile.gettempdir()).glob(Path(passlog).name + "*"):
            _cleanup_file(candidate)
    return video_kbps, audio_kbps, height


def _transcode_video(source: Path, max_mb: float) -> Path:
    output = _safe_output_path(source, f"cloud{max_mb:g}", extension=".mp4")
    source_info = _probe_media(source)
    if not source_info["has_video"]:
        raise CloudMediaFallbackError("исходный файл не содержит видеопотока")
    if _valid_cached_output(
        source,
        output,
        max_mb,
        kind="video",
        source_info=source_info,
    ):
        return output

    with _source_lock(source):
        if _valid_cached_output(
            source,
            output,
            max_mb,
            kind="video",
            source_info=source_info,
        ):
            return output

        _cleanup_file(output)
        temp_output = _temp_output(output)
        _cleanup_file(temp_output)
        last_error: Exception | None = None
        try:
            for budget_fraction in (0.84, 0.70):
                _cleanup_file(temp_output)
                try:
                    _encode_video_attempt(
                        source,
                        temp_output,
                        source_info,
                        max_mb,
                        budget_fraction=budget_fraction,
                    )
                    if not _under_limit(temp_output, max_mb):
                        size_mb = temp_output.stat().st_size / (1024 * 1024)
                        raise CloudMediaFallbackError(
                            f"резервное видео весит {size_mb:.1f} МБ"
                        )
                    temp_info = _probe_media(temp_output)
                    if not temp_info["has_video"] or not _same_duration(
                        float(source_info["duration"]),
                        float(temp_info["duration"]),
                    ):
                        raise CloudMediaFallbackError(
                            "резервное видео не прошло проверку целостности"
                        )
                    os.replace(temp_output, output)
                    logger.info(
                        "[CloudMediaFallback] Готово: %s (%.1fMB, %dx%d)",
                        output.name,
                        output.stat().st_size / (1024 * 1024),
                        temp_info["width"],
                        temp_info["height"],
                    )
                    return output
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "[CloudMediaFallback] попытка с budget %.0f%% не удалась: %s",
                        budget_fraction * 100,
                        str(exc)[:500],
                    )
            raise CloudMediaFallbackError(
                f"не удалось создать исправное видео <= {max_mb:.1f} МБ: {last_error}"
            )
        finally:
            _cleanup_file(temp_output)


def _audio_bitrate_plan(duration: float, max_mb: float) -> tuple[int, int, int]:
    target_bytes = max_mb * 1024 * 1024 * 0.84
    bitrate = max(8, min(128, int(target_bytes * 8 / duration / 1000)))
    channels = 1 if bitrate < 48 else 2
    sample_rate = 22050 if bitrate < 24 else (32000 if bitrate < 48 else 44100)
    return bitrate, channels, sample_rate


def _transcode_audio(source: Path, max_mb: float) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CloudMediaFallbackError("ffmpeg не найден — резервное сжатие аудио невозможно")
    output = _safe_output_path(source, f"cloud{max_mb:g}", extension=".mp3")
    source_info = _probe_media(source)
    if not source_info["has_audio"]:
        raise CloudMediaFallbackError("исходный файл не содержит аудиопотока")
    if _valid_cached_output(
        source,
        output,
        max_mb,
        kind="audio",
        source_info=source_info,
    ):
        return output

    with _source_lock(source):
        if _valid_cached_output(
            source,
            output,
            max_mb,
            kind="audio",
            source_info=source_info,
        ):
            return output
        _cleanup_file(output)
        temp_output = _temp_output(output)
        _cleanup_file(temp_output)
        duration = float(source_info["duration"])
        bitrate, channels, sample_rate = _audio_bitrate_plan(duration, max_mb)
        timeout = max(600, int(duration * 2))
        try:
            logger.warning(
                "[CloudMediaFallback] Сжимаю аудио %s %.1fMB → <=%.1fMB "
                "(%dk, %dch, %dHz)",
                source.name,
                source.stat().st_size / (1024 * 1024),
                max_mb,
                bitrate,
                channels,
                sample_rate,
            )
            _run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-map_metadata",
                    "0",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    f"{bitrate}k",
                    "-ac",
                    str(channels),
                    "-ar",
                    str(sample_rate),
                    "-f",
                    "mp3",
                    str(temp_output),
                ],
                timeout=timeout,
            )
            if not _under_limit(temp_output, max_mb):
                size_mb = temp_output.stat().st_size / (1024 * 1024)
                raise CloudMediaFallbackError(
                    f"резервное аудио весит {size_mb:.1f} МБ > {max_mb:.1f} МБ"
                )
            temp_info = _probe_media(temp_output)
            if not temp_info["has_audio"] or not _same_duration(
                duration,
                float(temp_info["duration"]),
            ):
                raise CloudMediaFallbackError(
                    "резервное аудио не прошло проверку целостности"
                )
            os.replace(temp_output, output)
            return output
        finally:
            _cleanup_file(temp_output)


def _append_fallback_note(kwargs: dict[str, Any], *, kind: str) -> None:
    media_word = "видео" if kind == "video" else "аудио"
    note = (
        f"\n\n⚠️ Локальный Bot API недоступен: {media_word} автоматически "
        "сжато под облачный лимит Telegram."
    )
    caption = kwargs.get("caption")
    if isinstance(caption, str) and len(caption) + len(note) <= 1024:
        kwargs["caption"] = caption + note


def _apply_replacement_metadata(
    kwargs: dict[str, Any],
    replacement: Path,
    *,
    kind: str,
    original_name: str,
) -> None:
    kwargs.setdefault("filename", original_name)
    info = _probe_media(replacement)
    kwargs["duration"] = max(1, int(round(float(info["duration"]))))
    if kind == "video":
        kwargs["width"] = int(info["width"])
        kwargs["height"] = int(info["height"])


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

        try:
            if kind == "video":
                replacement = await asyncio.to_thread(_transcode_video, path, max_mb)
            else:
                replacement = await asyncio.to_thread(_transcode_audio, path, max_mb)
            _append_fallback_note(kwargs, kind=kind)
            await asyncio.to_thread(
                _apply_replacement_metadata,
                kwargs,
                replacement,
                kind=kind,
                original_name=path.name,
            )
        except Exception as exc:
            logger.exception(
                "[CloudMediaFallback] Не удалось подготовить %s для облачной отправки: %s",
                path.name,
                exc,
            )
            raise CloudMediaFallbackError(
                f"готовый файл {path.name} превышает облачный лимит, "
                f"а безопасное автосжатие не удалось: {exc}"
            ) from exc

        if from_kwargs:
            kwargs[kind] = replacement
        else:
            mutable_args[media_pos] = replacement
        return await original(self, *mutable_args, **kwargs)

    wrapped._mp3bot_cloud_fallback = True  # type: ignore[attr-defined]
    setattr(cls, method_name, wrapped)


def _install_pipeline_limit_adapter() -> None:
    """Let LiveDub reach Bot.send_video where verified compression occurs."""
    global _ORIGINAL_PIPELINE_LIMIT
    try:
        import importlib

        pipeline = importlib.import_module("pipelines.main_pipeline")
    except Exception as exc:
        logger.warning("[CloudMediaFallback] main_pipeline adapter skipped: %s", exc)
        return
    current = getattr(pipeline, "get_max_file_size_mb", None)
    if not callable(current) or getattr(current, "_mp3bot_cloud_fallback", False):
        return
    _ORIGINAL_PIPELINE_LIMIT = current

    def processing_limit() -> int:
        real = int(current())
        mode = os.getenv("MP3BOT_EFFECTIVE_BOT_API", "").strip().lower()
        if mode == "local":
            return real
        if not _enabled() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
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
        except Exception as exc:
            logger.debug("[CloudMediaFallback] ExtBot patch skipped: %s", exc)
        _install_pipeline_limit_adapter()
        logger.info(
            "☁️ Cloud media fallback: ✅ проверяемое автосжатие видео/аудио "
            "до %.1f МБ при недоступном Local Bot API",
            _target_mb(),
        )
