#!/usr/bin/env python3
"""Strict media postconditions for the shared Shorts Factory source."""
from __future__ import annotations

from pathlib import Path

from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async


async def validated_factory_source_duration(
    source_path: Path,
    expected_duration: int,
) -> float:
    """Return exact ffprobe duration and reject truncated video/audio sources."""
    probe = await probe_media_async(source_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("Общий Factory-источник не прошёл media probe (нужны video+audio)")
    assert probe is not None
    exact_duration = float(probe.duration)
    if exact_duration + 3.0 < float(expected_duration):
        raise RuntimeError(
            "Общий Factory-источник обрезан: "
            f"ожидалось около {expected_duration:.0f}с, получено {exact_duration:.3f}с"
        )
    return exact_duration


__all__ = ["validated_factory_source_duration"]
