#!/usr/bin/env python3
"""Strict clean-track selection and two-MP3 delivery for LiveDub."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False
_DERIVED_MARKERS = (".final-mix", ".ru-audio", " финальный микс", " чистый ru")
_DERIVED_PREFIXES = ("pro_dub", "live_dub_merged")


def is_derived_audio_artifact(path: Path | str) -> bool:
    candidate = Path(path)
    name = candidate.name.casefold()
    stem = candidate.stem.casefold()
    if candidate.suffix.casefold() != ".mp3":
        return False
    return stem.startswith(_DERIVED_PREFIXES) or any(
        marker in name or marker in stem for marker in _DERIVED_MARKERS
    )


def select_clean_translation_mp3(workdir: Path | str) -> Path | None:
    """Choose by file role; ffprobe validates integrity later."""
    try:
        candidates = sorted(
            Path(workdir).glob("*.mp3"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return None
    for candidate in candidates:
        low = candidate.name.casefold()
        try:
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                continue
        except OSError:
            continue
        if low.startswith(("original_audio", "original_video")):
            continue
        if candidate.stem.casefold().endswith(("_qa", "_qa_original")):
            continue
        if not is_derived_audio_artifact(candidate):
            return candidate
    return None


def _install_clean_track_selection() -> None:
    import services.livedub_mix as mix
    import services.yandex_live_dub as yandex

    current_tracks = mix.find_pro_tracks
    if not getattr(current_tracks, "_mp3bot_clean_track_guard", False):
        def guarded_tracks(workdir: Path):
            original, _legacy = current_tracks(workdir)
            return original, select_clean_translation_mp3(workdir)
        guarded_tracks._mp3bot_clean_track_guard = True  # type: ignore[attr-defined]
        mix.find_pro_tracks = guarded_tracks

    current_latest = yandex._find_latest_file
    if not getattr(current_latest, "_mp3bot_clean_track_guard", False):
        def guarded_latest(directory: Path, pattern: str):
            if str(pattern).casefold() == "*.mp3":
                return select_clean_translation_mp3(directory)
            return current_latest(directory, pattern)
        guarded_latest._mp3bot_clean_track_guard = True  # type: ignore[attr-defined]
        yandex._find_latest_file = guarded_latest


def _install_conversion_postcondition() -> None:
    """A zero ffmpeg code is success only when the validated output exists."""
    import services.project_runtime_hardening as hardening

    current = hardening._SubprocessProxy.run
    if getattr(current, "_mp3bot_audio_postcondition", False):
        return

    def checked_run(self, *args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        guarded = hardening._guarded_mp3_command(command)
        result = current(self, *args, **kwargs)
        if guarded is None or result.returncode != 0:
            return result
        _cmd, _source, output = guarded
        if output.exists() and hardening._ffprobe_audio_ok(output):
            return result

        message = "MP3 conversion returned success without a valid output"
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr + (b"\n" if stderr else b"") + message.encode("utf-8")
        else:
            stderr = f"{stderr or ''}\n{message}".strip()
        logger.error("[AudioConversion] %s: %s", message, output)
        return hardening._subprocess.CompletedProcess(
            result.args, 1, result.stdout, stderr
        )

    checked_run._mp3bot_audio_postcondition = True  # type: ignore[attr-defined]
    hardening._SubprocessProxy.run = checked_run


def _install_complete_dual_delivery() -> None:
    import services.livedub_audio_companion as companion

    current = companion._send_new_audio
    if getattr(current, "_mp3bot_complete_dual_delivery", False):
        return

    async def send_complete(
        self, *, chat_id: Any, video_path: Path, caption: str, reply_to: Any,
        thumbnail: Any, video_file_id: str,
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

        mixed = None
        try:
            mixed = await asyncio.to_thread(companion._extract_mix_mp3, video_path)
            sources.append(("mixed", mixed))
        except Exception as exc:
            failures.append(f"Финальный объединённый микс: {str(exc)[:180]}")
            logger.exception("[LiveDubAudioQuality] mix extraction failed: %s", exc)

        if not dual:
            sources = [("clean", clean)] if clean is not None else (
                [("mixed", mixed)] if mixed is not None else []
            )

        unique: list[tuple[str, Path]] = []
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
            unique.append((variant, source))

        sent = 0
        for variant, source in unique:
            try:
                ok = await companion._send_variant(
                    self, variant=variant, source=source, video_path=video_path,
                    title=title, performer=performer, chat_id=chat_id,
                    reply_to=reply_to, thumbnail=thumbnail,
                    video_file_id=video_file_id, reference_duration=video_duration,
                )
                sent += int(bool(ok))
            except Exception as exc:
                failures.append(f"{companion._VARIANT_LABELS[variant]}: {str(exc)[:180]}")
                logger.exception("[LiveDubAudioQuality] %s failed: %s", variant, exc)

        expected = 2 if dual else 1
        if sent == expected:
            return True
        detail = "; ".join(failures) or "неизвестная ошибка"
        if sent:
            raise RuntimeError(f"отправлен неполный комплект MP3 ({sent}/{expected}); {detail}")
        raise RuntimeError(f"комплект MP3 не отправлен (0/{expected}); {detail}")

    send_complete._mp3bot_complete_dual_delivery = True  # type: ignore[attr-defined]
    companion._send_new_audio = send_complete


def install_livedub_audio_quality_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_clean_track_selection()
        _install_conversion_postcondition()
        _install_complete_dual_delivery()
        _INSTALLED = True
        logger.info("🎧 LiveDub audio quality guard: clean RU + strict 2/2 + valid conversion")
