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


__all__ = [
    "build_reference_v4",
    "build_render_segments_v4",
    "group_cues_v4",
    "group_ready_srt_v4",
]

_BASE_ALL = tuple(globals().get('__all__', ()))



import re

import types


from tools.voxcpm2 import russian_pronunciation

POLICY = "ready-srt-semantic-breath-grouping-v1"

POST_MERGE_POLICY = "fragile-heading-and-laughter-breath-merge-v1"

TARGET_SECONDS = 4.15

MIN_PREFERRED_SECONDS = 2.35

MAX_INTERNAL_GAP_SECONDS = 0.38

PREFERRED_INTERNAL_GAP_SECONDS = 0.24

MAX_WORDS_PER_SECOND = 5.45

_MAX_GROUP_SLACK = 0.04

def _normal_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def _words(value: Any) -> int:
    return len(re.findall(r"\w+", _normal_text(value), flags=re.UNICODE))

def _sentence_end(value: Any) -> bool:
    return bool(re.search(r"[.!?…][\s\"'»”)]*$", _normal_text(value)))

def _protected_final_pronunciation(value: Any) -> bool:
    prepared = russian_pronunciation.prepare_segment({"text": _normal_text(value)})
    return bool(prepared.get("stress_evidence_required"))

def _atoms(cues: list[Any], *, max_seconds: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for cue_index, cue in enumerate(
        sorted(cues, key=lambda item: (float(item.start), float(item.end))),
        start=1,
    ):
        parts = _split_timed_text(
            float(cue.start),
            float(cue.end),
            str(cue.text or ""),
            max_seconds=float(max_seconds),
        )
        for part_index, part in enumerate(parts, start=1):
            text = _normal_text(part.text)
            if not text or float(part.end) <= float(part.start):
                continue
            result.append(
                {
                    "start": float(part.start),
                    "end": float(part.end),
                    "source": text,
                    "source_cue": cue_index,
                    "source_part": part_index,
                }
            )
    return result

def _candidate(
    atoms: list[dict[str, Any]],
    left: int,
    right: int,
    *,
    max_seconds: float,
) -> dict[str, Any] | None:
    selected = atoms[left:right + 1]
    start = float(selected[0]["start"])
    end = float(selected[-1]["end"])
    duration = end - start
    if duration <= 0.0 or duration > float(max_seconds) + _MAX_GROUP_SLACK:
        return None
    gaps = [
        max(0.0, float(selected[index + 1]["start"]) - float(selected[index]["end"]))
        for index in range(len(selected) - 1)
    ]
    if gaps and max(gaps) > MAX_INTERNAL_GAP_SECONDS:
        return None
    if any(_protected_final_pronunciation(item["source"]) for item in selected[:-1]):
        return None

    text = _normal_text(" ".join(str(item["source"]) for item in selected))
    words = _words(text)
    rate = words / max(0.35, duration)
    if rate > MAX_WORDS_PER_SECOND:
        return None

    target = min(TARGET_SECONDS, max(1.6, float(max_seconds) - 0.35))
    cost = ((duration - target) / max(1.0, target)) ** 2
    if duration < MIN_PREFERRED_SECONDS:
        cost += ((MIN_PREFERRED_SECONDS - duration) * 1.55) ** 2
    if len(selected) == 1:
        cost += 0.22
        lowered = text.casefold().replace("ё", "е")
        if text.endswith(":") or "стих:" in lowered:
            cost += 2.8
        if "смеется" in lowered or "смеюсь" in lowered:
            cost += 1.4
    if gaps:
        cost += sum(
            max(0.0, gap - PREFERRED_INTERNAL_GAP_SECONDS) * 1.8
            for gap in gaps
        )
    cost += -0.10 if _sentence_end(text) else 0.14
    cost += 0.035
    return {
        "start": start,
        "end": end,
        "source": text,
        "duration": duration,
        "word_rate": rate,
        "cost": cost,
        "source_cue_count": len({int(item["source_cue"]) for item in selected}),
        "source_parts": [
            {
                "cue": int(item["source_cue"]),
                "part": int(item["source_part"]),
                "start": float(item["start"]),
                "end": float(item["end"]),
                "text": str(item["source"]),
            }
            for item in selected
        ],
        "internal_gaps": gaps,
    }

def _fragile_heading(text: str) -> bool:
    normalized = _normal_text(text).casefold().replace("ё", "е")
    return bool(normalized.endswith(":") or re.search(r"\bстих\s*:$", normalized))

def _fragile_laughter(text: str) -> bool:
    normalized = _normal_text(text).casefold().replace("ё", "е")
    return bool(re.search(r"\bсмеет(?:ся|есь)?\b|\bсмеюсь\b", normalized))

def _merge_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    max_seconds: float,
) -> dict[str, Any] | None:
    if _protected_final_pronunciation(left.get("source")):
        return None
    start = float(left["start"])
    end = float(right["end"])
    duration = end - start
    gap = max(0.0, float(right["start"]) - float(left["end"]))
    text = _normal_text(f"{left['source']} {right['source']}")
    rate = _words(text) / max(0.35, duration)
    if (
        duration > float(max_seconds) + _MAX_GROUP_SLACK
        or gap > MAX_INTERNAL_GAP_SECONDS
        or rate > MAX_WORDS_PER_SECOND
    ):
        return None
    return {
        "id": int(left.get("id") or 0),
        "start": round(start, 3),
        "end": round(end, 3),
        "source": text,
        "grouping_policy": POLICY,
        "post_merge_policy": POST_MERGE_POLICY,
        "source_cue_count": len(
            {
                int(item.get("cue") or 0)
                for item in [*(left.get("source_parts") or []), *(right.get("source_parts") or [])]
            }
            - {0}
        ),
        "source_parts": [*(left.get("source_parts") or []), *(right.get("source_parts") or [])],
        "internal_gaps": [
            *(left.get("internal_gaps") or []),
            round(gap, 6),
            *(right.get("internal_gaps") or []),
        ],
        "word_rate": round(rate, 6),
    }

def _merge_fragile_groups(
    groups: list[dict[str, Any]],
    *,
    max_seconds: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(groups):
        current = dict(groups[index])
        following = dict(groups[index + 1]) if index + 1 < len(groups) else None
        if following is not None and (
            _fragile_heading(str(current.get("source") or ""))
            or _fragile_laughter(str(current.get("source") or ""))
            or _fragile_laughter(str(following.get("source") or ""))
        ):
            merged = _merge_pair(current, following, max_seconds=max_seconds)
            if merged is not None:
                result.append(merged)
                index += 2
                continue
        result.append(current)
        index += 1
    for position, item in enumerate(result, start=1):
        item["id"] = position
    return result

def group_ready_srt_v4(cues: list[Any], *, max_seconds: float = 7.0) -> list[dict[str, Any]]:
    """Partition ready SRT into natural bounded breaths with exact text coverage."""
    limit = float(max_seconds)
    if not math.isfinite(limit) or limit < 0.35:
        raise RuntimeError("max_seconds для ready-SRT grouping некорректен.")
    atoms = _atoms(cues, max_seconds=limit)
    if not atoms:
        return []

    count = len(atoms)
    best_cost = [float("inf")] * (count + 1)
    best_next = [-1] * (count + 1)
    best_group: list[dict[str, Any] | None] = [None] * (count + 1)
    best_cost[count] = 0.0
    for left in range(count - 1, -1, -1):
        for right in range(left, count):
            candidate = _candidate(atoms, left, right, max_seconds=limit)
            if candidate is None:
                if float(atoms[right]["end"]) - float(atoms[left]["start"]) > limit + _MAX_GROUP_SLACK:
                    break
                continue
            total = float(candidate["cost"]) + best_cost[right + 1]
            if total < best_cost[left] - 1e-12:
                best_cost[left] = total
                best_next[left] = right + 1
                best_group[left] = candidate
    if best_next[0] < 0:
        raise RuntimeError("Не удалось построить физически допустимые semantic breaths из SRT.")

    groups: list[dict[str, Any]] = []
    cursor = 0
    while cursor < count:
        candidate = best_group[cursor]
        next_cursor = best_next[cursor]
        if not isinstance(candidate, dict) or next_cursor <= cursor:
            raise RuntimeError("Повреждён dynamic-programming plan ready-SRT grouping.")
        groups.append(
            {
                "id": len(groups) + 1,
                "start": round(float(candidate["start"]), 3),
                "end": round(float(candidate["end"]), 3),
                "source": str(candidate["source"]),
                "grouping_policy": POLICY,
                "source_cue_count": int(candidate["source_cue_count"]),
                "source_parts": candidate["source_parts"],
                "internal_gaps": [round(float(value), 6) for value in candidate["internal_gaps"]],
                "word_rate": round(float(candidate["word_rate"]), 6),
            }
        )
        cursor = next_cursor

    groups = _merge_fragile_groups(groups, max_seconds=limit)
    source_text = _normal_text(" ".join(str(item["source"]) for item in atoms))
    grouped_text = _normal_text(" ".join(str(item["source"]) for item in groups))
    if source_text != grouped_text:
        raise RuntimeError("Semantic-breath grouping изменил текст готового SRT.")
    if any(
        float(item["end"]) - float(item["start"]) > limit + _MAX_GROUP_SLACK + 1e-9
        for item in groups
    ):
        raise RuntimeError("Semantic-breath grouping создал слишком длинную реплику.")
    return groups

group_ready_srt_v4 = group_ready_srt_v4

__all__ = sorted(
    set(name for name in _BASE_ALL if not name.startswith("__"))
    | {
        "MAX_INTERNAL_GAP_SECONDS",
        "MAX_WORDS_PER_SECOND",
        "MIN_PREFERRED_SECONDS",
        "POLICY",
        "POST_MERGE_POLICY",
        "PREFERRED_INTERNAL_GAP_SECONDS",
        "TARGET_SECONDS",
        "_merge_fragile_groups",
        "group_ready_srt_v4",
    }
)
