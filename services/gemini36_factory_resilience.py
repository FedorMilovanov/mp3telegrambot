#!/usr/bin/env python3
"""Verified Gemini 3.6 reliability without semantic quality downgrades.

This runtime owns narrowly scoped production fixes:

* prepare a compact, high-quality AAC surrogate only for Gemini audio analysis;
  original video, LiveDub media and render sources are untouched;
* widen the app-owned 503/high-demand retry window while reusing the same upload;
* verify that user-visible LiveDub publication metadata owns Gemini 3.6/HIGH
  directly instead of mutating that route through import-order monkey-patching;
* optionally route GenerateContent calls through Gemini Priority inference when
  the operator explicitly enables it on an eligible Tier 2/3 project.

The semantic model remains ``gemini-3.6-flash`` with HIGH thinking. 3.5/Lite is
reserved for genuinely mechanical utility work.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GEMINI_ANALYSIS_BITRATE_KBPS = 128
_GEMINI_ANALYSIS_SAMPLE_RATE = 48000
_CAPACITY_ATTEMPTS = 4
_CAPACITY_BASE_SECONDS = 3.0
_CAPACITY_MAX_SECONDS = 20.0
_CAPACITY_JITTER_SECONDS = 2.0
_INSTALLED = False
_PRIORITY_INSTALLED = False


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def gemini_analysis_bitrate_kbps() -> int:
    """Return a conservative speech-analysis bitrate, never a low-fi preset."""
    return _bounded_int(
        "SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS",
        _GEMINI_ANALYSIS_BITRATE_KBPS,
        96,
        192,
    )


def gemini_analysis_sample_rate() -> int:
    """Keep a high source sample rate even though Gemini downsamples internally."""
    configured = _bounded_int(
        "SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE",
        _GEMINI_ANALYSIS_SAMPLE_RATE,
        24000,
        48000,
    )
    return 48000 if configured >= 36000 else 24000


def configured_service_tier() -> str:
    """Return the explicitly requested Gemini service tier.

    Priority is intentionally opt-in because GenerateContent Priority is limited
    to eligible paid Tier 2/3 projects. An invalid value fails closed at startup
    instead of silently routing requests differently than the operator expects.
    """
    value = os.getenv("GEMINI_SERVICE_TIER", "standard").strip().lower()
    value = {"": "standard", "default": "standard"}.get(value, value)
    if value not in {"standard", "priority"}:
        raise RuntimeError(
            "GEMINI_SERVICE_TIER must be 'standard' or 'priority'; "
            f"got {value!r}"
        )
    return value


async def _prepare_compact_gemini_audio(
    source_path: Path,
    source_probe: Any,
    media_id: str,
) -> Path:
    """Build a Gemini-only mono AAC surrogate and prove it is complete."""
    import services.shorts_factory_source as source

    source_path = Path(source_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare Factory Gemini audio")

    bitrate = gemini_analysis_bitrate_kbps()
    sample_rate = gemini_analysis_sample_rate()
    output_path = source.DOWNLOAD_DIR / f"{media_id}_factory_audio_gemini.aac"
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
    process = await source.run_cancellable_process(
        command,
        timeout=source._FACTORY_MEDIA_TIMEOUT_SEC,
        text=True,
    )
    if process.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "ffmpeg could not prepare compact Gemini Factory audio: "
            + source._stderr_tail(process)
        )

    probe = await source.probe_media_async(output_path)
    if not source.factory_audio_probe_is_usable(probe):
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

    source.factory_audio_mime_type(output_path)
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


def _replace_loaded_references(old: Any, new: Any) -> None:
    """Update helpers copied with ``from core.globals import ...`` before install."""
    for module in tuple(sys.modules.values()):
        if module is None:
            continue
        try:
            namespace = vars(module)
        except Exception:
            continue
        for name, value in tuple(namespace.items()):
            if value is old:
                try:
                    namespace[name] = new
                except Exception:
                    pass


def _with_service_tier(config: Any, tier: str) -> Any:
    """Add a supported SDK service-tier field without dropping other config."""
    if tier != "priority":
        return config
    if isinstance(config, dict):
        copied = dict(config)
        copied["service_tier"] = "priority"
        return copied
    model_copy = getattr(config, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"service_tier": "priority"})
    try:
        setattr(config, "service_tier", "priority")
    except Exception as exc:
        raise RuntimeError(
            "Installed google-genai SDK cannot apply service_tier='priority'; "
            "upgrade google-genai or set GEMINI_SERVICE_TIER=standard"
        ) from exc
    return config


def _install_priority_configs() -> None:
    """Attach Priority to shared GenerateContent configs only when opted in."""
    global _PRIORITY_INSTALLED
    if _PRIORITY_INSTALLED:
        return
    tier = configured_service_tier()
    if tier != "priority":
        return

    import core.globals as globals_module

    original_audio = globals_module.make_audio_config
    original_text_smart = globals_module.make_text_config_smart

    def priority_audio(*args, **kwargs):
        return _with_service_tier(original_audio(*args, **kwargs), tier)

    def priority_text_smart(*args, **kwargs):
        return _with_service_tier(original_text_smart(*args, **kwargs), tier)

    priority_audio._mp3bot_priority_tier = True  # type: ignore[attr-defined]
    priority_text_smart._mp3bot_priority_tier = True  # type: ignore[attr-defined]
    globals_module.make_audio_config = priority_audio
    globals_module.make_text_config_smart = priority_text_smart
    _replace_loaded_references(original_audio, priority_audio)
    _replace_loaded_references(original_text_smart, priority_text_smart)
    _PRIORITY_INSTALLED = True
    logger.info("Gemini GenerateContent service tier: priority (operator opt-in)")


def _verify_publication_quality_route() -> None:
    """Fail startup if the publication owner ever regresses from exact 3.6."""
    import services.livedub_publication_core as publication

    models = publication.publication_models()
    if models != ["gemini-3.6-flash"]:
        raise RuntimeError(
            "LiveDub publication owner must expose exact gemini-3.6-flash only; "
            f"got {models!r}"
        )
    if not callable(getattr(publication, "_quality_config", None)):
        raise RuntimeError("LiveDub publication owner is missing its HIGH quality config")


def install_gemini36_factory_resilience() -> str:
    """Install verified reliability changes after MAX runtime imports."""
    global _INSTALLED
    if _INSTALLED:
        return "Gemini 3.6/HIGH resilience already installed"

    import services.shorts_factory_capacity_runtime as capacity
    import services.shorts_factory_source as source

    source._prepare_gemini_audio = _prepare_compact_gemini_audio
    capacity._FACTORY_CAPACITY_PASS_ATTEMPTS = _CAPACITY_ATTEMPTS
    capacity._FACTORY_CAPACITY_RETRY_BASE_SECONDS = _CAPACITY_BASE_SECONDS
    capacity._FACTORY_CAPACITY_RETRY_MAX_SECONDS = _CAPACITY_MAX_SECONDS
    capacity._FACTORY_CAPACITY_RETRY_JITTER_SECONDS = _CAPACITY_JITTER_SECONDS
    _install_priority_configs()
    _verify_publication_quality_route()

    tier = configured_service_tier()
    _INSTALLED = True
    logger.info(
        "Gemini 3.6/HIGH resilience: AAC=%dk mono/%dHz; 503 attempts=%d "
        "backoff<=%.0fs; service_tier=%s; publication_owner=3.6/HIGH",
        gemini_analysis_bitrate_kbps(),
        gemini_analysis_sample_rate(),
        _CAPACITY_ATTEMPTS,
        _CAPACITY_MAX_SECONDS,
        tier,
    )
    return (
        "Gemini 3.6/HIGH; compact AAC analysis audio; publication_owner=3.6/HIGH; "
        f"503 attempts={_CAPACITY_ATTEMPTS}; service_tier={tier}"
    )


__all__ = [
    "configured_service_tier",
    "gemini_analysis_bitrate_kbps",
    "gemini_analysis_sample_rate",
    "install_gemini36_factory_resilience",
]
