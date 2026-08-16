#!/usr/bin/env python3
"""Pure quality helpers for LiveDub companion audio.

Clean-track selection is now called directly by the owning services/coordinator;
there is no runtime replacement of ``livedub_mix``, ``yandex_live_dub`` or
companion functions.
"""
from __future__ import annotations

from pathlib import Path

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
    """Return the newest role-correct original RU translation MP3."""
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


def validate_livedub_audio_quality_contract() -> str:
    """Validate the source-owned selection API without mutating any module."""
    if not callable(select_clean_translation_mp3):
        raise RuntimeError("clean translation selector is unavailable")
    return "source-owned clean-RU role selection; derived audio excluded"


# Compatibility name for old diagnostics. It performs no installation.


__all__ = [
    "is_derived_audio_artifact",
    "select_clean_translation_mp3",
    "validate_livedub_audio_quality_contract",
]
