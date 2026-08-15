#!/usr/bin/env python3
"""Pure preflight and spoken-language contract for SHORTS FACTORY MAX.

The Factory pipeline owns orchestration and delivery.  This module contains only
validation helpers plus a compatibility entry function that delegates directly
to that owner; it performs no runtime rebinding or import-time installation.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR, GEMINI_CLIENTS

logger = logging.getLogger(__name__)

_LANGUAGE_ALIASES = {
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "русский": "ru",
    "русская": "ru",
    "рус": "ru",
    "en": "en",
    "eng": "en",
    "english": "en",
    "английский": "en",
    "англ": "en",
    "uk": "uk",
    "ukr": "uk",
    "ukrainian": "uk",
    "украинский": "uk",
    "be": "be",
    "bel": "be",
    "belarusian": "be",
    "белорусский": "be",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "французский": "fr",
    "de": "de",
    "deu": "de",
    "ger": "de",
    "german": "de",
    "немецкий": "de",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "испанский": "es",
    "it": "it",
    "ita": "it",
    "italian": "it",
    "итальянский": "it",
    "pl": "pl",
    "pol": "pl",
    "polish": "pl",
    "польский": "pl",
    "pt": "pt",
    "por": "pt",
    "portuguese": "pt",
    "португальский": "pt",
}


def normalize_factory_language(value: Any) -> str:
    """Normalize Gemini/yt-dlp language evidence to a stable ISO-like code."""
    text = str(value or "").strip().lower()
    if not text or text in {
        "unknown",
        "und",
        "none",
        "неизвестно",
        "unknown language",
    }:
        return ""
    direct = _LANGUAGE_ALIASES.get(text)
    if direct:
        return direct

    primary = text.replace("_", "-").split("-", 1)[0].strip()
    direct = _LANGUAGE_ALIASES.get(primary)
    if direct:
        return direct

    for label, code in _LANGUAGE_ALIASES.items():
        if len(label) > 2 and label in text:
            return code
    if len(primary) == 2 and primary.isalpha():
        return primary
    return ""


def resolve_factory_spoken_language(
    plan: dict[str, Any],
    info: dict[str, Any],
) -> str:
    """Prefer language heard by Gemini; yt-dlp metadata is only a fallback."""
    metadata = plan.get("metadata") if isinstance(plan, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    heard = normalize_factory_language(metadata.get("language"))
    declared = normalize_factory_language((info or {}).get("language"))
    if heard:
        if declared and declared != heard:
            logger.warning(
                "Shorts Factory language mismatch: Gemini heard=%s, yt-dlp declared=%s; "
                "using spoken-audio evidence",
                heard,
                declared,
            )
        return heard
    if declared:
        logger.warning(
            "Shorts Factory Gemini language missing; using yt-dlp metadata=%s",
            declared,
        )
        return declared
    raise RuntimeError(
        "Не удалось доказать язык речи по аудио или метаданным. "
        "SHORTS FACTORY не определяет перевод по языку заголовка."
    )


def factory_language_needs_translation(language: str) -> bool:
    normalized = normalize_factory_language(language)
    if not normalized:
        raise RuntimeError("Язык речи не определён")
    return normalized != "ru"


def factory_preflight_issues(
    *,
    gemini_available: bool,
    whisper_available: bool,
    ffmpeg_available: bool,
    ffprobe_available: bool,
    free_gb: float,
    min_free_gb: float,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not gemini_available:
        issues.append("Gemini API clients are unavailable")
    if not whisper_available:
        issues.append("faster-whisper is unavailable")
    if not ffmpeg_available:
        issues.append("ffmpeg is unavailable")
    if not ffprobe_available:
        issues.append("ffprobe is unavailable")
    if free_gb < min_free_gb:
        issues.append(f"free disk {free_gb:.1f} GB is below {min_free_gb:.1f} GB")
    return tuple(issues)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def factory_translation_preflight_issues(
    *,
    oauth_present: bool,
    helper_available: bool,
    cli_available: bool,
    require_oauth: bool,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not helper_available and not cli_available:
        issues.append("Yandex LiveDub client route is unavailable")
    if require_oauth and not oauth_present:
        issues.append("VOT_API_TOKEN/YANDEX_OAUTH_TOKEN is missing")
    return tuple(issues)


def enforce_factory_translation_preflight() -> None:
    root = Path(__file__).resolve().parent.parent
    helper_dir = root / "vot_helper"
    helper_script = helper_dir / "vot_live.mjs"
    helper_dependency = helper_dir / "node_modules" / "@vot.js" / "node"
    node_available = bool(shutil.which("node") or shutil.which("node.exe"))
    npm_available = bool(shutil.which("npm") or shutil.which("npm.cmd"))
    helper_available = bool(
        node_available
        and helper_script.is_file()
        and (helper_dependency.exists() or npm_available)
    )
    cli_available = bool(
        shutil.which("vot-cli-live")
        or shutil.which("vot-cli-live.cmd")
        or shutil.which("npx")
        or shutil.which("npx.cmd")
    )
    oauth_present = bool(
        (os.getenv("VOT_API_TOKEN", "") or os.getenv("YANDEX_OAUTH_TOKEN", "")).strip()
    )
    issues = factory_translation_preflight_issues(
        oauth_present=oauth_present,
        helper_available=helper_available,
        cli_available=cli_available,
        require_oauth=_env_bool("SHORTS_FACTORY_REQUIRE_VOT_TOKEN", True),
    )
    if issues:
        raise RuntimeError("Factory LiveDub preflight failed: " + "; ".join(issues))


def _min_free_gb() -> float:
    try:
        value = float(os.getenv("SHORTS_FACTORY_MIN_FREE_GB", "2.0") or "2.0")
    except (TypeError, ValueError):
        value = 2.0
    return max(0.5, min(value, 100.0))


def enforce_factory_preflight() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    free_values: list[float] = []
    for location in (DOWNLOAD_DIR, Path(tempfile.gettempdir())):
        try:
            free_values.append(shutil.disk_usage(location).free / (1024**3))
        except OSError:
            continue
    free_gb = min(free_values) if free_values else 0.0
    try:
        import services.shorts_video_impl as shorts_video_impl
        whisper_available = bool(shorts_video_impl.HAS_FASTER_WHISPER)
    except Exception:
        whisper_available = False
    issues = factory_preflight_issues(
        gemini_available=bool(GEMINI_CLIENTS),
        whisper_available=whisper_available,
        ffmpeg_available=bool(shutil.which("ffmpeg")),
        ffprobe_available=bool(shutil.which("ffprobe")),
        free_gb=free_gb,
        min_free_gb=_min_free_gb(),
    )
    if issues:
        raise RuntimeError("Factory preflight failed: " + "; ".join(issues))


def _translation_source_error(exc: Exception) -> str:
    reason = str(exc).strip() or type(exc).__name__
    if "LIVEDUB_AUTH_REQUIRED" in reason or "LIVEDUB_NOT_AVAILABLE" in reason:
        return (
            "Яндекс LiveDub «Живые голоса» недоступен для этого источника. "
            "Нарезка иностранного оригинала и собственный нейроперевод намеренно "
            f"не выполняются. Причина: {reason[:240]}"
        )
    if "Factory LiveDub" in reason or "Yandex live audio" in reason:
        return (
            "Яндекс LiveDub «Живые голоса» получен, но локальная сборка Factory "
            "не прошла обязательную проверку качества. Ничего не отправлено, чтобы "
            f"не выдать обрезанный или битый перевод. Причина: {reason[:240]}"
        )
    return (
        "Не удалось подготовить проверенный русский LiveDub-источник для Factory. "
        "Это не означает автоматически, что перевод Яндекса отсутствует. "
        f"Причина: {reason[:240]}"
    )


async def process_shorts_factory_guarded(
    url,
    update,
    status_msg=None,
    progress_prefix="",
    context=None,
    silent_errors: bool = False,
):
    """Validate local prerequisites, then invoke the real Factory owner."""
    enforce_factory_preflight()
    from pipelines.shorts_factory import process_shorts_factory

    return await process_shorts_factory(
        url,
        update,
        status_msg=status_msg,
        progress_prefix=progress_prefix,
        context=context,
        silent_errors=silent_errors,
    )


__all__ = [
    "enforce_factory_preflight",
    "enforce_factory_translation_preflight",
    "factory_language_needs_translation",
    "factory_preflight_issues",
    "factory_translation_preflight_issues",
    "normalize_factory_language",
    "process_shorts_factory_guarded",
    "resolve_factory_spoken_language",
]
