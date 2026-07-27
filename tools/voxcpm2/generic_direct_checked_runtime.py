#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checked direct-SRT entrypoint with strict timing and verbatim TTS text."""
from __future__ import annotations

from typing import Any

from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_direct_runtime as production
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import semantic_tts_guard as legacy_semantic_guard
from tools.voxcpm2 import semantic_tts_guard_v4

_MIN_QA_WINDOW = 0.35


def build_direct_segments_safe(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    delay = max(0, int(delay_ms)) / 1000.0
    segments: list[dict[str, Any]] = []
    subtitles: list[pipeline.Cue] = []

    for index, group in enumerate(groups, start=1):
        original_start = max(0.0, float(group["start"]))
        source_end = min(float(duration), float(group["end"]))
        if original_start >= duration or source_end <= original_start:
            raise RuntimeError(f"Реплика #{index} не имеет окна внутри видео.")

        # A sub-300 ms final cue cannot pass acoustic/semantic QA. Expand only
        # its technical window backwards; the approved words and order stay intact.
        start = original_start
        if source_end - start < _MIN_QA_WINDOW:
            start = max(0.0, source_end - _MIN_QA_WINDOW)

        slot = source_end - start
        # Preserve 420 ms whenever possible. Near the end reduce only the delay
        # and spend every available millisecond on the approved speech.
        effective_delay = min(delay, max(0.0, slot - _MIN_QA_WINDOW))
        effective_delay_ms = int(round(effective_delay * 1000.0))
        render_end = min(source_end - effective_delay, duration)
        if render_end <= start:
            render_end = min(source_end, duration)
        if render_end <= start:
            raise RuntimeError(f"Реплика #{index} не помещается до конца видео.")

        profile = "composite" if index == len(groups) or index % 4 == 0 else "extended"
        text = str(group["source"]).strip()
        segments.append(
            {
                "id": index,
                "start": round(start, 3),
                "end": round(render_end, 3),
                "start_delay_ms": effective_delay_ms,
                "reference_profile": profile,
                "tail_guard": 0.36 if profile == "extended" else 0.42,
                "text": text,
                "source_end": round(source_end, 3),
                "source": text,
                "text_policy": "verbatim_user_srt",
                "original_srt_start": round(original_start, 3),
                "timing_window_expanded": start < original_start,
            }
        )
        subtitle_start = min(duration, start + effective_delay)
        subtitles.append(pipeline.Cue(subtitle_start, source_end, text))

    return segments, subtitles


def preserve_user_tts_text(value: str) -> str:
    """Do not edit punctuation or wording from the user's final SRT."""
    return str(value or "").strip()


def main() -> None:
    # Disable only the obsolete prompt-continuation installer. Quality v4 still
    # verifies the generated speech with Whisper and retries failed segments.
    legacy_semantic_guard.install = lambda: None
    legacy_semantic_guard.sanitize_tts_text = preserve_user_tts_text
    dub_quality_v4.install_direct_quality(production, pipeline)
    semantic_tts_guard_v4.install()
    production._build_direct_segments = build_direct_segments_safe
    production.main()


if __name__ == "__main__":
    main()
