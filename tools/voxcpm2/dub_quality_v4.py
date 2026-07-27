#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality-first policies shared by Gemini and ready-SRT Dub Studio modes.

The successful no-bot NoChew renderer used short, deterministic candidates and
reference-only cloning. This module restores those invariants while adding
caption coverage checks and finer timing anchors for the generic bot pipeline.
"""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2.activity_quality import sustained_activity_index

_SENTENCE_END_RE = re.compile(r"[.!?…][\s\"'»”)]*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_MIN_GROUP_SECONDS = 1.35
_MAX_GROUP_SLACK = 0.25


@dataclass(frozen=True)
class _CuePart:
    start: float
    end: float
    text: str


def log(message: str) -> None:
    print(f"[DUB-QUALITY-V4] {message}", flush=True)


def _sentence_end(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search(str(text or "").strip()))


def _word_count(text: str) -> int:
    return max(1, len(re.findall(r"\w+", str(text or ""), flags=re.UNICODE)))


def _split_words_balanced(text: str, parts: int) -> list[str]:
    tokens = str(text or "").split()
    if parts <= 1 or len(tokens) <= 1:
        return [" ".join(tokens).strip()]
    parts = min(parts, len(tokens))
    result: list[str] = []
    for index in range(parts):
        left = round(index * len(tokens) / parts)
        right = round((index + 1) * len(tokens) / parts)
        value = " ".join(tokens[left:right]).strip()
        if value:
            result.append(value)
    return result or [" ".join(tokens).strip()]


def _split_timed_text(
    start: float,
    end: float,
    text: str,
    *,
    max_seconds: float,
) -> list[_CuePart]:
    """Split an overlong cue without dropping or rewriting a single word."""
    start = float(start)
    end = float(end)
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    duration = end - start
    if not text or duration <= 0:
        return []
    if duration <= max_seconds:
        return [_CuePart(start, end, text)]

    sentence_parts = [item.strip() for item in _SENTENCE_SPLIT_RE.split(text) if item.strip()]
    if len(sentence_parts) < 2:
        sentence_parts = _split_words_balanced(text, max(2, math.ceil(duration / max_seconds)))

    total_words = sum(_word_count(item) for item in sentence_parts)
    refined: list[str] = []
    for item in sentence_parts:
        estimated = duration * _word_count(item) / max(1, total_words)
        refined.extend(_split_words_balanced(item, max(1, math.ceil(estimated / max_seconds))))

    weights = [_word_count(item) for item in refined]
    total_weight = sum(weights)
    result: list[_CuePart] = []
    cursor = start
    for index, (item, weight) in enumerate(zip(refined, weights, strict=True)):
        item_end = end if index == len(refined) - 1 else cursor + duration * weight / total_weight
        result.append(_CuePart(cursor, item_end, item))
        cursor = item_end
    return result


def _merge_tiny_groups(
    groups: list[dict[str, Any]],
    *,
    text_key: str,
    max_seconds: float,
    min_seconds: float = _MIN_GROUP_SECONDS,
) -> list[dict[str, Any]]:
    groups = [dict(item) for item in groups]
    changed = True
    while changed and len(groups) >= 2:
        changed = False
        for index, item in enumerate(groups):
            if float(item["end"]) - float(item["start"]) >= min_seconds:
                continue
            candidates: list[tuple[float, int]] = []
            if index > 0:
                previous = groups[index - 1]
                combined = float(item["end"]) - float(previous["start"])
                if combined <= max_seconds + _MAX_GROUP_SLACK:
                    candidates.append((float(item["start"]) - float(previous["end"]), index - 1))
            if index + 1 < len(groups):
                following = groups[index + 1]
                combined = float(following["end"]) - float(item["start"])
                if combined <= max_seconds + _MAX_GROUP_SLACK:
                    candidates.append((float(following["start"]) - float(item["end"]), index + 1))
            if not candidates:
                continue
            _, neighbour_index = min(candidates, key=lambda pair: (max(0.0, pair[0]), abs(pair[1] - index)))
            left_index = min(index, neighbour_index)
            right_index = max(index, neighbour_index)
            left = groups[left_index]
            right = groups[right_index]
            groups[left_index] = {
                **left,
                "start": float(left["start"]),
                "end": float(right["end"]),
                text_key: f"{left[text_key]} {right[text_key]}".strip(),
            }
            groups.pop(right_index)
            changed = True
            break
    return groups


def group_cues_v4(
    cues: list[Any],
    *,
    target_seconds: float = 4.8,
    max_seconds: float = 7.0,
) -> list[dict[str, Any]]:
    """Build short semantic blocks instead of 9–13.5 second timing islands."""
    ordered: list[_CuePart] = []
    for cue in sorted(cues, key=lambda item: (float(item.start), float(item.end))):
        ordered.extend(
            _split_timed_text(
                float(cue.start),
                float(cue.end),
                str(cue.text or ""),
                max_seconds=max_seconds,
            )
        )

    groups: list[dict[str, Any]] = []
    current: list[_CuePart] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = " ".join(item.text.strip() for item in current if item.text.strip())
        if text:
            groups.append(
                {
                    "id": len(groups) + 1,
                    "start": float(current[0].start),
                    "end": float(current[-1].end),
                    "english": re.sub(r"\s+", " ", text).strip(),
                }
            )
        current = []

    for cue in ordered:
        if not cue.text or cue.end <= cue.start:
            continue
        if not current:
            current = [cue]
            continue
        gap = cue.start - current[-1].end
        prospective = cue.end - current[0].start
        current_duration = current[-1].end - current[0].start
        split_before = bool(
            gap >= 0.42
            or prospective > max_seconds
            or (current_duration >= target_seconds and _sentence_end(current[-1].text))
        )
        if split_before:
            flush()
            current = [cue]
        else:
            current.append(cue)
            total = current[-1].end - current[0].start
            if total >= target_seconds and _sentence_end(current[-1].text):
                flush()
    flush()

    groups = _merge_tiny_groups(groups, text_key="english", max_seconds=max_seconds)
    for index, group in enumerate(groups, start=1):
        group["id"] = index
        group["start"] = round(float(group["start"]), 3)
        group["end"] = round(float(group["end"]), 3)
    return groups


def group_ready_srt_v4(cues: list[Any], *, max_seconds: float = 7.0) -> list[dict[str, Any]]:
    """Respect user SRT anchors and split only physically overlong cues."""
    groups: list[dict[str, Any]] = []
    for cue in sorted(cues, key=lambda item: (float(item.start), float(item.end))):
        for part in _split_timed_text(
            float(cue.start),
            float(cue.end),
            str(cue.text or ""),
            max_seconds=max_seconds,
        ):
            groups.append({"start": part.start, "end": part.end, "source": part.text})

    groups = _merge_tiny_groups(
        groups,
        text_key="source",
        max_seconds=max_seconds,
        min_seconds=1.15,
    )
    for index, group in enumerate(groups, start=1):
        group["id"] = index
        group["start"] = round(float(group["start"]), 3)
        group["end"] = round(float(group["end"]), 3)
    return groups


def build_render_segments_v4(
    groups: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Place each phrase inside its own source window with no delayed overlap."""
    delay = max(0, int(delay_ms)) / 1000.0
    render_segments: list[dict[str, Any]] = []
    subtitles: list[Any] = []
    from tools.voxcpm2 import generic_short_production as pipeline

    previous_audible_end = 0.0
    for index, (source, translated) in enumerate(zip(groups, translations, strict=True), start=1):
        start = max(previous_audible_end, max(0.0, float(source["start"])))
        source_end = min(float(duration), float(source["end"]))
        if source_end <= start:
            raise RuntimeError(f"Реплика #{index} не имеет свободного временного окна.")

        available = source_end - start
        minimum_voice_window = min(1.05, max(0.35, available))
        effective_delay = min(delay, max(0.0, available - minimum_voice_window))
        effective_delay_ms = int(round(effective_delay * 1000.0))
        render_end = source_end - effective_delay
        target_duration = render_end - start
        if target_duration < 0.35:
            raise RuntimeError(f"Реплика #{index} короче 0.35 сек. и не может быть озвучена безопасно.")

        profile = "composite" if index == len(groups) or index % 4 == 0 else "extended"
        default_guard = 0.42 if profile == "composite" else 0.36
        tail_guard = min(default_guard, max(0.08, target_duration * 0.18))
        text = str(translated["russian"]).strip()
        render_segments.append(
            {
                "id": index,
                "start": round(start, 3),
                "end": round(render_end, 3),
                "start_delay_ms": effective_delay_ms,
                "reference_profile": profile,
                "tail_guard": round(tail_guard, 3),
                "text": text,
                "source_end": round(source_end, 3),
                "source": source.get("source") or source.get("english") or "",
                "quality_timing": "local-window-v4.1",
            }
        )
        subtitle_start = min(float(duration), start + effective_delay)
        subtitles.append(pipeline.Cue(subtitle_start, source_end, text))
        previous_audible_end = source_end
    return render_segments, subtitles


def _frame_activity(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray, int]:
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    frame = max(64, int(sample_rate * 0.02))
    hop = max(32, int(sample_rate * 0.01))
    starts = np.arange(0, max(1, len(audio) - frame + 1), hop)
    rms = np.asarray(
        [math.sqrt(float(np.mean(audio[pos : pos + frame] ** 2)) + 1e-12) for pos in starts]
    )
    peak_db = 20.0 * math.log10(float(np.max(rms)) + 1e-12)
    threshold_db = max(-48.0, peak_db - 34.0)
    return 20.0 * np.log10(rms + 1e-12) >= threshold_db, starts, frame


def _sustained_index(active: np.ndarray, *, reverse: bool = False) -> int | None:
    return sustained_activity_index(active, reverse=reverse)


def _edge_trim(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if len(audio) < int(sample_rate * 0.25):
        return audio
    active, starts, frame = _frame_activity(audio, sample_rate)
    first_index = _sustained_index(active)
    last_index = _sustained_index(active, reverse=True)
    if first_index is None or last_index is None:
        return audio
    first = max(0, int(starts[first_index]) - int(sample_rate * 0.08))
    last = min(len(audio), int(starts[last_index]) + frame + int(sample_rate * 0.16))
    return audio[first:last]


def _extract_reference_part(source: Path, start: float, end: float, output: Path) -> np.ndarray:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, start):.6f}", "-t", f"{max(0.2, end - start):.6f}",
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=55,lowpass=f=7600", "-c:a", "pcm_f32le", str(output),
    ]
    proc = subprocess.run(command, check=False)
    if proc.returncode != 0 or not output.is_file():
        raise RuntimeError("FFmpeg не смог извлечь голосовой референс.")
    samples, sample_rate = sf.read(output, dtype="float32")
    if int(sample_rate) != 16000:
        raise RuntimeError(f"Неверная частота референса: {sample_rate}")
    return _edge_trim(np.asarray(samples, dtype=np.float32), int(sample_rate))


def _crossfade(parts: list[np.ndarray], sample_rate: int = 16000) -> np.ndarray:
    if not parts:
        raise RuntimeError("Не удалось получить ни одного фрагмента референса.")
    result = np.asarray(parts[0], dtype=np.float32)
    fade_max = int(sample_rate * 0.05)
    for part in parts[1:]:
        part = np.asarray(part, dtype=np.float32)
        fade = min(fade_max, len(result) // 5, len(part) // 5)
        if fade <= 8:
            result = np.concatenate([result, np.zeros(int(sample_rate * 0.03), np.float32), part])
            continue
        phase = np.linspace(0.0, math.pi / 2.0, fade, endpoint=False, dtype=np.float32)
        blended = result[-fade:] * np.cos(phase) + part[:fade] * np.sin(phase)
        result = np.concatenate([result[:-fade], blended, part[fade:]])
    return result


def build_reference_v4(
    source: Path,
    intervals: list[tuple[float, float]],
    output: Path,
    *,
    target_seconds: float,
) -> None:
    """Create a clean-edged reference with coherent runs and click-free joins."""
    valid = sorted(
        (max(0.0, float(start)), max(0.0, float(end)))
        for start, end in intervals
        if float(end) - float(start) >= 0.35
    )
    if not valid:
        raise RuntimeError("Нет пригодных интервалов для voice reference.")

    runs: list[list[float]] = []
    for start, end in valid:
        if runs and start - runs[-1][1] <= 0.42:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    target = max(5.0, min(float(target_seconds), 16.0))
    long_runs = [run for run in runs if run[1] - run[0] >= min(5.0, target * 0.55)]
    selected: list[tuple[float, float]]
    if long_runs:
        run = max(long_runs, key=lambda item: item[1] - item[0])
        length = min(target, run[1] - run[0] + 0.30)
        selected = [(max(0.0, run[0] - 0.12), max(0.2, run[0] - 0.12 + length))]
    else:
        selected_runs = sorted(runs, key=lambda item: item[1] - item[0], reverse=True)[:3]
        selected = sorted((max(0.0, item[0] - 0.10), item[1] + 0.16) for item in selected_runs)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-ref-v4-") as temp_dir:
        parts = [
            _extract_reference_part(source, start, end, Path(temp_dir) / f"part_{index:02d}.wav")
            for index, (start, end) in enumerate(selected, start=1)
        ]
    audio = _crossfade(parts)
    audio = audio[: int(target * 16000)]
    if len(audio) < int(16000 * 2.0):
        raise RuntimeError("Голосовой референс получился короче двух секунд.")

    rms = math.sqrt(float(np.mean(audio**2)) + 1e-12)
    peak = float(np.max(np.abs(audio))) + 1e-12
    rms_gain = (10.0 ** (-24.0 / 20.0)) / rms
    peak_gain = (10.0 ** (-3.0 / 20.0)) / peak
    gain = min(rms_gain, peak_gain, 10.0 ** (6.0 / 20.0))
    audio = np.clip(audio * gain, -0.999, 0.999).astype(np.float32)
    fade = min(int(16000 * 0.02), len(audio) // 8)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]
    sf.write(output, audio, 16000, subtype="PCM_24")
    log(f"reference={output.name}; parts={len(parts)}; duration={len(audio)/16000:.2f}s")


def _source_speech_onset(source: Path) -> float:
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(source),
        "-af", "silencedetect=noise=-42dB:d=0.12", "-f", "null", "-",
    ]
    proc = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = proc.stderr or ""
    if "silence_start: 0" not in text and "silence_start: 0.0" not in text:
        return 0.0
    match = re.search(r"silence_end:\s*([0-9.]+)", text)
    return float(match.group(1)) if match else 0.0


def install_gemini_quality(production: Any, pipeline: Any) -> None:
    """Install caption coverage, micro-segmentation and local timing policies."""
    pipeline.group_cues = group_cues_v4
    pipeline.build_reference = build_reference_v4
    production._build_render_segments = build_render_segments_v4
    original_acquire = production.acquire_transcript

    def acquire_with_coverage(
        source_url: str,
        source: Path,
        source_dir: Path,
        metadata: dict[str, Any],
        *,
        whisper_model: str,
        duration: float,
    ) -> tuple[list[Any], str, str]:
        cues, kind, language = original_acquire(
            source_url,
            source,
            source_dir,
            metadata,
            whisper_model=whisper_model,
            duration=duration,
        )
        if not cues:
            return cues, kind, language
        onset = _source_speech_onset(source)
        first = float(cues[0].start)
        uncovered = first - onset
        if kind != "whisper" and uncovered > 0.72:
            log(f"caption coverage gap={uncovered:.3f}s; Whisper проверяет пропущенное начало")
            whisper_cues, whisper_language = production.whisper_transcribe_auto(
                source, model_name=whisper_model
            )
            prefix = [cue for cue in whisper_cues if float(cue.end) <= first - 0.03]
            if prefix:
                cues = pipeline.normalize_cues(prefix + cues, duration)
                kind = f"{kind}+whisper_prefix"
                language = language or whisper_language
                log(f"добавлено Whisper-prefix cues={len(prefix)}; новое начало={cues[0].start:.3f}s")
            elif whisper_cues and float(whisper_cues[0].start) + 0.45 < first:
                cues = pipeline.normalize_cues(whisper_cues, duration)
                kind = "whisper_coverage_fallback"
                language = whisper_language
                log("caption track rejected: Whisper covers substantially more source speech")
        return cues, kind, language

    production.acquire_transcript = acquire_with_coverage


def install_direct_quality(production: Any, pipeline: Any) -> None:
    pipeline.build_reference = build_reference_v4
    production.group_srt_cues = group_ready_srt_v4


__all__ = [
    "build_reference_v4",
    "build_render_segments_v4",
    "group_cues_v4",
    "group_ready_srt_v4",
    "install_direct_quality",
    "install_gemini_quality",
]
