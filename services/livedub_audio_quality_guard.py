#!/usr/bin/env python3
"""Final quality guard for LiveDub's two-MP3 delivery contract.

This module is installed after :mod:`services.livedub_audio_companion` and before
its dedupe/deep-audit wrappers.  It fixes two production edge cases without
changing Telegram's public API surface:

* stale derived MP3 files (``*.final-mix.mp3`` / legacy ``*.ru-audio.mp3``) can
  never be mistaken for the isolated Yandex translation on a retry;
* dual-audio mode is successful only when both the clean RU track and the exact
  final-video mix are available and delivered.  A partial delivery remains
  useful to the user, but is reported as incomplete so the video/file-id pair is
  not cached as healthy.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False

_DERIVED_AUDIO_MARKERS = (
    ".final-mix",
    ".ru-audio",
    " финальный микс",
    " чистый ru",
)
_DERIVED_AUDIO_PREFIXES = (
    "pro_dub",
    "live_dub_merged",
)


def is_derived_audio_artifact(path: Path | str) -> bool:
    """Return True for MP3s produced by delivery/QA rather than by Yandex.

    The check intentionally uses deterministic names owned by this project.  It
    does not guess from broad words such as ``translation`` because genuine VOT
    files commonly contain those words.
    """
    candidate = Path(path)
    name = candidate.name.casefold()
    stem = candidate.stem.casefold()
    if candidate.suffix.casefold() != ".mp3":
        return False
    if stem.startswith(_DERIVED_AUDIO_PREFIXES):
        return True
    return any(marker in name or marker in stem for marker in _DERIVED_AUDIO_MARKERS)


def select_clean_translation_mp3(workdir: Path | str) -> Path | None:
    """Select the newest genuine RU translation and ignore generated outputs."""
    root = Path(workdir)
    try:
        candidates = sorted(
            root.glob("*.mp3"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return None

    for candidate in candidates:
        low = candidate.name.casefold()
        if candidate.stat().st_size <= 1024:
            continue
        if low.startswith(("original_audio", "original_video")):
            continue
        if candidate.stem.casefold().endswith(("_qa", "_qa_original")):
            continue
        if is_derived_audio_artifact(candidate):
            continue
        return candidate
    return None


def _install_clean_track_selection() -> None:
    import services.livedub_mix as mix
    import services.yandex_live_dub as yandex

    current_find_tracks = mix.find_pro_tracks
    if not getattr(current_find_tracks, "_mp3bot_clean_track_guard", False):
        def guarded_find_tracks(workdir: Path):
            original, _legacy_ru = current_find_tracks(workdir)
            return original, select_clean_translation_mp3(workdir)

        guarded_find_tracks._mp3bot_clean_track_guard = True  # type: ignore[attr-defined]
        mix.find_pro_tracks = guarded_find_tracks

    current_latest = yandex._find_latest_file
    if not getattr(current_latest, "_mp3bot_clean_track_guard", False):
        def guarded_latest(directory: Path, pattern: str):
            if str(pattern).casefold() == "*.mp3":
                clean = select_clean_translation_mp3(directory)
                if clean is not None:
                    return clean
                return None
            return current_latest(directory, pattern)

        guarded_latest._mp3bot_clean_track_guard = True  # type: ignore[attr-defined]
        yandex._find_latest_file = guarded_latest


def _install_complete_dual_delivery() -> None:
    import services.livedub_audio_companion as companion

    current = companion._send_new_audio
    if getattr(current, "_mp3bot_complete_dual_delivery", False):
        return

    async def send_new_audio_complete(
        self,
        *,
        chat_id: Any,
        video_path: Path,
        caption: str,
        reply_to: Any,
        thumbnail: Any,
        video_file_id: str,
    ) -> bool:
        title, performer = companion._title_parts(caption, video_path.stem)
        video_ok, video_duration = await asyncio.to_thread(companion._probe_audio, video_path)
        if not video_ok:
            raise RuntimeError("финальное LiveDub-видео не содержит проверяемой аудиодорожки")

        dual = companion._dual_enabled()
        sources: list[tuple[str, Path]] = []
        failures: list[str] = []

        clean = await asyncio.to_thread(companion._find_clean_ru_track, video_path)
        if clean is not None and not is_derived_audio_artifact(clean):
            sources.append(("clean", clean))
        elif dual:
            failures.append("Чистый русский перевод: исходная RU-дорожка не сохранилась")

        mixed: Path | None = None
        try:
            mixed = await asyncio.to_thread(companion._extract_mix_mp3, video_path)
            sources.append(("mixed", mixed))
        except Exception as exc:
            failures.append(f"Финальный объединённый микс: {str(exc)[:180]}")
            logger.exception("[LiveDubAudioQuality] final mix extraction failed: %s", exc)

        if not dual:
            if clean is not None and not is_derived_audio_artifact(clean):
                sources = [("clean", clean)]
            elif mixed is not None:
                sources = [("mixed", mixed)]
            else:
                sources = []

        # Do not allow the same physical file to masquerade as two variants.
        unique_sources: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for variant, source in sources:
            try:
                identity = str(source.resolve()).casefold()
            except OSError:
                identity = str(source).casefold()
            if identity in seen:
                failures.append(f"{companion._VARIANT_LABELS[variant]}: совпадает с другой версией")
                continue
            seen.add(identity)
            unique_sources.append((variant, source))

        sent = 0
        for variant, source in unique_sources:
            try:
                if await companion._send_variant(
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
                failures.append(f"{companion._VARIANT_LABELS[variant]}: {str(exc)[:180]}")
                logger.exception("[LiveDubAudioQuality] %s delivery failed: %s", variant, exc)

        expected = 2 if dual else 1
        if sent == expected:
            logger.info("[LiveDubAudioQuality] complete MP3 set delivered: %d/%d", sent, expected)
            return True

        detail = "; ".join(failures) or "неизвестная ошибка"
        if sent:
            raise RuntimeError(f"отправлен неполный комплект MP3 ({sent}/{expected}); {detail}")
        raise RuntimeError(f"комплект MP3 не отправлен (0/{expected}); {detail}")

    send_new_audio_complete._mp3bot_complete_dual_delivery = True  # type: ignore[attr-defined]
    companion._send_new_audio = send_new_audio_complete


def install_livedub_audio_quality_guard() -> None:
    """Install clean-track selection and strict dual-delivery semantics once."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_clean_track_selection()
        _install_complete_dual_delivery()
        _INSTALLED = True
        logger.info(
            "🎧 LiveDub audio quality guard: genuine clean RU selection + strict 2/2 delivery"
        )
