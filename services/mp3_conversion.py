#!/usr/bin/env python3
"""Verified atomic MP3 re-encoding for delivery/cache variants."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from services.async_process import run_cancellable_process

logger = logging.getLogger(__name__)


async def _probe_audio_file(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    path = Path(path)
    if not ffprobe or not path.is_file() or path.stat().st_size <= 10 * 1024:
        return False
    process = await run_cancellable_process(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        timeout=45,
        text=True,
    )
    if process.returncode != 0:
        return False
    try:
        payload = json.loads(process.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    has_audio = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in payload.get("streams") or []
    )
    return bool(has_audio and duration > 0.0)


async def reencode_mp3_atomic(
    source_path: Path,
    output_path: Path,
    *,
    bitrate_kbps: int = 64,
    timeout: float = 300.0,
) -> bool:
    """Create a proved MP3 and atomically publish it without risking the source."""
    source = Path(source_path)
    output = Path(output_path)
    if not source.is_file() or source.stat().st_size <= 0:
        return False
    try:
        if source.resolve(strict=False) == output.resolve(strict=False):
            raise ValueError("MP3 source and output paths must differ")
    except OSError:
        if source.absolute() == output.absolute():
            raise ValueError("MP3 source and output paths must differ")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    bitrate = max(32, min(int(bitrate_kbps), 320))
    output.parent.mkdir(parents=True, exist_ok=True)

    if (
        output.is_file()
        and output.stat().st_mtime >= source.stat().st_mtime
        and await _probe_audio_file(output)
    ):
        return True

    temp = output.with_name(
        f"{output.stem}.part-{os.getpid()}-{uuid.uuid4().hex[:8]}.mp3"
    )
    try:
        temp.unlink(missing_ok=True)
        process = await run_cancellable_process(
            [
                ffmpeg,
                "-i",
                str(source),
                "-b:a",
                f"{bitrate}k",
                "-y",
                str(temp),
            ],
            timeout=timeout,
            text=True,
        )
        if process.returncode != 0:
            logger.warning(
                "MP3 %dk conversion failed rc=%s source=%s: %s",
                bitrate,
                process.returncode,
                source.name,
                str(process.stderr or "")[-500:],
            )
            return False
        if not await _probe_audio_file(temp):
            logger.warning(
                "MP3 %dk conversion rejected by audio probe: source=%s",
                bitrate,
                source.name,
            )
            return False
        os.replace(temp, output)
        return True
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError) as exc:
        logger.warning("MP3 %dk conversion transaction failed: %s", bitrate, exc)
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


async def reencode_mp3_64k_atomic(source_path: Path, output_path: Path) -> bool:
    return await reencode_mp3_atomic(
        source_path,
        output_path,
        bitrate_kbps=64,
        timeout=300.0,
    )


__all__ = ["reencode_mp3_64k_atomic", "reencode_mp3_atomic"]
