#!/usr/bin/env python3
"""Full-coverage translation QA for long LiveDub videos.

The legacy Quick-QA gate stopped at 120 seconds. Simply raising that number
would still be weak: one large request can miss the end of a lecture, while the
SRT helper is intentionally capped. This adapter checks long media in
chronological, overlapping segments and merges findings into global timestamps.
It reuses the existing QA prompt per segment; there is no second prompt stack.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return os.getenv("LIVEDUB_LONG_QA", "1").strip().lower() in _TRUE


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or str(default))
    except ValueError:
        value = default
    return max(low, min(value, high))


def segment_windows(duration: int, segment_sec: int = 480, overlap_sec: int = 20) -> list[tuple[int, int]]:
    """Return complete ``(start, length)`` coverage with boundary overlap."""
    total = max(0, int(duration or 0))
    segment = max(60, int(segment_sec or 480))
    overlap = max(0, min(int(overlap_sec or 0), segment // 3))
    if total <= 0:
        return []
    if total <= segment:
        return [(0, total)]
    step = max(1, segment - overlap)
    windows: list[tuple[int, int]] = []
    start = 0
    while start < total:
        length = min(segment, total - start)
        windows.append((start, length))
        if start + length >= total:
            break
        start += step
    return windows


def _ffprobe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        return float((proc.stdout or "0").strip() or 0) if proc.returncode == 0 else 0.0
    except Exception:
        return 0.0


def _extract_audio_segment(source: Path, start: int, length: int, output: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден для сегментной QA")
    temp = output.with_name(f".{output.stem}.{os.getpid()}.part.mp3")
    temp.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss", str(max(0, start)),
                "-t", str(max(1, length)),
                "-i", str(source),
                "-vn",
                "-map", "0:a:0",
                "-c:a", "libmp3lame",
                "-b:a", "64k",
                "-ac", "1",
                "-ar", "32000",
                str(temp),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(300, length * 3),
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg segment error")[-700:])
        measured = _ffprobe_duration(temp)
        if measured <= 0 or abs(measured - length) > max(4.0, length * 0.08):
            raise RuntimeError(f"сегмент не прошёл проверку длительности ({measured:.1f}с)")
        os.replace(temp, output)
        return output
    finally:
        temp.unlink(missing_ok=True)


def _parse_clock(value: str) -> float | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except ValueError:
        return None
    return None


def _format_clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _parse_srt_time(value: str) -> int | None:
    match = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        return None
    h, m, s, ms = (int(part) for part in match.groups())
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def _format_srt_time(milliseconds: int) -> str:
    total = max(0, int(milliseconds))
    hours, rem = divmod(total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def _slice_srt(source: Path, start: int, length: int, output: Path) -> Optional[Path]:
    try:
        raw = source.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    start_ms = max(0, start) * 1000
    end_ms = (max(0, start) + max(1, length)) * 1000
    blocks: list[str] = []
    index = 1
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        ts_idx = 1 if lines[0].strip().isdigit() else 0
        if ts_idx >= len(lines) or "-->" not in lines[ts_idx]:
            continue
        left, right = [part.strip() for part in lines[ts_idx].split("-->", 1)]
        begin = _parse_srt_time(left)
        finish = _parse_srt_time(right.split()[0])
        if begin is None or finish is None or finish <= start_ms or begin >= end_ms:
            continue
        rel_begin = max(0, begin - start_ms)
        rel_finish = min(end_ms - start_ms, finish - start_ms)
        text_lines = lines[ts_idx + 1:]
        if not text_lines:
            continue
        blocks.append(
            f"{index}\n{_format_srt_time(rel_begin)} --> {_format_srt_time(rel_finish)}\n"
            + "\n".join(text_lines)
        )
        index += 1
    if not blocks:
        return None
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return output


def _offset_issues(issues: Any, offset_sec: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in issues or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        seconds = _parse_clock(str(item.get("time") or ""))
        if seconds is not None:
            absolute = seconds + max(0, offset_sec)
            item["time"] = _format_clock(absolute)
            item["_absolute_seconds"] = absolute
        out.append(item)
    return out


def _issue_signature(issue: dict[str, Any]) -> str:
    text = " ".join(str(issue.get(key) or "") for key in ("problem", "heard", "should_be")).lower()
    return " ".join(re.findall(r"[a-zа-яё]{4,}", text)[:12])


def _merge_issues(results: list[tuple[int, int, dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for start, _length, result in results:
        for issue in _offset_issues(result.get("issues") or [], start):
            seconds = float(issue.get("_absolute_seconds") or -9999)
            signature = _issue_signature(issue)
            duplicate = False
            for existing in merged:
                existing_seconds = float(existing.get("_absolute_seconds") or -9999)
                existing_signature = _issue_signature(existing)
                if abs(seconds - existing_seconds) <= 20 and signature and existing_signature and (
                    signature in existing_signature or existing_signature in signature
                ):
                    duplicate = True
                    if str(issue.get("severity")) == "major":
                        existing["severity"] = "major"
                    break
            if not duplicate:
                merged.append(issue)
    merged.sort(
        key=lambda item: (
            0 if str(item.get("severity")) == "major" else 1,
            float(item.get("_absolute_seconds") or 10**9),
        )
    )
    for item in merged:
        item.pop("_absolute_seconds", None)
    return merged[:20]


def _coverage_seconds(intervals: list[tuple[int, int]]) -> int:
    ranges = sorted((start, start + length) for start, length in intervals if length > 0)
    if not ranges:
        return 0
    total = 0
    cur_start, cur_end = ranges[0]
    for start, end in ranges[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return total + cur_end - cur_start


def aggregate_segment_results(
    successful: list[tuple[int, int, dict[str, Any]]],
    total_windows: int,
    duration: int,
) -> dict[str, Any] | None:
    if not successful:
        return None
    weighted_sum = 0.0
    weighted_duration = 0
    for _start, length, segment_result in successful:
        score = segment_result.get("score")
        if isinstance(score, (int, float)) and math.isfinite(float(score)):
            weighted_sum += float(score) * max(1, length)
            weighted_duration += max(1, length)
    score = round(weighted_sum / weighted_duration) if weighted_duration else None
    issues = _merge_issues(successful)
    majors = sum(1 for item in issues if str(item.get("severity")) == "major")
    checked_seconds = _coverage_seconds([(start, length) for start, length, _ in successful])
    coverage = min(1.0, checked_seconds / max(1, duration))
    if issues:
        major_note = f", из них серьёзных — {majors}" if majors else ""
        verdict = f"Сегментная проверка всей записи выявила {len(issues)} неточностей{major_note}."
    else:
        verdict = "Сегментная проверка всей записи не выявила существенных искажений перевода."
    result: dict[str, Any] = {
        "reasoning": (
            f"Запись проверена последовательно по {len(successful)} из {total_windows} "
            "перекрывающихся сегментов; результаты сведены в общий хронометраж."
        ),
        "verdict": verdict,
        "issues": issues,
        "_segmented": True,
        "_segments_checked": len(successful),
        "_segments_total": total_windows,
        "_coverage_ratio": coverage,
    }
    if score is not None:
        result["score"] = score
    if len(successful) < total_windows or coverage < 0.9:
        result["_segmented_partial"] = True
    if any(segment_result.get("_low_confidence") for _, _, segment_result in successful):
        result["_low_confidence"] = True
    return result


async def _run_long_qa(
    original_run,
    *,
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str,
    dub_srt_path: Optional[Path],
    dub_audio_path: Optional[Path],
    existing_audio_part,
    existing_client,
    thinking_level: str,
) -> Optional[dict]:
    original_source = Path(original_audio_path) if original_audio_path and Path(original_audio_path).is_file() else None
    if original_source is None:
        logger.info("[LiveDubLongQA] локального оригинала нет — использую обычную полную QA")
        return await original_run(
            dub_video_path=dub_video_path,
            original_audio_path=original_audio_path,
            ai_data=ai_data,
            duration=duration,
            model_name=model_name,
            dub_srt_path=dub_srt_path,
            dub_audio_path=dub_audio_path,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
            thinking_level=thinking_level,
        )

    segment_sec = _env_int("LIVEDUB_LONG_QA_SEGMENT_SEC", 480, 180, 1200)
    overlap_sec = _env_int("LIVEDUB_LONG_QA_OVERLAP_SEC", 20, 0, 120)
    max_segments = _env_int("LIVEDUB_LONG_QA_MAX_SEGMENTS", 24, 1, 48)
    windows = segment_windows(duration, segment_sec, overlap_sec)[:max_segments]
    if not windows:
        return None

    long_model = os.getenv("LIVEDUB_LONG_QA_MODEL", "").strip()
    if not long_model:
        try:
            from core.database import GEMINI_MODEL

            long_model = GEMINI_MODEL
        except Exception:
            long_model = model_name
    long_thinking = os.getenv("LIVEDUB_LONG_QA_THINKING", "low").strip() or "low"
    russian_source = Path(dub_audio_path) if dub_audio_path and Path(dub_audio_path).is_file() else Path(dub_video_path)
    srt_source = Path(dub_srt_path) if dub_srt_path and Path(dub_srt_path).is_file() else None

    successful: list[tuple[int, int, dict[str, Any]]] = []
    with tempfile.TemporaryDirectory(prefix="mp3bot-lqa-") as temp_dir:
        root = Path(temp_dir)
        for index, (start, length) in enumerate(windows, start=1):
            try:
                logger.info(
                    "[LiveDubLongQA] segment %d/%d: %s–%s",
                    index,
                    len(windows),
                    _format_clock(start),
                    _format_clock(start + length),
                )
                orig_segment = await asyncio.to_thread(
                    _extract_audio_segment,
                    original_source,
                    start,
                    length,
                    root / f"original-{index:02d}.mp3",
                )
                segment_srt = None
                if srt_source is not None:
                    segment_srt = await asyncio.to_thread(
                        _slice_srt,
                        srt_source,
                        start,
                        length,
                        root / f"dub-{index:02d}.srt",
                    )
                ru_segment = None
                if segment_srt is None:
                    ru_segment = await asyncio.to_thread(
                        _extract_audio_segment,
                        russian_source,
                        start,
                        length,
                        root / f"dub-{index:02d}.mp3",
                    )
                segment_result = await original_run(
                    dub_video_path=ru_segment or Path(dub_video_path),
                    original_audio_path=orig_segment,
                    ai_data=None,
                    duration=length,
                    model_name=long_model or model_name,
                    dub_srt_path=segment_srt,
                    dub_audio_path=ru_segment,
                    existing_audio_part=None,
                    existing_client=None,
                    thinking_level=long_thinking,
                )
                if isinstance(segment_result, dict):
                    successful.append((start, length, segment_result))
                else:
                    logger.warning("[LiveDubLongQA] segment %d returned no result", index)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[LiveDubLongQA] segment %d failed: %s", index, str(exc)[:220])

    combined = aggregate_segment_results(successful, len(windows), duration)
    if combined is not None:
        logger.info(
            "[LiveDubLongQA] complete: segments=%d/%d coverage=%.0f%% issues=%d",
            combined.get("_segments_checked", 0),
            combined.get("_segments_total", 0),
            float(combined.get("_coverage_ratio") or 0) * 100,
            len(combined.get("issues") or []),
        )
        return combined

    logger.warning("[LiveDubLongQA] all segments failed — one full-request fallback")
    return await original_run(
        dub_video_path=dub_video_path,
        original_audio_path=original_audio_path,
        ai_data=ai_data,
        duration=duration,
        model_name=model_name,
        dub_srt_path=dub_srt_path,
        dub_audio_path=dub_audio_path,
        existing_audio_part=existing_audio_part,
        existing_client=existing_client,
        thinking_level=thinking_level,
    )



async def run_long_translation_qa(
    base_runner,
    *,
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str = "",
    dub_srt_path: Optional[Path] = None,
    dub_audio_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
    thinking_level: str = "high",
) -> Optional[dict]:
    """Use segmented complete coverage only when the recording crosses the threshold."""
    common = dict(
        dub_video_path=dub_video_path,
        original_audio_path=original_audio_path,
        ai_data=ai_data,
        duration=duration,
        model_name=model_name,
        dub_srt_path=dub_srt_path,
        dub_audio_path=dub_audio_path,
        existing_audio_part=existing_audio_part,
        existing_client=existing_client,
        thinking_level=thinking_level,
    )
    threshold = _env_int("LIVEDUB_LONG_QA_THRESHOLD_SEC", 480, 120, 3600)
    if not _enabled() or not duration or duration <= threshold:
        return await base_runner(**common)
    return await _run_long_qa(base_runner, **common)


def decorate_segment_report(text: str, qa: dict[str, Any]) -> str:
    if not isinstance(qa, dict) or not qa.get("_segmented"):
        return text
    checked = int(qa.get("_segments_checked") or 0)
    total = int(qa.get("_segments_total") or 0)
    coverage = float(qa.get("_coverage_ratio") or 0) * 100
    note = (
        f"⚠️ Сегментная проверка частичная: {checked}/{total}, покрытие {coverage:.0f}%."
        if qa.get("_segmented_partial")
        else f"🧩 Вся запись проверена по сегментам: {checked}/{total}, покрытие {coverage:.0f}%."
    )
    lines = str(text or "").splitlines()
    lines.insert(1 if lines else 0, note)
    try:
        from converters.md_telegraph import safe_trim_caption
        return safe_trim_caption(chr(10).join(lines), 3900)
    except Exception:
        return chr(10).join(lines)[:3900]


__all__ = [
    "aggregate_segment_results",
    "decorate_segment_report",
    "run_long_translation_qa",
    "segment_windows",
]
