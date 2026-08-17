#!/usr/bin/env python3
"""Factory-owned media acquisition, Gemini audio input and plan boundary."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.shorts_factory_disk_guard import (
    ensure_factory_audio_space,
    ensure_factory_video_space,
    factory_delivery_sort_args,
)

logger = logging.getLogger(__name__)

_FACTORY_MEDIA_TIMEOUT_SEC = 7200
_FACTORY_TAIL_TOLERANCE_SEC = 0.20
_GEMINI_ANALYSIS_BITRATE_KBPS = 128
_GEMINI_ANALYSIS_SAMPLE_RATE = 48000

_AUDIO_MIME_BY_SUFFIX = {
    ".aac": "audio/aac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".mp3": "audio/mp3",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".wav": "audio/wav",
}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def gemini_analysis_bitrate_kbps() -> int:
    """Return the Factory-owned compact Gemini AAC bitrate."""
    return _bounded_int(
        "SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS",
        _GEMINI_ANALYSIS_BITRATE_KBPS,
        96,
        192,
    )


def gemini_analysis_sample_rate() -> int:
    """Return the Factory-owned compact Gemini sample rate."""
    configured = _bounded_int(
        "SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE",
        _GEMINI_ANALYSIS_SAMPLE_RATE,
        24000,
        48000,
    )
    return 48000 if configured >= 36000 else 24000


def factory_audio_mime_type(path: Path) -> str:
    mime_type = _AUDIO_MIME_BY_SUFFIX.get(Path(path).suffix.casefold(), "")
    if not mime_type:
        raise RuntimeError(
            "SHORTS FACTORY audio format is not supported by Gemini: "
            f"{Path(path).suffix or '<no extension>'}"
        )
    return mime_type


def factory_audio_probe_is_usable(probe: Any) -> bool:
    return bool(
        probe is not None
        and float(getattr(probe, "duration", 0.0) or 0.0) > 0
        and getattr(probe, "has_audio", False)
        and int(getattr(probe, "audio_sample_rate", 0) or 0) > 0
        and str(getattr(probe, "audio_codec", "") or "").strip()
    )


def factory_duration_matches(actual: float, expected: float) -> bool:
    """Allow only small container/timeline drift for a supposedly complete source."""
    try:
        actual_value = float(actual)
        expected_value = float(expected)
    except (TypeError, ValueError, OverflowError):
        return False
    if actual_value <= 0 or expected_value <= 0:
        return False
    tolerance = max(2.0, min(15.0, expected_value * 0.002))
    return abs(actual_value - expected_value) <= tolerance


def _progress_duration_seconds(stdout: Any) -> float:
    """Read the actual processed media timeline from FFmpeg -progress output."""
    text = str(stdout or "")
    best = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("out_time_us="):
            try:
                best = max(best, int(line.split("=", 1)[1]) / 1_000_000.0)
            except (TypeError, ValueError, OverflowError):
                pass
            continue
        if not line.startswith("out_time="):
            continue
        value = line.split("=", 1)[1].strip()
        parts = value.split(":")
        if len(parts) != 3:
            continue
        try:
            seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (TypeError, ValueError, OverflowError):
            continue
        best = max(best, seconds)
    return best


async def measure_factory_audio_duration(path: Path) -> float:
    """Decode the audio timeline instead of trusting raw AAC/ADTS duration estimates."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to verify Factory analysis audio")
    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(Path(path)),
        "-map",
        "0:a:0",
        "-f",
        "null",
        os.devnull,
    ]
    process = await run_cancellable_process(
        command,
        timeout=_FACTORY_MEDIA_TIMEOUT_SEC,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "Factory analysis audio decode verification failed: " + _stderr_tail(process)
        )
    duration = _progress_duration_seconds(process.stdout)
    if duration <= 0:
        raise RuntimeError("Factory analysis audio decode verification returned no timeline")
    return duration


def _partial_media(path: Path) -> bool:
    return path.suffix.casefold() in {".part", ".ytdl", ".tmp"}


def _stderr_tail(process: Any, limit: int = 900) -> str:
    value = getattr(process, "stderr", "") or ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value)[-limit:]


def _remove_paths(paths) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def _factory_livedub_timeout_seconds() -> int:
    try:
        value = int(os.getenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "") or 1800)
    except (TypeError, ValueError):
        value = 1800
    return max(1800, min(value, 7200))


async def _select_audio_source(media_id: str) -> tuple[Path, Any]:
    candidates = sorted(
        (
            path
            for path in DOWNLOAD_DIR.glob(f"{media_id}_factory_audio_source.*")
            if path.is_file() and not _partial_media(path)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        probe = await probe_media_async(path)
        if factory_audio_probe_is_usable(probe):
            return path, probe
    raise RuntimeError("yt-dlp completed without a probed Factory audio stream")


async def _prepare_gemini_audio(
    source_path: Path,
    source_probe: Any,
    media_id: str,
    *,
    expected_duration: float = 0.0,
) -> Path:
    """Build a compact AAC surrogate and prove its real processed duration."""
    source_path = Path(source_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare Factory Gemini audio")

    source_duration = float(getattr(source_probe, "duration", 0.0) or 0.0)
    expected = float(expected_duration or 0.0)
    if expected > 0 and not factory_duration_matches(source_duration, expected):
        raise RuntimeError(
            "Downloaded Factory audio source duration does not match yt-dlp metadata: "
            f"metadata={expected:.3f}s source={source_duration:.3f}s"
        )

    bitrate = gemini_analysis_bitrate_kbps()
    sample_rate = gemini_analysis_sample_rate()
    output_path = DOWNLOAD_DIR / f"{media_id}_factory_audio_gemini.aac"
    output_path.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(source_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "aac",
        "-b:a",
        f"{bitrate}k",
        "-f",
        "adts",
        "-y",
        str(output_path),
    ]
    process = await run_cancellable_process(
        command,
        timeout=_FACTORY_MEDIA_TIMEOUT_SEC,
        text=True,
    )
    if process.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "ffmpeg could not prepare compact Gemini Factory audio: "
            + _stderr_tail(process)
        )

    verified_duration = _progress_duration_seconds(process.stdout)
    if verified_duration <= 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg returned no verified timeline for Factory Gemini audio")

    probe = await probe_media_async(output_path)
    if not factory_audio_probe_is_usable(probe):
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Compact Gemini Factory audio failed its audio-stream probe")

    reference_duration = expected if expected > 0 else source_duration
    if reference_duration > 0 and not factory_duration_matches(
        verified_duration,
        reference_duration,
    ):
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Compact Gemini Factory audio is incomplete by decoded timeline: "
            f"expected={reference_duration:.3f}s verified={verified_duration:.3f}s"
        )
    if output_path.stat().st_size < 1024:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Compact Gemini Factory audio is empty")

    factory_audio_mime_type(output_path)
    ffprobe_estimate = float(getattr(probe, "duration", 0.0) or 0.0)
    if output_path != source_path:
        try:
            source_path.unlink(missing_ok=True)
        except OSError:
            pass
    logger.info(
        "Factory Gemini analysis audio prepared: codec=AAC mono bitrate=%dk "
        "sample_rate=%d verified_duration=%.3fs ffprobe_estimate=%.3fs size=%.1fMB",
        bitrate,
        sample_rate,
        verified_duration,
        ffprobe_estimate,
        output_path.stat().st_size / (1024 * 1024),
    )
    return output_path


def _factory_quality_sort_reset() -> list[str]:
    return factory_delivery_sort_args(
        [
            "--format-sort-reset",
            "--no-format-sort-force",
            "--no-prefer-free-formats",
        ]
    )


async def _download_factory_audio_fresh(
    url: str,
    media_id: str,
    *,
    expected_duration: float = 0.0,
) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _remove_paths(DOWNLOAD_DIR.glob(f"{media_id}_factory_audio_*"))
    output_template = DOWNLOAD_DIR / f"{media_id}_factory_audio_source.%(ext)s"
    command = list(YTDLP_BASE_ARGS) + _factory_quality_sort_reset() + [
        "--abort-on-unavailable-fragments",
        "--format",
        "bestaudio/best",
        "--no-playlist",
        "--output",
        str(output_template),
        url,
    ]
    try:
        process = await run_cancellable_process(
            command,
            timeout=_FACTORY_MEDIA_TIMEOUT_SEC,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "yt-dlp maximum-quality Factory audio download failed: "
                + _stderr_tail(process)
            )
        source_path, source_probe = await _select_audio_source(media_id)
        return await _prepare_gemini_audio(
            source_path,
            source_probe,
            media_id,
            expected_duration=expected_duration,
        )
    except (asyncio.CancelledError, Exception):
        _remove_paths(DOWNLOAD_DIR.glob(f"{media_id}_factory_audio_*"))
        raise


async def download_factory_audio_source(
    url: str,
    media_id: str,
    *,
    status_msg: Any = None,
    expected_duration: float = 0.0,
) -> Path:
    """Return verified compact analysis audio through the source-owned retry cache."""
    if expected_duration > 0:
        ensure_factory_audio_space(
            [DOWNLOAD_DIR],
            duration_seconds=float(expected_duration),
        )

    from services.shorts_factory_retry_cache import (
        download_factory_audio_with_retry_cache,
    )

    async def _download_verified(download_url: str, download_media_id: str) -> Path:
        return await _download_factory_audio_fresh(
            download_url,
            download_media_id,
            expected_duration=expected_duration,
        )

    return await download_factory_audio_with_retry_cache(
        url,
        media_id,
        original_downloader=_download_verified,
        status_msg=status_msg,
        expected_duration=expected_duration,
    )


async def download_factory_video_source(
    url: str,
    media_id: str,
    workdir: Path | None = None,
    *,
    expected_duration: float = 0.0,
) -> Path:
    """Download the best available video+audio without a resolution ceiling."""
    target_dir = Path(workdir) if workdir is not None else DOWNLOAD_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    if expected_duration > 0:
        ensure_factory_video_space(
            [DOWNLOAD_DIR, target_dir],
            duration_seconds=float(expected_duration),
        )
    prefix = f"{media_id}_factory_max_source"
    _remove_paths(target_dir.glob(f"{prefix}.*"))
    output_template = target_dir / f"{prefix}.%(ext)s"
    command = list(YTDLP_BASE_ARGS) + _factory_quality_sort_reset() + [
        "--format",
        "bestvideo+bestaudio/best",
        "--merge-output-format",
        "mkv",
        "--no-playlist",
        "--output",
        str(output_template),
        url,
    ]
    try:
        process = await run_cancellable_process(
            command,
            timeout=_FACTORY_MEDIA_TIMEOUT_SEC,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "yt-dlp maximum-quality Factory video download failed: "
                + _stderr_tail(process)
            )
        candidates = sorted(
            (
                path
                for path in target_dir.glob(f"{prefix}.*")
                if path.is_file() and not _partial_media(path)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            probe = await probe_media_async(path)
            if media_probe_is_deliverable(probe):
                logger.info(
                    "Factory maximum-quality video source: %s %sx%s %.3fs %.1fMB",
                    path.name,
                    getattr(probe, "width", 0),
                    getattr(probe, "height", 0),
                    float(getattr(probe, "duration", 0.0) or 0.0),
                    path.stat().st_size / (1024 * 1024),
                )
                return path
        raise RuntimeError(
            "yt-dlp completed without a probed maximum-quality video+audio source"
        )
    except (asyncio.CancelledError, Exception):
        _remove_paths(target_dir.glob(f"{prefix}.*"))
        raise


async def prepare_factory_translation_video(
    url: str,
    workdir: Path,
    duration: int,
    source_language: str,
) -> Path:
    """Mix Yandex live audio over the unrestricted maximum-quality original."""
    import pipelines.shorts_factory as factory_pipeline
    from services.livedub_mix import get_mix_params, mix_tracks
    from services.yandex_live_dub import get_live_dub_audio

    backend = factory_pipeline._translation_backend()
    if backend != "yandex_live":
        raise RuntimeError(
            "SHORTS FACTORY сейчас поддерживает только Яндекс «Живые голоса». "
            f"Backend {backend!r} ещё не реализован."
        )
    if not factory_pipeline._env_bool("SHORTS_FACTORY_LIVEDUB", True):
        raise RuntimeError(
            "Для иностранного источника SHORTS_FACTORY_LIVEDUB должен быть "
            "включён: собственный нейроперевод запрещён."
        )

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ensure_factory_video_space(
        [DOWNLOAD_DIR, workdir],
        duration_seconds=float(duration),
    )
    ru_task = asyncio.create_task(
        get_live_dub_audio(
            url,
            workdir,
            timeout=_factory_livedub_timeout_seconds(),
            voice_style="live",
            duration=float(duration),
            lang=source_language,
        )
    )
    original_task = asyncio.create_task(
        download_factory_video_source(
            url,
            "translated",
            workdir=workdir,
            expected_duration=float(duration),
        )
    )
    tasks = (ru_task, original_task)
    try:
        ru_audio, original_video = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    original_probe = await probe_media_async(original_video)
    if not media_probe_is_deliverable(original_probe):
        raise RuntimeError("Maximum-quality original for Factory LiveDub failed media probe")

    output_path = workdir / "factory_max_livedub.mp4"
    output_path.unlink(missing_ok=True)
    mixed = await mix_tracks(original_video, ru_audio, output_path)
    if not mixed or not Path(mixed).is_file():
        raise RuntimeError(
            "Factory could not mix Yandex live audio over the maximum-quality original"
        )

    final_probe = await probe_media_async(Path(mixed))
    if not media_probe_is_deliverable(final_probe):
        raise RuntimeError("Maximum-quality Factory LiveDub result failed media probe")
    assert original_probe is not None and final_probe is not None
    mix_params = get_mix_params()
    required_tail = float(mix_params.get("tail_pad_ms", 0) or 0) / 1000.0
    minimum_duration = original_probe.duration + required_tail
    if final_probe.duration + _FACTORY_TAIL_TOLERANCE_SEC < minimum_duration:
        raise RuntimeError(
            "Maximum-quality Factory LiveDub lost the required Russian tail: "
            f"original={original_probe.duration:.3f}s "
            f"required={minimum_duration:.3f}s final={final_probe.duration:.3f}s"
        )
    logger.info(
        "Factory maximum-quality LiveDub: source=%sx%s %.3fs final=%.3fs required_tail=%.3fs",
        original_probe.width,
        original_probe.height,
        original_probe.duration,
        final_probe.duration,
        required_tail,
    )
    return Path(mixed)


def _strict_boundary_prompt(base_prompt: str) -> str:
    return base_prompt + (
        "\n\nОБЯЗАТЕЛЬНО: metadata.language должен содержать один "
        "доминирующий фактически услышанный язык речи как ISO 639-1 "
        "(например ru, en, de). Не определяй язык по заголовку. "
        "Если доминирующий язык доказать нельзя, верни mixed."
    )


async def create_factory_plan_from_supported_audio(
    audio_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
    status_msg: Any = None,
) -> dict[str, Any]:
    """Run the strict source-owned three-pass plan with bounded capacity retry."""
    from services.shorts_factory_capacity_runtime import create_factory_plan_resumable

    return await create_factory_plan_resumable(
        audio_path,
        title=title,
        performer=performer,
        duration=duration,
        source_language=source_language,
        status_msg=status_msg,
    )


__all__ = [
    "create_factory_plan_from_supported_audio",
    "download_factory_audio_source",
    "download_factory_video_source",
    "factory_audio_mime_type",
    "factory_audio_probe_is_usable",
    "factory_duration_matches",
    "gemini_analysis_bitrate_kbps",
    "gemini_analysis_sample_rate",
    "measure_factory_audio_duration",
    "prepare_factory_translation_video",
]
