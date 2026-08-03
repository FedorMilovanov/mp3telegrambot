#!/usr/bin/env python3
"""Quality-first verification and rendering for multi-fragment Highlights.

The first Gemini extras pass is deliberately treated as a *proposal*: it only
sees compressed analysis metadata and therefore cannot prove exact sentence
boundaries.  This module builds one short audio probe from the proposed source
windows, transcribes that probe once, moves cuts to complete utterances,
rejects silence-heavy or context-dependent fragments, and asks a second
text-only Gemini pass to judge the actual recognised fragment texts.

Highlights are optional.  When the evidence cannot prove a coherent reel, the
safe result is no reel rather than a polished-looking broken one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from core.database import GEMINI_MODEL
from core.globals import (
    GEMINI_CLIENTS,
    HAS_GEMINI,
    gemini_generate,
    make_text_config_smart,
)
from services.ffmpeg import _get_video_encoder
from services.shorts_video import HAS_FASTER_WHISPER, transcribe_short_clip

logger = logging.getLogger(__name__)

_SENTENCE_END_RE = re.compile(r'[.!?…]["»”’)]*$')
_DANGLING_END_RE = re.compile(
    r'(?i)(?:[,;:—–-]|'
    r'\b(?:и|а|но|или|что|чтобы|когда|если|потому что|так как|который|которая|которые|'
    r'поскольку|хотя|ведь|как|к|с|в|на|для|из|по|от|у)\s*)["»”’)]*$'
)
_LEFT_CONTEXT_RE = re.compile(
    r'(?i)^(?:и|а|но|или|потому|поэтому|однако|ведь|тогда|так что|в смысле|'
    r'этот|эта|это|эти|такой|такая|такие|он|она|они|его|ее|её|их|'
    r'который|которая|которые|что|когда|если|поскольку|хотя)\b'
)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", re.UNICODE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _clean_text(value: Any) -> str:
    text = _CONTROL_RE.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _word_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(text)
        if len(token) >= 3
    }


def _text_similarity(left: str, right: str) -> float:
    a = _word_tokens(left)
    b = _word_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    valid = sorted((float(a), float(b)) for a, b in intervals if float(b) > float(a))
    merged: list[list[float]] = []
    for start, end in valid:
        if not merged or start > merged[-1][1] + 0.04:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(a, b) for a, b in merged]


def _interval_metrics(
    intervals: list[tuple[float, float]],
    *,
    start: float,
    end: float,
) -> tuple[float, float]:
    clipped = []
    for left, right in intervals:
        a = max(start, float(left))
        b = min(end, float(right))
        if b > a:
            clipped.append((a, b))
    merged = _merge_intervals(clipped)
    spoken = sum(b - a for a, b in merged)
    duration = max(0.001, end - start)
    max_gap = 0.0
    cursor = start
    for left, right in merged:
        max_gap = max(max_gap, left - cursor)
        cursor = max(cursor, right)
    max_gap = max(max_gap, end - cursor)
    return min(1.0, spoken / duration), max_gap


def _has_balanced_quotes(text: str) -> bool:
    pairs = (("«", "»"), ("“", "”"))
    for opening, closing in pairs:
        if text.count(opening) != text.count(closing):
            return False
    if text.count('"') == 1:
        return False
    return True


def _segment_intervals(segments: list[dict]) -> list[tuple[float, float]]:
    word_intervals = []
    for segment in segments:
        for word in segment.get("words") or []:
            try:
                start = float(word.get("start", 0))
                end = float(word.get("end", start))
            except (TypeError, ValueError):
                continue
            if end > start:
                word_intervals.append((start, end))
    if word_intervals:
        return word_intervals

    segment_intervals = []
    for segment in segments:
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            continue
        if end > start and _clean_text(segment.get("text")):
            segment_intervals.append((start, end))
    return segment_intervals


def _needs_left_context(text: str) -> bool:
    value = _clean_text(text).lstrip("«“\"'—–- ")
    return bool(_LEFT_CONTEXT_RE.match(value))


def _ends_cleanly(text: str) -> bool:
    value = _clean_text(text)
    return bool(value and _SENTENCE_END_RE.search(value) and not _DANGLING_END_RE.search(value))


def _normalise_segments(segments: list[dict]) -> list[dict]:
    normalised = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            continue
        text = _clean_text(segment.get("text"))
        if end <= start or not text:
            continue
        words = []
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            try:
                word_start = float(word.get("start", start))
                word_end = float(word.get("end", word_start))
            except (TypeError, ValueError):
                continue
            word_text = _clean_text(word.get("word"))
            if word_end > word_start and word_text:
                words.append(
                    {
                        "start": word_start,
                        "end": word_end,
                        "word": word_text,
                    }
                )
        normalised.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "words": words,
            }
        )
    return sorted(normalised, key=lambda item: (item["start"], item["end"]))


def refine_fragment_from_transcript(
    fragment: dict,
    segments: list[dict],
    *,
    window_start: float,
    window_end: float,
) -> tuple[dict | None, dict]:
    """Move one approximate fragment to complete utterance boundaries.

    This function is pure and intentionally exported for focused regression
    tests. Segment times and returned times are source-video seconds.
    """
    try:
        original_start = float(fragment.get("start_seconds", 0))
        original_end = float(fragment.get("end_seconds", 0))
    except (TypeError, ValueError):
        return None, {"reason": "invalid_times"}

    if original_end <= original_start:
        return None, {"reason": "invalid_times"}

    items = _normalise_segments(segments)
    if not items:
        return None, {"reason": "no_transcript"}

    first_index = next(
        (index for index, item in enumerate(items) if item["end"] >= original_start),
        None,
    )
    if first_index is None:
        return None, {"reason": "start_not_covered"}

    context_hops = 0
    while (
        first_index > 0
        and context_hops < 3
        and (
            _needs_left_context(items[first_index]["text"])
            or not _ends_cleanly(items[first_index - 1]["text"])
        )
        and items[first_index]["start"] - items[first_index - 1]["end"] <= 1.8
        and items[first_index - 1]["start"] >= window_start
    ):
        first_index -= 1
        context_hops += 1

    last_index = next(
        (index for index in range(first_index, len(items)) if items[index]["end"] >= original_end),
        len(items) - 1,
    )
    while last_index + 1 < len(items):
        joined = " ".join(item["text"] for item in items[first_index : last_index + 1])
        if _ends_cleanly(joined):
            break
        if items[last_index + 1]["end"] > window_end:
            break
        last_index += 1

    selected = items[first_index : last_index + 1]
    text = _clean_text(" ".join(item["text"] for item in selected))
    refined_start = max(window_start, selected[0]["start"] - 0.08)
    refined_end = min(window_end, selected[-1]["end"] + 0.12)
    duration = refined_end - refined_start

    intervals = _segment_intervals(selected)
    speech_coverage, max_gap = _interval_metrics(
        intervals,
        start=refined_start,
        end=refined_end,
    )
    word_count = len(_WORD_RE.findall(text))
    max_silence = _env_float("HIGHLIGHTS_MAX_INTERNAL_SILENCE_SECONDS", 2.8, 1.2, 6.0)
    min_coverage = _env_float("HIGHLIGHTS_MIN_SPEECH_COVERAGE", 0.52, 0.30, 0.85)

    reason = ""
    if duration < 4.0:
        reason = "too_short_after_refine"
    elif duration > 30.0:
        reason = "too_long_after_refine"
    elif max_gap > max_silence:
        reason = "internal_silence"
    elif speech_coverage < min_coverage:
        reason = "low_speech_coverage"
    elif _needs_left_context(text):
        reason = "unresolved_left_context"
    elif not _ends_cleanly(text):
        reason = "unfinished_ending"
    elif not _has_balanced_quotes(text):
        reason = "unbalanced_quote"
    elif word_count < 8:
        reason = "too_few_words"

    evidence = {
        "reason": reason or "accepted",
        "original_start": original_start,
        "original_end": original_end,
        "refined_start": round(refined_start, 3),
        "refined_end": round(refined_end, 3),
        "duration": round(duration, 3),
        "word_count": word_count,
        "speech_coverage": round(speech_coverage, 4),
        "max_silence": round(max_gap, 3),
        "transcript": text,
    }
    if reason:
        return None, evidence

    subtitle_segments = []
    for item in selected:
        seg_start = max(refined_start, item["start"])
        seg_end = min(refined_end, item["end"])
        if seg_end <= seg_start:
            continue
        words = []
        for word in item.get("words") or []:
            word_start = max(refined_start, float(word["start"]))
            word_end = min(refined_end, float(word["end"]))
            if word_end > word_start:
                words.append(
                    {
                        "start": word_start,
                        "end": word_end,
                        "word": word["word"],
                    }
                )
        subtitle_segments.append(
            {
                "start": seg_start,
                "end": seg_end,
                "text": item["text"],
                "words": words,
            }
        )

    refined = {
        **fragment,
        "start_seconds": round(refined_start, 3),
        "end_seconds": round(refined_end, 3),
        "transcript": text,
        "_subtitle_source_segments": subtitle_segments,
        "_quality": {
            key: value
            for key, value in evidence.items()
            if key != "transcript"
        },
    }
    return refined, evidence


def _drop_overlaps_and_repeats(fragments: list[dict]) -> tuple[list[dict], list[dict]]:
    accepted: list[dict] = []
    rejected: list[dict] = []
    for fragment in sorted(fragments, key=lambda item: item["start_seconds"]):
        if accepted:
            previous = accepted[-1]
            if float(fragment["start_seconds"]) < float(previous["end_seconds"]) - 0.15:
                rejected.append({"reason": "source_overlap", "fragment": fragment})
                continue
        duplicate = next(
            (
                previous
                for previous in accepted
                if _text_similarity(
                    str(previous.get("transcript", "")),
                    str(fragment.get("transcript", "")),
                )
                >= 0.62
            ),
            None,
        )
        if duplicate is not None:
            rejected.append({"reason": "repeated_meaning", "fragment": fragment})
            continue
        accepted.append(fragment)
    return accepted, rejected


def build_delivery_subtitles(fragments: list[dict]) -> list[dict]:
    """Map source-relative verified transcript segments onto reel time."""
    output: list[dict] = []
    reel_cursor = 0.0
    for fragment in fragments:
        source_start = float(fragment["start_seconds"])
        source_end = float(fragment["end_seconds"])
        for segment in fragment.get("_subtitle_source_segments") or []:
            start = reel_cursor + max(0.0, float(segment["start"]) - source_start)
            end = reel_cursor + min(
                source_end - source_start,
                float(segment["end"]) - source_start,
            )
            if end <= start:
                continue
            words = []
            for word in segment.get("words") or []:
                word_start = reel_cursor + max(0.0, float(word["start"]) - source_start)
                word_end = reel_cursor + min(
                    source_end - source_start,
                    float(word["end"]) - source_start,
                )
                if word_end > word_start:
                    words.append(
                        {
                            "start": round(word_start, 3),
                            "end": round(word_end, 3),
                            "word": word["word"],
                        }
                    )
            output.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": segment["text"],
                    "words": words,
                }
            )
        reel_cursor += source_end - source_start
    return output


def scale_subtitle_segments(segments: list[dict], speed: float) -> list[dict]:
    if not segments or abs(float(speed or 1.0) - 1.0) <= 0.01:
        return segments
    factor = max(0.01, float(speed))
    scaled = []
    for segment in segments:
        item = {
            **segment,
            "start": float(segment["start"]) / factor,
            "end": float(segment["end"]) / factor,
        }
        item["words"] = [
            {
                **word,
                "start": float(word["start"]) / factor,
                "end": float(word["end"]) / factor,
            }
            for word in segment.get("words") or []
        ]
        scaled.append(item)
    return scaled


def _probe_windows(
    fragments: list[dict],
    *,
    source_duration: float,
) -> list[dict]:
    pre_roll = _env_float("HIGHLIGHTS_QA_PREROLL_SECONDS", 4.0, 1.0, 10.0)
    post_roll = _env_float("HIGHLIGHTS_QA_POSTROLL_SECONDS", 6.0, 1.0, 12.0)
    windows = []
    reel_cursor = 0.0
    separator = 1.5
    for index, fragment in enumerate(fragments):
        start = max(0.0, float(fragment["start_seconds"]) - pre_roll)
        end = float(fragment["end_seconds"]) + post_roll
        if source_duration > 0:
            end = min(source_duration, end)
        duration = end - start
        if duration <= 0:
            continue
        windows.append(
            {
                "index": index,
                "source_start": start,
                "source_end": end,
                "probe_start": reel_cursor,
                "probe_end": reel_cursor + duration,
            }
        )
        reel_cursor += duration + separator
    return windows


async def _build_audio_probe(
    source_video_path: Path,
    windows: list[dict],
    work_dir: Path,
) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not source_video_path.exists() or not windows:
        return None

    part_paths: list[Path] = []
    separator_path = work_dir / "separator.wav"
    separator_cmd = [
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=16000:cl=mono",
        "-t",
        "1.5",
        "-c:a",
        "pcm_s16le",
        "-y",
        str(separator_path),
    ]
    loop = asyncio.get_running_loop()
    separator_proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            separator_cmd,
            capture_output=True,
            timeout=30,
        ),
    )
    if separator_proc.returncode != 0 or not separator_path.exists():
        return None

    for window in windows:
        part_path = work_dir / f"window_{window['index']:02d}.wav"
        part_paths.append(part_path)
        duration = window["source_end"] - window["source_start"]
        cmd = [
            ffmpeg,
            "-ss",
            f"{window['source_start']:.3f}",
            "-i",
            str(source_video_path),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(part_path),
        ]
        proc = await loop.run_in_executor(
            None,
            lambda c=cmd: subprocess.run(c, capture_output=True, timeout=90),
        )
        if proc.returncode != 0 or not part_path.exists() or part_path.stat().st_size < 1024:
            return None

    concat_path = work_dir / "probe_concat.txt"
    probe_path = work_dir / "highlights_probe.wav"
    lines = []
    for index, part_path in enumerate(part_paths):
        lines.append(f"file '{part_path.resolve()}'")
        if index + 1 < len(part_paths):
            lines.append(f"file '{separator_path.resolve()}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    concat_cmd = [
        ffmpeg,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-y",
        str(probe_path),
    ]
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(concat_cmd, capture_output=True, timeout=120),
    )
    if proc.returncode != 0 or not probe_path.exists() or probe_path.stat().st_size < 1024:
        return None
    return probe_path


def _map_probe_segments_to_source(
    probe_segments: list[dict],
    windows: list[dict],
) -> dict[int, list[dict]]:
    """Map only transcript evidence that is actually inside one probe window."""
    mapped: dict[int, list[dict]] = {int(window["index"]): [] for window in windows}
    items = _normalise_segments(probe_segments)
    for item in items:
        center = (item["start"] + item["end"]) / 2
        window = next(
            (
                candidate
                for candidate in windows
                if candidate["probe_start"] <= center <= candidate["probe_end"]
            ),
            None,
        )
        if window is None:
            continue
        clipped_start = max(float(window["probe_start"]), float(item["start"]))
        clipped_end = min(float(window["probe_end"]), float(item["end"]))
        if clipped_end <= clipped_start:
            continue
        shift = float(window["source_start"]) - float(window["probe_start"])
        words = []
        for word in item.get("words") or []:
            word_start = max(clipped_start, float(word["start"]))
            word_end = min(clipped_end, float(word["end"]))
            if word_end > word_start:
                words.append(
                    {
                        **word,
                        "start": word_start + shift,
                        "end": word_end + shift,
                    }
                )
        if words:
            mapped_text = _clean_text(" ".join(word["word"] for word in words))
        else:
            overhang = (clipped_start - float(item["start"])) + (
                float(item["end"]) - clipped_end
            )
            if overhang > 0.25:
                # A no-word segment crossing a synthetic separator cannot be
                # split safely; accepting its full text would contaminate a cut.
                continue
            mapped_text = item["text"]
        mapped[int(window["index"])].append(
            {
                **item,
                "start": clipped_start + shift,
                "end": clipped_end + shift,
                "text": mapped_text,
                "words": words,
            }
        )
    return mapped


async def _judge_actual_transcripts(
    *,
    title: str,
    fragments: list[dict],
) -> tuple[bool, list[int], str]:
    if not HAS_GEMINI or not GEMINI_CLIENTS:
        return False, [], "gemini_unavailable"

    payload = [
        {
            "index": index + 1,
            "source_start": round(float(fragment["start_seconds"]), 2),
            "source_end": round(float(fragment["end_seconds"]), 2),
            "text": _clean_text(fragment.get("transcript"))[:900],
        }
        for index, fragment in enumerate(fragments)
    ]
    prompt = (
        "Ты — строгий монтажный редактор. Проверяешь уже РАСПОЗНАННЫЕ "
        "фрагменты одного рекламного ролика Highlights.\n"
        "Не додумывай контекст и не исправляй расшифровку. Оцени только данный текст.\n\n"
        f"Заявленный заголовок: {title}\n"
        f"Фрагменты в хронологическом порядке:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        "Верни только JSON: "
        '{"accept":true,"keep_indices":[1,2,3,4],"reason":"кратко"}.\n'
        "Правила:\n"
        "- каждый оставленный фрагмент — законченная мини-мысль, понятная без пропущенной фразы;\n"
        "- нельзя оставлять обрывок цитаты, ссылку без продолжения, местоимение без ясного предмета;\n"
        "- все оставленные фрагменты должны раскрывать ОДНУ точную тему заголовка;\n"
        "- порядок менять нельзя, он уже хронологический;\n"
        "- рекламный ролик не должен содержать проходной setup без payoff;\n"
        "- оставь минимум 4 фрагмента; если это невозможно, accept=false;\n"
        "- при сомнении отклоняй: плохой ролик хуже отсутствующего."
    )

    async def _call(client):
        config = make_text_config_smart(
            temperature=0.0,
            max_output_tokens=1000,
            thinking_level="low",
            response_mime_type="application/json",
        )
        return await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
        )

    timeout = _env_float("HIGHLIGHTS_COHERENCE_TIMEOUT_SECONDS", 60.0, 15.0, 180.0)
    try:
        response = await asyncio.wait_for(
            gemini_generate(GEMINI_CLIENTS, _call),
            timeout=timeout,
        )
        raw = _clean_text(getattr(response, "text", "") or "")
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return False, [], "coherence_json_missing"
        data = json.loads(raw[start : end + 1])
        accepted = bool(data.get("accept"))
        keep = []
        for value in data.get("keep_indices") or []:
            if isinstance(value, bool):
                continue
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= index <= len(fragments) and index not in keep:
                keep.append(index)
        keep.sort()
        reason = _clean_text(data.get("reason"))[:240] or "no_reason"
        if not accepted or len(keep) < 4:
            return False, keep, reason
        return True, keep, reason
    except Exception as exc:
        logger.warning(
            "Highlights coherence QA failed: %s: %s",
            type(exc).__name__,
            str(exc)[:180],
        )
        return False, [], f"coherence_error:{type(exc).__name__}"


async def refine_highlights_candidate(
    source_video_path: Path,
    candidate: dict,
    *,
    ai_data: dict | None = None,
    source_duration: float = 0.0,
) -> tuple[dict | None, dict]:
    """Return a verified candidate or ``None`` with structured evidence."""
    report: dict[str, Any] = {
        "policy": "actual-transcript-highlights-quality-v2",
        "accepted": False,
        "rejections": [],
    }
    if not HAS_FASTER_WHISPER:
        report["reason"] = "whisper_unavailable"
        return None, report
    if not source_video_path.exists():
        report["reason"] = "source_missing"
        return None, report

    raw_fragments = candidate.get("fragments") or []
    if not isinstance(raw_fragments, list) or not (4 <= len(raw_fragments) <= 7):
        report["reason"] = "invalid_fragment_count"
        return None, report

    fragments = []
    for fragment in raw_fragments:
        if not isinstance(fragment, dict):
            continue
        try:
            start = float(fragment.get("start_seconds", 0))
            end = float(fragment.get("end_seconds", 0))
        except (TypeError, ValueError):
            continue
        if end > start >= 0 and (source_duration <= 0 or end <= source_duration + 0.5):
            fragments.append({**fragment, "start_seconds": start, "end_seconds": end})
    fragments.sort(key=lambda item: item["start_seconds"])
    if len(fragments) < 4:
        report["reason"] = "too_few_valid_times"
        return None, report

    windows = _probe_windows(fragments, source_duration=float(source_duration or 0))
    if len(windows) != len(fragments):
        report["reason"] = "probe_window_failure"
        return None, report

    with tempfile.TemporaryDirectory(prefix="highlights_qa_") as temp_dir:
        probe = await _build_audio_probe(source_video_path, windows, Path(temp_dir))
        if probe is None:
            report["reason"] = "probe_render_failed"
            return None, report
        timeout = _env_float("HIGHLIGHTS_TRANSCRIBE_TIMEOUT_SECONDS", 240.0, 60.0, 600.0)
        try:
            probe_segments = await asyncio.wait_for(
                transcribe_short_clip(probe, ai_data=ai_data or {}),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            report["reason"] = "transcription_timeout"
            return None, report
        except Exception as exc:
            report["reason"] = f"transcription_error:{type(exc).__name__}"
            return None, report

    if not probe_segments:
        report["reason"] = "transcription_empty"
        return None, report

    mapped = _map_probe_segments_to_source(probe_segments, windows)
    refined = []
    for index, fragment in enumerate(fragments):
        window = windows[index]
        accepted, evidence = refine_fragment_from_transcript(
            fragment,
            mapped.get(index, []),
            window_start=float(window["source_start"]),
            window_end=float(window["source_end"]),
        )
        if accepted is None:
            report["rejections"].append({"index": index + 1, **evidence})
            continue
        refined.append(accepted)

    # Keep every independently verified fragment separate. Clock proximity is
    # not semantic evidence and must never manufacture a new unverified cut.
    refined, structural_rejections = _drop_overlaps_and_repeats(refined)
    report["rejections"].extend(structural_rejections)
    if len(refined) < 4:
        report["reason"] = "too_few_complete_fragments"
        return None, report

    require_coherence = _env_bool("HIGHLIGHTS_REQUIRE_COHERENCE_QA", True)
    if require_coherence:
        coherent, keep_indices, coherence_reason = await _judge_actual_transcripts(
            title=_clean_text(candidate.get("title")),
            fragments=refined,
        )
        report["coherence_reason"] = coherence_reason
        report["coherence_keep_indices"] = keep_indices
        if not coherent:
            report["reason"] = "coherence_rejected"
            return None, report
        refined = [refined[index - 1] for index in keep_indices]

    if not (4 <= len(refined) <= 7):
        report["reason"] = "final_fragment_count"
        return None, report

    total_duration = sum(
        float(fragment["end_seconds"]) - float(fragment["start_seconds"])
        for fragment in refined
    )
    max_total = _env_float("HIGHLIGHTS_MAX_VERIFIED_DURATION_SECONDS", 115.0, 60.0, 180.0)
    if total_duration > max_total:
        report["reason"] = "verified_duration_too_long"
        report["total_duration"] = round(total_duration, 3)
        return None, report

    delivery_subtitles = build_delivery_subtitles(refined)
    public_fragments = []
    evidence_fragments = []
    for fragment in refined:
        evidence_fragments.append(
            {
                "start": fragment["start_seconds"],
                "end": fragment["end_seconds"],
                "quality": fragment.get("_quality") or {},
                "transcript": _clean_text(fragment.get("transcript"))[:500],
            }
        )
        public_fragments.append(
            {
                key: value
                for key, value in fragment.items()
                if key
                not in {
                    "transcript",
                    "_subtitle_source_segments",
                    "_quality",
                }
            }
        )

    verified = {
        **candidate,
        "fragments": public_fragments,
        "total_dur": round(total_duration, 3),
        "_quality_verified": True,
        "_subtitle_segments": delivery_subtitles,
        "_quality_policy": report["policy"],
    }
    report.update(
        {
            "accepted": True,
            "reason": "accepted",
            "fragment_count": len(public_fragments),
            "total_duration": round(total_duration, 3),
            "fragments": evidence_fragments,
        }
    )
    logger.info(
        "Highlights verified: fragments=%d total=%.2fs evidence=%s",
        len(public_fragments),
        total_duration,
        json.dumps(report, ensure_ascii=False)[:5000],
    )
    return verified, report


async def render_verified_highlights(
    source_video_path: Path,
    output_path: Path,
    fragments: list[dict],
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    """Render all verified cuts from source in one encode.

    Each fragment is an independently seeked input, then video/audio are joined
    by the concat filter. This avoids the old chain “encode every part → concat
    → encode again”, removes per-part AAC delay accumulation and keeps the
    transcript-derived boundaries stable. Only a 35–45 ms audio edge fade is
    used to suppress cut clicks; it is too short to conceal a bad semantic cut.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not source_video_path.exists() or not fragments:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    try:
        encoder, quality, preset = _get_video_encoder()
        input_args: list[str] = []
        filters: list[str] = []
        concat_inputs: list[str] = []

        for index, fragment in enumerate(fragments):
            start = float(fragment["start_seconds"])
            end = float(fragment["end_seconds"])
            duration = end - start
            if duration <= 0:
                return False

            input_args.extend(
                [
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    str(source_video_path),
                ]
            )
            fade_out_start = max(0.0, duration - 0.045)

            if visual_mode == "crop_zoom":
                filters.append(
                    f"[{index}:v]"
                    "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                    "scale=720:1280,fps=30,format=yuv420p,"
                    f"setpts=PTS-STARTPTS[v{index}]"
                )
            else:
                filters.extend(
                    [
                        f"[{index}:v]split=2[bg{index}][fg{index}]",
                        f"[bg{index}]"
                        "scale=720:1280:force_original_aspect_ratio=increase,"
                        f"crop=720:1280,gblur=sigma=20,setsar=1[blurred{index}]",
                        f"[fg{index}]"
                        "scale=720:1280:force_original_aspect_ratio=decrease,"
                        f"setsar=1[small{index}]",
                        f"[blurred{index}][small{index}]"
                        "overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p,"
                        f"setpts=PTS-STARTPTS[v{index}]",
                    ]
                )

            filters.append(
                f"[{index}:a]"
                "aresample=48000,asetpts=PTS-STARTPTS,"
                "afade=t=in:st=0:d=0.035,"
                f"afade=t=out:st={fade_out_start:.3f}:d=0.045[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")

        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(fragments)}:v=1:a=1[outv][outa]"
        )
        cmd = [
            ffmpeg,
            *input_args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            encoder,
            *preset,
            *quality,
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]

        from core.resource_scheduler import scheduler as resource_scheduler

        async with resource_scheduler.gpu_render:
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                ),
            )
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning(
                "Verified highlights render failed: %s",
                (proc.stderr or "")[-1000:],
            )
            return False

        logger.info(
            "Verified highlights rendered: %s fragments=%d size=%.1fMB",
            output_path.name,
            len(fragments),
            output_path.stat().st_size / (1024 * 1024),
        )
        return True
    except subprocess.TimeoutExpired:
        logger.warning("render_verified_highlights: ffmpeg timeout")
        return False
    except Exception as exc:
        logger.warning(
            "render_verified_highlights error: %s: %s",
            type(exc).__name__,
            str(exc)[:240],
        )
        return False


__all__ = [
    "build_delivery_subtitles",
    "refine_fragment_from_transcript",
    "refine_highlights_candidate",
    "render_verified_highlights",
    "scale_subtitle_segments",
]
