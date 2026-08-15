#!/usr/bin/env python3
"""Maximum-quality media acquisition and Gemini audio input for Factory."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.media_delivery_probe import (
    media_probe_is_deliverable,
    probe_media_async,
)

logger = logging.getLogger(__name__)

_FACTORY_AUDIO_INLINE_LIMIT_BYTES = 18 * 1024 * 1024
_FACTORY_MEDIA_TIMEOUT_SEC = 7200
_FACTORY_TAIL_TOLERANCE_SEC = 0.20
_GEMINI_ANALYSIS_BITRATE_KBPS = 128
_GEMINI_ANALYSIS_SAMPLE_RATE = 48000
_INSTALLED = False

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
    """Return the Factory-owned Gemini analysis AAC bitrate."""
    return _bounded_int(
        "SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS",
        _GEMINI_ANALYSIS_BITRATE_KBPS,
        96,
        192,
    )


def gemini_analysis_sample_rate() -> int:
    """Return the Factory-owned Gemini analysis sample rate."""
    configured = _bounded_int(
        "SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE",
        _GEMINI_ANALYSIS_SAMPLE_RATE,
        24000,
        48000,
    )
    return 48000 if configured >= 36000 else 24000


def factory_audio_mime_type(path: Path) -> str:
    """Return an officially supported Gemini audio MIME for a prepared file."""
    mime_type = _AUDIO_MIME_BY_SUFFIX.get(Path(path).suffix.casefold(), "")
    if not mime_type:
        raise RuntimeError(
            "SHORTS FACTORY audio format is not supported by Gemini: "
            f"{Path(path).suffix or '<no extension>'}"
        )
    return mime_type


def factory_audio_probe_is_usable(probe: Any) -> bool:
    """Require concrete audio evidence without requiring a video stream."""
    return bool(
        probe is not None
        and float(getattr(probe, "duration", 0.0) or 0.0) > 0
        and getattr(probe, "has_audio", False)
        and int(getattr(probe, "audio_sample_rate", 0) or 0) > 0
        and str(getattr(probe, "audio_codec", "") or "").strip()
    )


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
    """Return the quality-first Factory LiveDub timeout."""
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
) -> Path:
    """Build the compact Gemini-only mono AAC surrogate at the source owner."""
    source_path = Path(source_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare Factory Gemini audio")

    bitrate = gemini_analysis_bitrate_kbps()
    sample_rate = gemini_analysis_sample_rate()
    output_path = DOWNLOAD_DIR / f"{media_id}_factory_audio_gemini.aac"
    output_path.unlink(missing_ok=True)

    command = [
        ffmpeg,
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

    probe = await probe_media_async(output_path)
    if not factory_audio_probe_is_usable(probe):
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Compact Gemini Factory audio failed its audio-stream probe")

    source_duration = float(getattr(source_probe, "duration", 0.0) or 0.0)
    final_duration = float(getattr(probe, "duration", 0.0) or 0.0)
    if source_duration > 0 and final_duration + 2.0 < source_duration:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Compact Gemini Factory audio is truncated: "
            f"source={source_duration:.3f}s final={final_duration:.3f}s"
        )
    if output_path.stat().st_size < 1024:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Compact Gemini Factory audio is empty")

    factory_audio_mime_type(output_path)
    if output_path != source_path:
        try:
            source_path.unlink(missing_ok=True)
        except OSError:
            pass

    logger.info(
        "Factory Gemini analysis audio prepared: codec=AAC mono bitrate=%dk "
        "sample_rate=%d duration=%.3fs size=%.1fMB",
        bitrate,
        sample_rate,
        final_duration,
        output_path.stat().st_size / (1024 * 1024),
    )
    return output_path


def _factory_quality_sort_reset() -> list[str]:
    """Discard local container preferences while preserving auth/network args."""
    return [
        "--format-sort-reset",
        "--no-format-sort-force",
        "--no-prefer-free-formats",
    ]


async def download_factory_audio_source(url: str, media_id: str) -> Path:
    """Download best native audio, then prepare compact Gemini-only AAC."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _remove_paths(DOWNLOAD_DIR.glob(f"{media_id}_factory_audio_*"))
    output_template = DOWNLOAD_DIR / f"{media_id}_factory_audio_source.%(ext)s"
    command = list(YTDLP_BASE_ARGS) + _factory_quality_sort_reset() + [
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
        return await _prepare_gemini_audio(source_path, source_probe, media_id)
    except (asyncio.CancelledError, Exception):
        _remove_paths(DOWNLOAD_DIR.glob(f"{media_id}_factory_audio_*"))
        raise


async def download_factory_video_source(
    url: str,
    media_id: str,
    workdir: Path | None = None,
) -> Path:
    """Download the best available video+audio without a resolution ceiling."""
    target_dir = Path(workdir) if workdir is not None else DOWNLOAD_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
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
        download_factory_video_source(url, "translated", workdir=workdir)
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


async def create_factory_plan_from_supported_audio(
    audio_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
) -> dict[str, Any]:
    """Run all three Factory passes with the real prepared-audio MIME type."""
    import services.shorts_factory_candidates as candidates

    if (
        not candidates.HAS_GEMINI
        or not candidates.GEMINI_CLIENTS
        or candidates.types is None
    ):
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini")
    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")

    mime_type = factory_audio_mime_type(audio_path)
    model = candidates.shorts_factory_model()
    file_size = audio_path.stat().st_size
    last_error: Exception | None = None

    for client_index, client in enumerate(candidates.GEMINI_CLIENTS, 1):
        uploaded_name = ""
        try:
            if file_size <= _FACTORY_AUDIO_INLINE_LIMIT_BYTES:
                audio_part = candidates.types.Part.from_bytes(
                    data=audio_path.read_bytes(),
                    mime_type=mime_type,
                )
            else:
                uploaded = await client.aio.files.upload(
                    file=audio_path,
                    config=candidates.types.UploadFileConfig(
                        mime_type=mime_type,
                        display_name=(f"Shorts Factory MAX — {performer} — {title}")[:500],
                    ),
                )
                uploaded = await candidates._wait_uploaded_file(client, uploaded)
                audio_part = uploaded
                uploaded_name = str(getattr(uploaded, "name", "") or "")

            scout = await candidates._run_pass(
                client,
                model=model,
                audio_part=audio_part,
                prompt=candidates._scout_prompt(
                    title,
                    performer,
                    duration,
                    source_language,
                ),
                max_tokens=32000,
            )
            judged = await candidates._run_pass(
                client,
                model=model,
                audio_part=audio_part,
                prompt=candidates._judge_prompt(scout, duration),
                max_tokens=28000,
            )
            audited = await candidates._run_pass(
                client,
                model=model,
                audio_part=audio_part,
                prompt=candidates._boundary_prompt(judged, duration),
                max_tokens=28000,
            )

            plan = candidates.validate_factory_plan(
                audited,
                duration,
                require_verified=True,
            )
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError(
                    "Three-pass Gemini review produced no candidates with verified boundaries"
                )
            plan["model"] = model
            plan["thinking_level"] = "high"
            plan["review_passes"] = 3
            plan["strict_quality"] = True
            plan["audio_mime_type"] = mime_type
            return plan
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Shorts Factory source client %d/%d failed strict review: %s: %s",
                client_index,
                len(candidates.GEMINI_CLIENTS),
                type(exc).__name__,
                exc,
            )
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    raise RuntimeError(
        "All Gemini clients failed strict Shorts Factory review: "
        f"{last_error}"
    )


def install_factory_source_quality_policy() -> bool:
    """Install native-audio and unrestricted-video Factory sources."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.shorts_factory as factory_pipeline
    import services.shorts_factory_candidates as candidates_module

    factory_pipeline._download_factory_audio = download_factory_audio_source
    factory_pipeline.download_video_for_shorts = download_factory_video_source
    factory_pipeline._prepare_translation_video = prepare_factory_translation_video
    factory_pipeline.create_factory_plan = create_factory_plan_from_supported_audio
    candidates_module.create_factory_plan = create_factory_plan_from_supported_audio

    eager_factory = sys.modules.get("pipelines.shorts_factory")
    if eager_factory is not None:
        eager_factory._download_factory_audio = download_factory_audio_source
        eager_factory.download_video_for_shorts = download_factory_video_source
        eager_factory._prepare_translation_video = prepare_factory_translation_video
        eager_factory.create_factory_plan = create_factory_plan_from_supported_audio

    _INSTALLED = True
    logger.info(
        "Shorts Factory source quality installed: native bestaudio -> compact AAC mono, "
        "unrestricted bestvideo+bestaudio for Russian and Yandex LiveDub sources, "
        "required Russian tail, and mandatory media probes"
    )
    return True


__all__ = [
    "create_factory_plan_from_supported_audio",
    "download_factory_audio_source",
    "download_factory_video_source",
    "factory_audio_mime_type",
    "factory_audio_probe_is_usable",
    "gemini_analysis_bitrate_kbps",
    "gemini_analysis_sample_rate",
    "install_factory_source_quality_policy",
    "prepare_factory_translation_video",
]
