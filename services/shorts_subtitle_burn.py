#!/usr/bin/env python3
"""Transactional ASS subtitle burn for public Shorts-family video outputs.

The legacy implementation created a delete=False temporary ``.ass`` file and
removed it only after FFmpeg returned normally. A timeout or exception could
therefore leak the ASS file and leave a partial output MP4 behind. This module
owns the active publication path and scopes every temporary artifact to a
``TemporaryDirectory`` that is removed on success, rejection, timeout and
cancellation.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from services.ffmpeg import _get_video_encoder
from services.shorts_video import (
    _generate_ass_from_segments,
    get_subtitles_mode_settings,
)

logger = logging.getLogger(__name__)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Stop FFmpeg and wait until it no longer owns temp/output files."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5.0)
        return
    except (ProcessLookupError, asyncio.TimeoutError):
        pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def _run_burn_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one encoded burn under the shared GPU semaphore.

    An asyncio-owned child process is used instead of ``run_in_executor`` so a
    task cancellation can terminate FFmpeg before the ASS directory and partial
    output are cleaned. This is especially important on Windows, where an open
    file handle can otherwise make cleanup fail.
    """
    from core.resource_scheduler import scheduler as resource_scheduler

    async with resource_scheduler.gpu_render:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=600.0,
            )
        except asyncio.TimeoutError as exc:
            await _terminate_process(process)
            raise subprocess.TimeoutExpired(command, timeout=600) from exc
        except asyncio.CancelledError:
            await _terminate_process(process)
            raise

    return subprocess.CompletedProcess(
        command,
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
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

            encoder, quality, preset = _get_video_encoder()
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
