"""Conservative static-slide detector for Shorts visual-mode selection.

Moving footage keeps the established ``crop_zoom`` default. Only a confidently
static or near-static slide is reclassified to the existing centred
``full_frame_vertical``/blur renderer. The detector downsamples the central
frame area before measuring motion: this suppresses codec noise, film grain and
small decorative particles without mistaking ordinary talking-head movement for
a still image.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Iterable

logger = logging.getLogger(__name__)

_INSTALLED = False
_CACHE: dict[tuple[str, int, int, int, int, int], bool] = {}
_CACHE_LIMIT = 256

_YDIF_RE = re.compile(r"lavfi\.signalstats\.YDIF\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)")
_FREEZE_START_RE = re.compile(r"freeze_start:\s*([0-9]+(?:\.[0-9]+)?)")
_FREEZE_DURATION_RE = re.compile(r"freeze_duration:\s*([0-9]+(?:\.[0-9]+)?)")


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(value, high))


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _percentile(values: Iterable[float], q: float) -> float:
    data: list[float] = []
    for raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            data.append(value)
    data.sort()
    if not data:
        return math.inf
    if len(data) == 1:
        return data[0]
    pos = max(0.0, min(1.0, q)) * (len(data) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return data[lo]
    weight = pos - lo
    return data[lo] * (1.0 - weight) + data[hi] * weight


def _parse_ydif_values(output: str) -> list[float]:
    values: list[float] = []
    for raw in _YDIF_RE.findall(output or ""):
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _freeze_coverage(output: str, probe_seconds: float) -> float:
    """Return the fraction of the probe covered by freeze intervals.

    ``setpts=PTS-STARTPTS`` in the probe makes freeze timestamps relative to the
    sampled window. A trailing open freeze is counted only from its actual
    ``freeze_start``; a freeze beginning in the final frames therefore cannot be
    mistaken for a fully static clip.
    """
    probe = max(0.001, float(probe_seconds))
    total = 0.0
    open_start: float | None = None

    for line in (output or "").splitlines():
        start_match = _FREEZE_START_RE.search(line)
        if start_match:
            try:
                open_start = max(0.0, min(float(start_match.group(1)), probe))
            except ValueError:
                open_start = None

        duration_match = _FREEZE_DURATION_RE.search(line)
        if duration_match:
            try:
                duration = max(0.0, float(duration_match.group(1)))
            except ValueError:
                duration = 0.0
            total += min(duration, probe)
            open_start = None

    if open_start is not None:
        total += max(0.0, probe - open_start)

    return max(0.0, min(total / probe, 1.0))


def _classify_static_metrics(
    *,
    freeze_ratio: float,
    ydif_values: Iterable[float],
    probe_seconds: float,
) -> tuple[bool, dict[str, float | int | str]]:
    values: list[float] = []
    for raw in ydif_values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)

    med = median(values) if values else math.inf
    p90 = _percentile(values, 0.90)
    p98 = _percentile(values, 0.98)

    freeze_min = _env_float("SHORTS_STATIC_FREEZE_RATIO_MIN", 0.86, 0.50, 0.99)
    median_max = _env_float("SHORTS_STATIC_YDIF_MEDIAN_MAX", 0.55, 0.05, 5.0)
    p90_max = _env_float("SHORTS_STATIC_YDIF_P90_MAX", 1.60, 0.10, 10.0)
    p98_max = _env_float("SHORTS_STATIC_YDIF_P98_MAX", 3.50, 0.20, 20.0)
    ultra_median = _env_float("SHORTS_STATIC_ULTRA_MEDIAN_MAX", 0.16, 0.01, 2.0)
    ultra_p90 = _env_float("SHORTS_STATIC_ULTRA_P90_MAX", 0.45, 0.02, 4.0)

    # At 3 fps an 8-second probe normally yields about 23 differences. Require
    # enough evidence; an ffmpeg/metadata failure must preserve crop_zoom.
    min_samples = max(6, int(max(4.0, probe_seconds) * 1.5))
    enough_samples = len(values) >= min_samples

    frozen_and_quiet = (
        enough_samples
        and freeze_ratio >= freeze_min
        and med <= median_max
        and p90 <= p90_max
        and p98 <= p98_max
    )
    ultra_still = enough_samples and med <= ultra_median and p90 <= ultra_p90
    is_static = bool(frozen_and_quiet or ultra_still)

    reason = "moving/default-crop"
    if frozen_and_quiet:
        reason = "dominant-freeze+low-motion"
    elif ultra_still:
        reason = "ultra-low-motion"
    elif not enough_samples:
        reason = "insufficient-motion-samples"

    metrics: dict[str, float | int | str] = {
        "freeze_ratio": round(float(freeze_ratio), 4),
        "ydif_median": round(float(med), 4) if math.isfinite(med) else "inf",
        "ydif_p90": round(float(p90), 4) if math.isfinite(p90) else "inf",
        "ydif_p98": round(float(p98), 4) if math.isfinite(p98) else "inf",
        "samples": len(values),
        "reason": reason,
    }
    return is_static, metrics


def _probe_filter(noise_db: float, freeze_seconds: float) -> str:
    # Central crop makes real face/mouth/gesture movement occupy a larger share of
    # the analysed pixels. Area downscaling removes codec noise, film grain and
    # tiny decorative particles from cover art before motion measurement.
    # yuv420p keeps signalstats on a universally supported pixel format; YDIF is
    # still a luma-only motion metric.
    return (
        "fps=3,"
        "crop=trunc(iw*0.56/2)*2:trunc(ih*0.70/2)*2:(iw-ow)/2:(ih-oh)/2,"
        "scale=96:96:flags=area,format=yuv420p,setpts=PTS-STARTPTS,"
        f"freezedetect=n={noise_db:.1f}dB:d={freeze_seconds:.2f},"
        "signalstats,metadata=mode=print"
    )


def _cache_key(
    video_path: Path,
    sample_start: float,
    probe_seconds: float,
    second_probe_offset: float,
) -> tuple[str, int, int, int, int, int]:
    try:
        stat = video_path.stat()
        size = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
    except OSError:
        size = 0
        mtime_ns = 0
    return (
        str(video_path.resolve()),
        size,
        mtime_ns,
        int(round(max(0.0, sample_start) * 10)),
        int(round(probe_seconds * 10)),
        int(round(second_probe_offset * 10)),
    )


async def _run_motion_probe(
    *,
    ffmpeg: str,
    video_path: Path,
    start: float,
    probe_seconds: float,
    noise_db: float,
    freeze_seconds: float,
) -> tuple[bool, dict[str, float | int | str]]:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{probe_seconds:.3f}",
        "-vf",
        _probe_filter(noise_db, freeze_seconds),
        "-an",
        "-f",
        "null",
        "-",
    ]

    proc = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        ),
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg rc={proc.returncode}: {(proc.stderr or '')[-240:]}")

    return _classify_static_metrics(
        freeze_ratio=_freeze_coverage(output, probe_seconds),
        ydif_values=_parse_ydif_values(output),
        probe_seconds=probe_seconds,
    )


def _log_probe(start: float, probe: float, is_static: bool, metrics: dict[str, float | int | str]) -> None:
    logger.info(
        "Short visual probe: %s at %.1fs probe=%.1fs freeze=%s "
        "YDIF median=%s p90=%s p98=%s samples=%s reason=%s",
        "static" if is_static else "moving/uncertain",
        start,
        probe,
        metrics["freeze_ratio"],
        metrics["ydif_median"],
        metrics["ydif_p90"],
        metrics["ydif_p98"],
        metrics["samples"],
        metrics["reason"],
    )


async def _is_static_video_confident(
    video_path: Path,
    sample_start: float = 0.0,
    probe_seconds: float = 8.0,
) -> bool:
    """Classify a Shorts source window conservatively.

    ``False`` is the fail-safe result: moving footage and uncertain/error cases
    keep the existing crop_zoom behavior. ``True`` is returned only when two
    separated low-resolution motion probes both support a static slide. The
    second probe prevents a long opening title card from making an otherwise
    moving talking-head clip use blur for its entire duration.
    """
    if not _env_enabled("SHORTS_STATIC_BLUR_AUTO", True):
        return False

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path.exists():
        return False

    probe = _env_float("SHORTS_STATIC_PROBE_SECONDS", probe_seconds, 4.0, 15.0)
    start_offset = _env_float("SHORTS_STATIC_PROBE_OFFSET", 0.75, 0.0, 3.0)
    second_offset = _env_float("SHORTS_STATIC_SECOND_PROBE_OFFSET", 12.0, 6.0, 45.0)
    noise_db = _env_float("SHORTS_STATIC_FREEZE_NOISE_DB", -50.0, -70.0, -30.0)
    freeze_seconds = _env_float("SHORTS_STATIC_FREEZE_SECONDS", 1.5, 0.5, 4.0)
    first_start = max(0.0, float(sample_start) + start_offset)

    key = _cache_key(video_path, first_start, probe, second_offset)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    starts = [first_start]
    if _env_enabled("SHORTS_STATIC_MULTI_PROBE", True):
        starts.append(first_start + second_offset)

    decisions: list[bool] = []
    try:
        for start in starts:
            is_static, metrics = await _run_motion_probe(
                ffmpeg=ffmpeg,
                video_path=video_path,
                start=start,
                probe_seconds=probe,
                noise_db=noise_db,
                freeze_seconds=freeze_seconds,
            )
            decisions.append(is_static)
            _log_probe(start, probe, is_static, metrics)
    except Exception as exc:
        logger.warning("Short static detector failed safe to crop: %s", exc)
        result = False
    else:
        result = bool(decisions) and all(decisions)
        logger.info(
            "Short visual classifier: %s (probes=%s)",
            "static→full_frame_blur" if result else "moving/uncertain→crop_zoom",
            decisions,
        )

    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def install_short_static_runtime() -> str:
    """Install before shorts_video/montage import; patch old imports if present."""
    global _INSTALLED
    if _INSTALLED:
        return "moving=crop_zoom; confident static slide=full_frame_blur"

    from services import ffmpeg as ffmpeg_module

    ffmpeg_module._is_static_video = _is_static_video_confident
    for module_name in ("services.shorts_video", "services.render_clips_montage"):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(module, "_is_static_video", _is_static_video_confident)

    _INSTALLED = True
    return "moving=crop_zoom; 2-probe static slide=full_frame_blur; errors keep crop"
