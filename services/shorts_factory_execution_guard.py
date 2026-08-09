#!/usr/bin/env python3
"""Quality-first execution contract for SHORTS FACTORY MAX."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR, GEMINI_CLIENTS
from core.url_utils import get_youtube_video_url
from core.utils import parse_title
from services.shorts_factory_candidates import factory_ai_data
from services.shorts_factory_runtime import (
    factory_completed_delivery_counts,
    factory_render_context,
)

logger = logging.getLogger(__name__)

_INSTALLED = False

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

    compact = text.replace("_", "-")
    primary = compact.split("-", 1)[0].strip()
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
    """Prefer language heard by Gemini; metadata is only a fallback."""
    metadata = plan.get("metadata") if isinstance(plan, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    heard = normalize_factory_language(metadata.get("language"))
    declared = normalize_factory_language((info or {}).get("language"))

    if heard:
        if declared and declared != heard:
            logger.warning(
                "Shorts Factory language mismatch: Gemini heard=%s, "
                "yt-dlp declared=%s; using spoken-audio evidence",
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
    """Russian output requires LiveDub for every proven non-Russian language."""
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
        issues.append(
            f"free disk {free_gb:.1f} GB is below {min_free_gb:.1f} GB"
        )
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
        (
            os.getenv("VOT_API_TOKEN", "")
            or os.getenv("YANDEX_OAUTH_TOKEN", "")
        ).strip()
    )
    issues = factory_translation_preflight_issues(
        oauth_present=oauth_present,
        helper_available=helper_available,
        cli_available=cli_available,
        require_oauth=_env_bool(
            "SHORTS_FACTORY_REQUIRE_VOT_TOKEN",
            True,
        ),
    )
    if issues:
        raise RuntimeError(
            "Factory LiveDub preflight failed: " + "; ".join(issues)
        )


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
            free_values.append(
                shutil.disk_usage(location).free / (1024 ** 3)
            )
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
        raise RuntimeError(
            "Factory preflight failed: " + "; ".join(issues)
        )


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _cleanup_stale_factory_audio(media_id: str) -> None:
    for path in DOWNLOAD_DIR.glob(f"{media_id}_factory*.mp3"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _stage_detail(errors: list[str]) -> str:
    if not errors:
        return ""
    return "; ".join(errors)[:700]


def _translation_source_error(exc: Exception) -> str:
    """Describe provider failures separately from local post-LiveDub failures."""
    reason = str(exc).strip() or type(exc).__name__
    if "LIVEDUB_AUTH_REQUIRED" in reason or "LIVEDUB_NOT_AVAILABLE" in reason:
        return (
            "Яндекс LiveDub «Живые голоса» недоступен для этого источника. "
            "Нарезка иностранного оригинала и собственный нейроперевод "
            f"намеренно не выполняются. Причина: {reason[:240]}"
        )
    if "Factory LiveDub" in reason or "Yandex live audio" in reason:
        return (
            "Яндекс LiveDub «Живые голоса» получен, но локальная сборка "
            "Factory не прошла обязательную проверку качества. Ничего не "
            "отправлено, чтобы не выдать обрезанный или битый перевод. "
            f"Причина: {reason[:240]}"
        )
    return (
        "Не удалось подготовить проверенный русский LiveDub-источник для "
        "Factory. Это не означает автоматически, что перевод Яндекса "
        f"отсутствует. Причина: {reason[:240]}"
    )


async def process_shorts_factory_guarded(
    url,
    update,
    status_msg=None,
    progress_prefix="",
    context=None,
    silent_errors: bool = False,
):
    """Analyze first, resolve spoken language, then render truthfully."""
    del context, progress_prefix

    import pipelines.shorts_factory as factory_module

    url = get_youtube_video_url(url)
    mp3_path: Path | None = None
    persistent_source_path: Path | None = None
    keep_source_for_trim = False
    workdir = Path(tempfile.mkdtemp(prefix="shorts_factory_"))
    source_task: asyncio.Task | None = None

    try:
        enforce_factory_preflight()
        factory_module._cleanup_expired_factory_sources()
        if status_msg is None:
            status_msg = await update.message.reply_text(
                "🧠 SHORTS FACTORY MAX: получаю метаданные…"
            )
        else:
            await factory_module._safe_status(
                status_msg,
                "🧠 SHORTS FACTORY MAX: получаю метаданные…",
            )

        info = await factory_module._load_video_info(url)
        if info.get("is_live") or info.get("live_status") in {
            "is_live",
            "is_upcoming",
            "post_live",
        }:
            raise RuntimeError(
                "Live-трансляцию можно нарезать только после завершения "
                "обработки записи"
            )

        try:
            duration = int(float(info.get("duration") or 0))
        except (TypeError, ValueError, OverflowError):
            duration = 0
        if duration <= 0:
            raise RuntimeError("Не удалось определить длительность видео")

        max_duration = _env_int(
            "SHORTS_FACTORY_MAX_SOURCE_SEC",
            10800,
            60,
            24 * 3600,
        )
        if duration > max_duration:
            raise RuntimeError(
                f"Источник {duration // 60} мин превышает лимит режима "
                f"{max_duration // 60} мин"
            )

        media_id = factory_module._media_id(info, url)
        full_title = str(info.get("title") or "Видео").strip()
        channel_name = str(
            info.get("channel") or info.get("uploader") or ""
        ).strip()
        performer, title = parse_title(full_title, channel_name)
        metadata_language = str(info.get("language") or "").strip().lower()

        _cleanup_stale_factory_audio(media_id)
        await factory_module._safe_status(
            status_msg,
            "🎧 SHORTS FACTORY MAX: скачиваю аудио максимального качества "
            "для точного анализа…",
        )
        mp3_path = await factory_module._download_factory_audio(url, media_id)

        await factory_module._safe_status(
            status_msg,
            "🧠 Gemini MAX слушает весь материал: отбор, редактура "
            "и проверка границ…",
        )
        plan = await factory_module.create_factory_plan(
            mp3_path,
            title=title or full_title,
            performer=performer or channel_name,
            duration=duration,
            source_language=metadata_language,
        )
        spoken_language = resolve_factory_spoken_language(plan, info)
        translation_required = factory_language_needs_translation(spoken_language)
        ai_data = factory_ai_data(
            plan,
            title=title or full_title,
            performer=performer or channel_name,
        )

        await factory_module._safe_status(
            status_msg,
            "🎙 План и язык речи проверены. Подготавливаю единый "
            "источник для всех вырезок…",
        )
        if translation_required:
            enforce_factory_translation_preflight()
            source_task = asyncio.create_task(
                factory_module._prepare_translation_video(
                    url,
                    workdir,
                    duration,
                    spoken_language,
                ),
                name=f"shorts-factory-yandex-{media_id}",
            )
        else:
            source_task = asyncio.create_task(
                factory_module.download_video_for_shorts(
                    url,
                    media_id,
                    workdir=workdir,
                ),
                name=f"shorts-factory-source-{media_id}",
            )

        source_timeout = _env_int(
            "SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC",
            1800,
            60,
            7200,
        )
        try:
            source_video_path = await asyncio.wait_for(
                source_task,
                timeout=source_timeout,
            )
        except asyncio.TimeoutError as exc:
            source_task.cancel()
            if translation_required:
                raise RuntimeError(
                    f"Яндекс LiveDub не завершился за "
                    f"{source_timeout // 60} мин. "
                    "Нейроперевод намеренно не используется."
                ) from exc
            raise RuntimeError(
                f"Исходное видео не скачалось за "
                f"{source_timeout // 60} мин."
            ) from exc
        except Exception as exc:
            if translation_required:
                raise RuntimeError(_translation_source_error(exc)) from exc
            raise

        if not source_video_path or not Path(source_video_path).is_file():
            raise RuntimeError(
                "Не удалось получить общий видеоисточник для Factory"
            )
        persistent_source_path = factory_module._persist_factory_source(
            Path(source_video_path),
            media_id,
        )
        render_source_duration = await factory_module._validated_source_duration(
            persistent_source_path,
            expected_duration=duration,
        )

        if not silent_errors:
            await update.message.reply_text(
                factory_module._plan_message(
                    plan,
                    translation_required=translation_required,
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await factory_module._safe_status(
            status_msg,
            "✂️ Рендерю SHORTS HIGHLIGHTS и длинные фрагменты…",
        )

        shorts_candidates = plan.get("shorts_candidates") or []
        long_candidates = plan.get("long_candidates") or []
        render_shorts = shorts_candidates
        render_longs = long_candidates
        stage_errors: list[str] = []

        if translation_required:
            try:
                render_shorts = factory_module._shift_candidates_for_livedub(
                    shorts_candidates,
                    source_duration=render_source_duration,
                )
            except Exception as exc:
                render_shorts = []
                stage_errors.append(f"Shorts timing: {str(exc)[:180]}")
            try:
                render_longs = factory_module._shift_candidates_for_livedub(
                    long_candidates,
                    source_duration=render_source_duration,
                )
            except Exception as exc:
                render_longs = []
                stage_errors.append(f"Long timing: {str(exc)[:180]}")

        with factory_render_context(render_shorts, render_longs):
            if render_shorts:
                try:
                    await factory_module.process_and_send_shorts(
                        url=url,
                        media_id=media_id,
                        mp3_path=mp3_path,
                        title=title or full_title,
                        performer=performer or channel_name,
                        duration=render_source_duration,
                        ai_data=ai_data,
                        update=update,
                        workdir=workdir,
                        livedub_video_path=persistent_source_path,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "Shorts Factory short stage failed after possible "
                        "partial delivery: %s",
                        exc,
                    )
                    stage_errors.append(f"Shorts: {str(exc)[:180]}")

            if render_longs:
                try:
                    await factory_module.process_and_send_clips(
                        url=url,
                        media_id=media_id,
                        mp3_path=mp3_path,
                        title=title or full_title,
                        performer=performer or channel_name,
                        duration=render_source_duration,
                        ai_data=ai_data,
                        update=update,
                        livedub_video_path=persistent_source_path,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "Shorts Factory long stage failed after possible "
                        "partial delivery: %s",
                        exc,
                    )
                    stage_errors.append(f"Long: {str(exc)[:180]}")

        shorts_sent, longs_sent = factory_completed_delivery_counts()
        keep_source_for_trim = shorts_sent > 0

        if shorts_sent < len(render_shorts):
            stage_errors.append(
                f"Shorts доставлено {shorts_sent}/{len(render_shorts)}"
            )
        if longs_sent < len(render_longs):
            stage_errors.append(
                f"Long доставлено {longs_sent}/{len(render_longs)}"
            )

        total_sent = shorts_sent + longs_sent
        if total_sent <= 0:
            detail = _stage_detail(stage_errors) or (
                "рендереры не подтвердили ни одной Telegram-доставки"
            )
            raise RuntimeError(
                "Не доставлено ни одного Factory-фрагмента. " + detail
            )

        if stage_errors:
            await factory_module._safe_status(
                status_msg,
                "⚠️ SHORTS FACTORY MAX частично завершён: "
                f"{shorts_sent} Shorts, {longs_sent} длинных фрагмента. "
                f"{_stage_detail(stage_errors)}",
            )
        else:
            await factory_module._safe_status(
                status_msg,
                "✅ SHORTS FACTORY MAX завершён: "
                f"{shorts_sent} Shorts, {longs_sent} длинных фрагмента.",
            )

        logger.info(
            "Shorts Factory MAX done media_id=%s original=%ss source=%.3fs "
            "shorts=%d/%d longs=%d/%d yandex=%s spoken_language=%s "
            "partial=%s",
            media_id,
            duration,
            float(render_source_duration),
            shorts_sent,
            len(render_shorts),
            longs_sent,
            len(render_longs),
            translation_required,
            spoken_language,
            bool(stage_errors),
        )
        return True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Shorts Factory MAX failed: %s", exc)
        if not silent_errors:
            message = f"❌ SHORTS FACTORY MAX: {str(exc)[:500]}"
            if status_msg:
                await factory_module._safe_status(status_msg, message)
            else:
                await update.message.reply_text(message)
        return False
    finally:
        if source_task is not None and not source_task.done():
            source_task.cancel()
            try:
                await source_task
            except BaseException:
                pass
        if mp3_path is not None:
            try:
                mp3_path.unlink(missing_ok=True)
            except OSError:
                pass
        if persistent_source_path is not None and not keep_source_for_trim:
            try:
                persistent_source_path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


def install_shorts_factory_execution_guard() -> bool:
    """Replace the legacy Factory orchestration with the strict executor."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.shorts_factory as factory_module

    factory_module.process_shorts_factory = process_shorts_factory_guarded
    _INSTALLED = True
    logger.info(
        "Shorts Factory execution guard installed: audio-proven language, "
        "deferred source selection and truthful partial delivery"
    )
    return True


__all__ = [
    "enforce_factory_preflight",
    "enforce_factory_translation_preflight",
    "factory_language_needs_translation",
    "factory_preflight_issues",
    "factory_translation_preflight_issues",
    "install_shorts_factory_execution_guard",
    "normalize_factory_language",
    "process_shorts_factory_guarded",
    "resolve_factory_spoken_language",
]