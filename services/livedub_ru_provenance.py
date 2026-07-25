#!/usr/bin/env python3
"""Record and consume the exact VOT Russian-audio artifact.

The LiveDub work directory contains several MP3 roles: the VOT translation,
original-audio helpers, QA extracts and generated final mixes. Selecting the
"newest non-derived MP3" is a useful legacy fallback, but it is not proof that the
file is the Russian translation returned by VOT.

This adapter records the exact successful ``get_live_dub_audio`` result in an
atomic marker and makes ``find_pro_tracks`` prefer that validated path. The marker
contains only a basename plus immutable-at-selection metadata; absolute paths,
subdirectories, derived artifacts and changed files are rejected. If provenance is
missing or invalid, the existing selector remains the compatibility fallback.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False
_MARKER_NAME = ".livedub_ru_audio.json"
_SCHEMA_VERSION = 1


def _marker_path(workdir: Path | str) -> Path:
    return Path(workdir) / _MARKER_NAME


def _safe_basename(value: Any) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return ""
    if Path(name).name != name:
        return ""
    return name


def _is_derived(path: Path) -> bool:
    try:
        from services.livedub_audio_quality_guard import is_derived_audio_artifact

        return bool(is_derived_audio_artifact(path))
    except Exception:
        return False


def write_ru_audio_provenance(path: Path | str, *, voice_style: str = "") -> bool:
    """Atomically record one exact MP3 returned by VOT.

    Provenance is advisory: failure to persist it must never destroy a successfully
    downloaded translation. Consumers fall back to the established selector.
    """
    candidate = Path(path)
    temp: Path | None = None
    try:
        if not candidate.is_file() or candidate.suffix.casefold() != ".mp3":
            return False
        if _is_derived(candidate):
            return False
        stat = candidate.stat()
        if stat.st_size <= 1024:
            return False
        parent = candidate.parent
        marker = _marker_path(parent)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "filename": candidate.name,
            "size_bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "voice_style": str(voice_style or "")[:24],
        }
        temp = marker.with_name(
            f"{marker.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
        )
        parent.mkdir(parents=True, exist_ok=True)
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, marker)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("[LiveDubProvenance] marker write failed for %s: %s", candidate, exc)
        return False
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def read_ru_audio_provenance(workdir: Path | str) -> Path | None:
    """Return the exact unchanged VOT MP3 named by a safe marker."""
    root = Path(workdir)
    marker = _marker_path(root)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("schema_version") or 0) != _SCHEMA_VERSION:
            return None
        filename = _safe_basename(payload.get("filename"))
        if not filename or Path(filename).suffix.casefold() != ".mp3":
            return None
        candidate = root / filename
        if not candidate.is_file() or _is_derived(candidate):
            return None
        if candidate.parent.resolve() != root.resolve():
            return None
        stat = candidate.stat()
        if stat.st_size <= 1024:
            return None
        if int(payload.get("size_bytes") or -1) != int(stat.st_size):
            return None
        if int(payload.get("mtime_ns") or -1) != int(stat.st_mtime_ns):
            return None
        return candidate
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _voice_style(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    if kwargs.get("voice_style") is not None:
        return str(kwargs.get("voice_style") or "")
    # get_live_dub_audio(video_url, output_dir, timeout, retries, voice_style, ...)
    if len(args) > 4:
        return str(args[4] or "")
    return "live"


def _install_vot_recorder() -> None:
    from services import yandex_live_dub as yandex

    current = yandex.get_live_dub_audio
    if getattr(current, "_mp3bot_ru_provenance", False):
        return

    @functools.wraps(current)
    async def recorded(*args: Any, **kwargs: Any):
        result = await current(*args, **kwargs)
        try:
            if write_ru_audio_provenance(Path(result), voice_style=_voice_style(args, kwargs)):
                logger.info("[LiveDubProvenance] exact VOT RU source recorded: %s", Path(result).name)
        except Exception as exc:
            logger.warning("[LiveDubProvenance] recorder skipped: %s", str(exc)[:180])
        return result

    recorded._mp3bot_ru_provenance = True  # type: ignore[attr-defined]
    yandex.get_live_dub_audio = recorded


def _install_track_reader() -> None:
    from services import livedub_mix as mix

    current = mix.find_pro_tracks
    if getattr(current, "_mp3bot_ru_provenance", False):
        return

    @functools.wraps(current)
    def exact_tracks(workdir: Path):
        original, fallback = current(workdir)
        exact = read_ru_audio_provenance(workdir)
        if exact is not None:
            logger.info("[LiveDubProvenance] exact RU source selected: %s", exact.name)
            return original, exact
        return original, fallback

    exact_tracks._mp3bot_ru_provenance = True  # type: ignore[attr-defined]
    mix.find_pro_tracks = exact_tracks


def install_livedub_ru_provenance() -> None:
    """Install after clean-track guards and before delivery captures track lookup."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_vot_recorder()
        _install_track_reader()
        _INSTALLED = True
        logger.info("🧬 LiveDub RU provenance: exact VOT source marker enabled")
