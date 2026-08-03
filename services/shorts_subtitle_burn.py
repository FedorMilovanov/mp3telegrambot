#!/usr/bin/env python3
"""Transactional ASS subtitle burn for public Shorts-family video outputs.

The active burn path owns no independent child-process policy. FFmpeg lifetime,
timeout, stdin, cancellation and reap semantics are delegated to the shared
``run_cancellable_process`` contract so every public video renderer follows the
same process-ownership rules.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from services.async_process import run_cancellable_process
from services.async_worker import await_owned_coroutine
from services.ffmpeg import _get_video_encoder
from services.shorts_video import (
    _generate_ass_from_segments,
    get_subtitles_mode_settings,
)

logger = logging.getLogger(__name__)

_BURN_TIMEOUT_SECONDS = 600.0


async def _run_burn_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one encoded burn while retaining the shared GPU semaphore."""
    from core.resource_scheduler import scheduler as resource_scheduler

    async with resource_scheduler.gpu_render:
        return await run_cancellable_process(
            command,
            timeout=_BURN_TIMEOUT_SECONDS,
            text=True,
        )


def _remove_partial_output(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "Subtitle burn could not remove partial output %s: %s",
            path,
            exc,
        )


async def burn_subtitles_into_short(
    input_path: Path,
    output_path: Path,
    segments: list[dict],
) -> bool:
    """Burn ASS subtitles and commit only a non-empty finished output.

    The source file is never modified. The output is removed before each run
    and again on every failure, so callers cannot accidentally deliver a stale
    or partially encoded artifact from an earlier attempt.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not input_path.is_file() or not segments:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_partial_output(output_path)

    try:
        subtitle_config = get_subtitles_mode_settings()
        ass_content = _generate_ass_from_segments(
            segments,
            karaoke=bool(subtitle_config["karaoke"]),
        )
        if not ass_content.strip():
            logger.warning("Subtitle burn rejected an empty ASS document")
            return False

        with tempfile.TemporaryDirectory(prefix="short-subtitles-") as temp_dir:
            ass_path = Path(temp_dir) / "subtitles.ass"
            ass_path.write_text(ass_content, encoding="utf-8")
            ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")

            encoder, quality, preset = await await_owned_coroutine(
                asyncio.to_thread(_get_video_encoder)
            )
            command = [
                ffmpeg,
                "-i",
                str(input_path),
                "-vf",
                f"subtitles='{ass_escaped}'",
                "-c:v",
                encoder,
                *preset,
                *quality,
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
            process = await _run_burn_command(command)

        if process.returncode != 0:
            logger.warning(
                "burn_subtitles ffmpeg error: %s",
                (process.stderr or "")[-500:],
            )
            _remove_partial_output(output_path)
            return False
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            logger.warning("Subtitle burn produced no non-empty output")
            _remove_partial_output(output_path)
            return False

        logger.info(
            "Subtitles burned transactionally: %s (%.1fMB)",
            output_path.name,
            output_path.stat().st_size / 1024 / 1024,
        )
        return True
    except asyncio.CancelledError:
        _remove_partial_output(output_path)
        raise
    except subprocess.TimeoutExpired:
        logger.warning("burn_subtitles_into_short: ffmpeg timeout")
        _remove_partial_output(output_path)
        return False
    except Exception as exc:
        logger.warning(
            "burn_subtitles_into_short error: %s: %s",
            type(exc).__name__,
            exc,
        )
        _remove_partial_output(output_path)
        return False


__all__ = ["burn_subtitles_into_short"]
