#!/usr/bin/env python3
"""Cross-cutting delivery hardening installed before ``main`` is imported.

This module fixes issues at their ownership boundaries instead of globally
monkey-patching ``subprocess``:

* yt-dlp JS runtimes are emitted as repeated options, never as ``deno,node``;
* every patched ffprobe call decodes UTF-8 defensively on Windows;
* LiveDub QA auto-fix covers every major timestamp (no silent first-six limit);
* rebuilt media is duration-validated before it can replace the original result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


def _run_text(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


def normalise_js_runtime_args(args: Iterable[str]) -> list[str]:
    """Expand yt-dlp ``--js-runtimes deno,node`` into repeated options.

    yt-dlp accepts one runtime specification per option occurrence. Keeping this
    as a pure function makes the startup rewrite deterministic and testable.
    """
    src = [str(item) for item in args]
    out: list[str] = []
    runtimes: list[str] = []
    index = 0
    while index < len(src):
        token = src[index]
        if token == "--js-runtimes":
            if index + 1 < len(src):
                runtimes.extend(part.strip() for part in src[index + 1].split(","))
                index += 2
                continue
            index += 1
            continue
        if token.startswith("--js-runtimes="):
            runtimes.extend(part.strip() for part in token.split("=", 1)[1].split(","))
            index += 1
            continue
        out.append(token)
        index += 1

    unique: list[str] = []
    for runtime in runtimes:
        if runtime and runtime not in unique:
            unique.append(runtime)
    for runtime in unique:
        out.extend(("--js-runtimes", runtime))
    return out


def _install_ytdlp_runtime_args() -> None:
    import services.ffmpeg as ffmpeg_helpers

    current = list(ffmpeg_helpers.YTDLP_BASE_ARGS)
    fixed = normalise_js_runtime_args(current)
    if fixed != current:
        # Mutate in place: modules that imported the list object by value keep the
        # corrected contents too.
        ffmpeg_helpers.YTDLP_BASE_ARGS[:] = fixed
        logger.info("🧩 yt-dlp JS runtimes normalised as repeated options")


def _safe_probe_video_meta(path: Path) -> dict[str, int | None]:
    ffprobe = shutil.which("ffprobe")
    meta: dict[str, int | None] = {"width": None, "height": None, "duration": None}
    if not ffprobe:
        return meta
    try:
        proc = _run_text(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json",
                str(path),
            ],
            timeout=60,
        )
        if proc.returncode != 0:
            logger.warning(
                "[LiveDubMix] ffprobe meta rc=%s: %s",
                proc.returncode,
                (proc.stderr or "")[-300:],
            )
            return meta
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if streams:
            meta["width"] = streams[0].get("width")
            meta["height"] = streams[0].get("height")
        duration = (data.get("format") or {}).get("duration")
        if duration:
            meta["duration"] = int(round(float(duration)))
    except Exception as exc:
        logger.warning("[LiveDubMix] safe probe_video_meta failed: %s", str(exc)[:180])
    return meta


def _safe_audio_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        proc = _run_text(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout=30,
        )
        if proc.returncode != 0:
            return 0.0
        return max(0.0, float((proc.stdout or "").strip() or "0"))
    except Exception:
        return 0.0


def _duration_matches(expected: float | None, actual: float | None) -> bool:
    if not expected or not actual:
        return False
    tolerance = max(3.0, float(expected) * 0.015)
    return abs(float(expected) - float(actual)) <= tolerance


def _install_utf8_probes() -> None:
    import services.livedub_mix as mix

    _safe_probe_video_meta._mp3bot_delivery_hardening = True  # type: ignore[attr-defined]
    mix.probe_video_meta = _safe_probe_video_meta

    try:
        import services.eng_subtitles as eng_subtitles

        eng_subtitles._get_audio_duration = _safe_audio_duration
    except Exception as exc:
        logger.info("[LiveDubHardening] eng_subtitles probe patch skipped: %s", str(exc)[:160])


def _major_issue_times(issues: Iterable[dict[str, Any]], parse_time) -> list[float]:
    times: list[float] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("severity") or "").strip().casefold() != "major":
            continue
        parsed = parse_time(str(issue.get("time") or ""))
        if parsed is not None:
            times.append(float(parsed))
    return sorted(set(times))


def build_major_fix_intervals(
    issues: Iterable[dict[str, Any]],
    *,
    parse_time,
    delay_s: float,
    pre_s: float = 0.5,
    window_s: float = 6.0,
    max_intervals: int = 0,
) -> list[tuple[float, float]]:
    """Build merged intervals covering every valid major issue timestamp.

    ``max_intervals=0`` means unlimited. A positive limit raises instead of
    silently truncating, so a result can never be presented as fully fixed when
    some major timestamps were omitted.
    """
    raw = [
        (max(0.0, moment - pre_s), moment - pre_s + window_s + max(0.0, delay_s))
        for moment in _major_issue_times(issues, parse_time)
    ]
    merged: list[tuple[float, float]] = []
    for start, end in raw:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if max_intervals > 0 and len(merged) > max_intervals:
        raise RuntimeError(
            f"QA produced {len(merged)} independent major intervals, above the configured "
            f"safe limit {max_intervals}; refusing a partial auto-fix"
        )
    return merged


def _intervals_cover_major_times(
    issues: Iterable[dict[str, Any]],
    intervals: list[tuple[float, float]],
    parse_time,
) -> bool:
    for moment in _major_issue_times(issues, parse_time):
        if not any(start <= moment <= end for start, end in intervals):
            return False
    return True


def _install_complete_autofix() -> None:
    import services.livedub_mix as mix

    def extract_fix_intervals(
        issues: list[dict],
        max_fixes: int | None = None,
    ) -> list[tuple[float, float]]:
        # Backward-compatible parameter name. None/0 is unlimited; an explicit
        # positive caller limit is honoured by raising rather than truncating.
        configured = os.getenv("LIVEDUB_AUTOFIX_MAX_INTERVALS", "0").strip()
        try:
            env_limit = max(0, int(configured or "0"))
        except ValueError:
            env_limit = 0
        explicit = int(max_fixes or 0)
        limit = explicit or env_limit
        intervals = build_major_fix_intervals(
            issues,
            parse_time=mix.parse_mmss,
            delay_s=mix.get_mix_params()["delay_ms"] / 1000.0,
            pre_s=float(getattr(mix, "_FIX_PRE", 0.5)),
            window_s=float(getattr(mix, "_FIX_LEN", 6.0)),
            max_intervals=limit,
        )
        if not _intervals_cover_major_times(issues, intervals, mix.parse_mmss):
            raise RuntimeError("internal QA coverage check failed")
        return intervals

    async def apply_qa_audio_fixes(workdir: Path, issues: list[dict]) -> Path | None:
        intervals = extract_fix_intervals(issues)
        if not intervals:
            return None
        orig_video, ru_audio = mix.find_pro_tracks(workdir)
        if not (orig_video and ru_audio and orig_video.exists() and ru_audio.exists()):
            logger.info("[LiveDubMix] auto-fix skipped: clean tracks were not retained")
            return None

        ru_expr = mix.build_interval_volume_expr(
            intervals,
            inside=float(getattr(mix, "_FIX_RU_GAIN", 0.0)),
        )
        en_expr = mix.build_interval_volume_expr(
            intervals,
            inside=float(getattr(mix, "_FIX_EN_BOOST", 2.2)),
        )
        out = Path(workdir) / "pro_dub_fixed.mp4"
        result = await mix.mix_tracks(
            orig_video,
            ru_audio,
            out,
            ru_extra_expr=ru_expr,
            en_extra_expr=en_expr,
        )
        if not result or not Path(result).is_file():
            return None

        expected = await asyncio.to_thread(mix.probe_media_duration, orig_video)
        actual = await asyncio.to_thread(mix.probe_media_duration, Path(result))
        if not _duration_matches(expected, actual):
            try:
                Path(result).unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                f"QA auto-fix duration validation failed: source={expected}, fixed={actual}"
            )
        if not mix.has_video_stream(Path(result)):
            try:
                Path(result).unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("QA auto-fix produced media without a video stream")

        logger.info(
            "[LiveDubMix] auto-fix validated: %d merged interval(s), all major timestamps covered",
            len(intervals),
        )

        # Preserve independently generated subtitles for short ENG Full videos.
        srt = Path(workdir) / "gemini_subs.srt"
        if srt.exists() and srt.stat().st_size > 50:
            try:
                from services.eng_subtitles import merge_subtitles

                with_subs = await merge_subtitles(Path(result), srt, is_fallback=False)
                if with_subs and Path(with_subs).exists():
                    sub_duration = await asyncio.to_thread(
                        mix.probe_media_duration, Path(with_subs)
                    )
                    if _duration_matches(expected, sub_duration):
                        logger.info("[LiveDubMix] auto-fix subtitles restored and validated")
                        return Path(with_subs)
                    logger.warning(
                        "[LiveDubMix] subtitle restore duration mismatch; keeping validated fixed video"
                    )
            except Exception as exc:
                logger.warning("[LiveDubMix] failed to restore subtitles: %s", str(exc)[:180])
        return Path(result)

    extract_fix_intervals._mp3bot_complete_autofix = True  # type: ignore[attr-defined]
    apply_qa_audio_fixes._mp3bot_complete_autofix = True  # type: ignore[attr-defined]
    mix.extract_fix_intervals = extract_fix_intervals
    mix.apply_qa_audio_fixes = apply_qa_audio_fixes


def install_livedub_delivery_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_ytdlp_runtime_args()
        _install_utf8_probes()
        _install_complete_autofix()
        _INSTALLED = True
        logger.info(
            "🛡 LiveDub delivery hardening: UTF-8 probes, valid yt-dlp runtimes, "
            "complete major-issue auto-fix enabled"
        )
