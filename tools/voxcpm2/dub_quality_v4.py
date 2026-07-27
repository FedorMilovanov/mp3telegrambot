#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality-first policies shared by Gemini and ready-SRT Dub Studio modes.

The successful no-bot NoChew renderer used short, deterministic candidates and
reference-only cloning.  This module restores those invariants while adding
caption coverage checks and finer timing anchors for the generic bot pipeline.
"""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

_SENTENCE_END_RE = re.compile(r"[.!?…][\s\"'»”)]*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def log(message: str) -> None:
    print(f"[DUB-QUALITY-V4] {message}", flush=True)


def _sentence_end(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search(str(text or "").strip()))


def group_cues_v4(
    cues: list[Any],
    *,
    target_seconds: float = 4.8,
    max_seconds: float = 7.0,
) -> list[dict[str, Any]]:
    """Build short semantic blocks instead of 9–13.5 second timing islands."""
    ordered = sorted(cues, key=lambda cue: (float(cue.start), float(cue.end)))
    groups: list[dict[str, Any]] = []
    current: list[Any] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = " ".join(str(item.text).strip() for item in current if str(item.text).strip())
        if text:
            groups.append(
                {
                    "id": len(groups) + 1,
                    "start": round(float(current[0].start), 3),
                    "end": round(float(current[-1].end), 3),
                    "english": re.sub(r"\s+", " ", text).strip(),
                }
            )
        current = []

    for cue in ordered:
        if not str(cue.text or "").strip() or float(cue.end) <= float(cue.start):
            continue
        if not current:
            current = [cue]
            continue
        gap = float(cue.start) - float(current[-1].end)
        prospective = float(cue.end) - float(current[0].start)
        current_duration = float(current[-1].end) - float(current[0].start)
        split_before = bool(
            gap >= 0.42
            or prospective > max_seconds
            or (current_duration >= target_seconds and _sentence_end(str(current[-1].text)))
        )
        if split_before:
            flush()
            current = [cue]
        else:
            current.append(cue)
            total = float(current[-1].end) - float(current[0].start)
            if total >= target_seconds and _sentence_end(str(current[-1].text)):
                flush()
    flush()

    if len(groups) >= 2:
        tail = groups[-1]
        previous = groups[-2]
        if (
            float(tail["end"]) - float(tail["start"]) < 1.35
            and float(tail["end"]) - float(previous["start"]) <= max_seconds + 0.45
        ):
            previous["end"] = tail["end"]
            previous["english"] = f"{previous['english']} {tail['english']}".strip()
            groups.pop()
    for index, group in enumerate(groups, start=1):
        group["id"] = index
    return groups


def _split_long_ready_cue(cue: Any, *, max_seconds: float = 7.0) -> list[tuple[float, float, str]]:
    start = float(cue.start)
    end = float(cue.end)
    text = re.sub(r"\s+", " ", str(cue.text or "")).strip()
    duration = end - start
    if duration <= max_seconds or not text:
        return [(start, end, text)]
    pieces = [item.strip() for item in _SENTENCE_SPLIT_RE.split(text) if item.strip()]
    if len(pieces) < 2:
        return [(start, end, text)]
    weights = [max(1, len(re.findall(r"\w+", item, flags=re.UNICODE))) for item in pieces]
    total_weight = sum(weights)
    result: list[tuple[float, float, str]] = []
    cursor = start
    for index, (piece, weight) in enumerate(zip(pieces, weights, strict=True)):
        piece_end = end if index == len(pieces) - 1 else cursor + duration * weight / total_weight
        result.append((cursor, piece_end, piece))
        cursor = piece_end
    return result


def group_ready_srt_v4(cues: list[Any]) -> list[dict[str, Any]]:
    """Respect the user's SRT anchors; merge only technically tiny neighbours."""
    expanded: list[dict[str, Any]] = []
    for cue in sorted(cues, key=lambda item: (float(item.start), float(item.end))):
        for start, end, text in _split_long_ready_cue(cue):
            if text and end > start:
                expanded.append({"start": start, "end": end, "source": text})

    groups: list[dict[str, Any]] = []
    for item in expanded:
        duration = float(item["end"]) - float(item["start"])
        if groups and duration < 1.15:
            previous = groups[-1]
            gap = float(item["start"]) - float(previous["end"])
            combined = float(item["end"]) - float(previous["start"])
            if gap <= 0.22 and combined <= 7.25:
                previous["end"] = item["end"]
                previous["source"] = f"{previous['source']} {item['source']}".strip()
                continue
        groups.append(dict(item))
    for index, group in enumerate(groups, start=1):
        group["id"] = index
        group["start"] = round(float(group["start"]), 3)
        group["end"] = round(float(group["end"]), 3)
    return groups


def _edge_trim(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if len(audio) < int(sample_rate * 0.25):
        return audio
    frame = max(64, int(sample_rate * 0.02))
    hop = max(32, int(sample_rate * 0.01))
    starts = np.arange(0, max(1, len(audio) - frame + 1), hop)
    rms = np.asarray([
        math.sqrt(float(np.mean(audio[pos : pos + frame] ** 2)) + 1e-12)
        for pos in starts
    ])
    peak_db = 20.0 * math.log10(float(np.max(rms)) + 1e-12)
    threshold_db = max(-48.0, peak_db - 34.0)
    active = np.flatnonzero(20.0 * np.log10(rms + 1e-12) >= threshold_db)
    if not len(active):
        return audio
    first = max(0, int(starts[int(active[0])]) - int(sample_rate * 0.08))
    last = min(len(audio), int(starts[int(active[-1])] + frame + sample_rate * 0.16))
    return audio[first:last]


def _extract_reference_part(source: Path, start: float, end: float, output: Path) -> np.ndarray:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, start):.6f}", "-to", f"{max(start + 0.2, end):.6f}",
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
    proc = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    text = proc.stderr or ""
    if "silence_start: 0" not in text and "silence_start: 0.0" not in text:
        return 0.0
    match = re.search(r"silence_end:\s*([0-9.]+)", text)
    return float(match.group(1)) if match else 0.0


def install_gemini_quality(production: Any, pipeline: Any) -> None:
    """Install caption coverage, micro-segmentation and reference policies."""
    pipeline.group_cues = group_cues_v4
    pipeline.build_reference = build_reference_v4
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
    "group_cues_v4",
    "group_ready_srt_v4",
    "install_direct_quality",
    "install_gemini_quality",
]
