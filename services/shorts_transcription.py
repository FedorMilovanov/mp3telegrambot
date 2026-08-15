#!/usr/bin/env python3
"""Owned Shorts transcription with an explicit subtitle profile."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from services import shorts_video_impl as _impl
from services.async_process import run_cancellable_process
from services.async_worker import await_owned_coroutine

logger = logging.getLogger(__name__)

DEFAULT_FACTORY_WHISPER_MODEL = "large-v3"
FACTORY_SUBTITLE_PROFILE: dict[str, Any] = {
    "model_name": DEFAULT_FACTORY_WHISPER_MODEL,
    "karaoke": True,
    "word_timestamps": True,
    "light": False,
    "gemini_hints": True,
}


def factory_subtitle_profile() -> dict[str, Any]:
    """Return the strict Factory profile without changing global subtitle settings."""
    profile = dict(FACTORY_SUBTITLE_PROFILE)
    profile["model_name"] = (
        os.getenv("SHORTS_FACTORY_WHISPER_MODEL", "").strip()
        or DEFAULT_FACTORY_WHISPER_MODEL
    )
    return profile


def resolve_subtitle_profile(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a complete profile without mutating global settings."""
    base = dict(_impl.get_subtitles_mode_settings())
    if override:
        base.update(dict(override))
    model_name = str(base.get("model_name") or "large-v3").strip() or "large-v3"
    karaoke = bool(base.get("karaoke", False))
    return {
        "model_name": model_name,
        "karaoke": karaoke,
        "word_timestamps": bool(base.get("word_timestamps", karaoke)),
        "light": bool(base.get("light", False)),
        "gemini_hints": bool(base.get("gemini_hints", True)),
    }


async def transcribe_short_clip(
    video_path: Path,
    ai_data: dict | None = None,
    *,
    subtitle_profile: dict[str, Any] | None = None,
) -> list[dict]:
    """Transcribe one clip using the supplied profile, with no ambient override."""
    if not _impl.HAS_FASTER_WHISPER:
        logger.warning("Subtitles: faster-whisper is unavailable")
        return []
    video_path = Path(video_path)
    if not video_path.is_file():
        logger.warning("Subtitles: source file not found: %s", video_path)
        return []

    config = resolve_subtitle_profile(subtitle_profile)
    model_size = config["model_name"]
    word_timestamps = bool(config["word_timestamps"])
    wav_path: Path | None = None
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("Subtitles: ffmpeg not found")
            return []
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        process = await run_cancellable_process(
            [
                ffmpeg,
                "-i",
                str(video_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                "-y",
                str(wav_path),
            ],
            timeout=120,
        )
        if process.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size < 1024:
            logger.warning("Subtitles: WAV extraction failed for %s", video_path.name)
            return []

        initial_prompt = _impl.build_whisper_initial_prompt(
            ai_data,
            use_gemini_hints=bool(config["gemini_hints"]),
        )
        wav_for_thread = wav_path

        def _run_whisper():
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except Exception:
                pass
            model = _impl._get_whisper_model(model_size)
            segments, info = model.transcribe(
                str(wav_for_thread),
                language="ru",
                initial_prompt=initial_prompt,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=word_timestamps,
            )
            result = [
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": _impl._polish_subtitle_text(segment.text),
                    "words": [
                        {
                            "word": _impl._polish_subtitle_text(word.word),
                            "start": word.start,
                            "end": word.end,
                        }
                        for word in (segment.words or [])
                    ],
                }
                for segment in segments
            ]
            return (
                result,
                getattr(info, "duration", 0),
                getattr(info, "language", "?"),
                getattr(info, "language_probability", 1.0),
            )

        from core.resource_scheduler import scheduler

        async with scheduler.whisper:
            segments, audio_duration, detected_lang, lang_prob = await await_owned_coroutine(
                asyncio.to_thread(_run_whisper)
            )
        wav_path.unlink(missing_ok=True)
        wav_path = None

        logger.info(
            "Subtitles: model=%s karaoke=%s word_ts=%s lang=%s confidence=%.2f duration=%.1fs",
            model_size,
            config["karaoke"],
            word_timestamps,
            detected_lang,
            lang_prob,
            audio_duration,
        )
        if lang_prob < 0.4:
            logger.info("Subtitles: rejected low language confidence %.2f", lang_prob)
            return []
        return [segment for segment in segments if segment.get("text")]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Subtitles transcribe error (%s): %s", type(exc).__name__, exc)
        return []
    finally:
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "DEFAULT_FACTORY_WHISPER_MODEL",
    "FACTORY_SUBTITLE_PROFILE",
    "factory_subtitle_profile",
    "resolve_subtitle_profile",
    "transcribe_short_clip",
]
