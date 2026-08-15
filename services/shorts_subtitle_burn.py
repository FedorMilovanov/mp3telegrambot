#!/usr/bin/env python3
"""Transactional, validated ASS subtitle burn for public Shorts outputs."""
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
from services.shorts_subtitle_integrity import (
    generate_ass_from_segments as _validated_generate_ass_from_segments,
    validate_ass_document,
)
from services.shorts_video import get_subtitles_mode_settings

logger = logging.getLogger(__name__)

_BURN_TIMEOUT_SECONDS = 600.0
_generate_ass_from_segments = _validated_generate_ass_from_segments


async def _run_burn_command(command: list[str]) -> subprocess.CompletedProcess[str]:
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
    *,
    karaoke: bool | None = None,
) -> bool:
    """Validate, burn and commit one subtitle output with explicit style support."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not input_path.is_file() or not segments:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_partial_output(output_path)

    try:
        if karaoke is None:
            subtitle_config = get_subtitles_mode_settings()
            karaoke_enabled = bool(subtitle_config["karaoke"])
        else:
            karaoke_enabled = bool(karaoke)
        try:
            ass_content = _generate_ass_from_segments(
                segments,
                karaoke=karaoke_enabled,
            )
        except (TypeError, ValueError) as exc:
            logger.warning("Subtitle burn rejected transcript timing: %s", exc)
            return False

        if not ass_content.strip():
            logger.warning("Subtitle burn rejected an empty ASS document")
            return False
        validation_issues = validate_ass_document(
            ass_content,
            karaoke=karaoke_enabled,
        )
        if validation_issues:
            logger.warning(
                "Subtitle burn rejected invalid ASS: %s",
                "; ".join(validation_issues[:8]),
            )
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
            "Subtitles burned transactionally after ASS validation: %s (%.1fMB, karaoke=%s)",
            output_path.name,
            output_path.stat().st_size / 1024 / 1024,
            karaoke_enabled,
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
