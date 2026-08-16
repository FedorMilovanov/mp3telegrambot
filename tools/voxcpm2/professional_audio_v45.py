#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared short-segment and real-reference preparation policy.

The historical module name is retained because clean production imports this API
directly. Standard production does not call ``install()`` and does not use its
legacy renderer constants. Reference extraction preserves the original speaker:
no denoiser is applied to already clean source audio.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import semantic_tts_guard_v4
from tools.voxcpm2.direct_max_quality_analysis import activity_stats, pitch_profile

POLICY = "professional-audio-v4.5"
_ORIGINAL_VERIFY = semantic_tts_guard_v4.verify_timeline_v4
_SENTENCE = re.compile(r"(?<=[.!?…;:])\s+")


def log(text: str) -> None:
    print(f"[DUB-PRO-V4.5] {text}", flush=True)


def words(text: str) -> int:
    return max(1, len(re.findall(r"\w+", str(text or ""), re.UNICODE)))


def split_balanced(text: str, count: int) -> list[str]:
    tokens = str(text or "").split()
    count = min(max(1, count), max(1, len(tokens)))
    return [
        " ".join(
            tokens[
                round(index * len(tokens) / count) :
                round((index + 1) * len(tokens) / count)
            ]
        ).strip()
        for index in range(count)
        if tokens[
            round(index * len(tokens) / count) :
            round((index + 1) * len(tokens) / count)
        ]
    ]


def split_text(text: str, duration: float, max_seconds: float = 5.4) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    required = max(1, math.ceil(max(0.1, duration) / max_seconds))
    parts = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    if len(parts) < required:
        parts = split_balanced(text, required)
    total = sum(words(part) for part in parts)
    result: list[str] = []
    for part in parts:
        part_count = max(
            1,
            math.ceil(
                duration * words(part) / max(1, total) / max_seconds
            ),
        )
        result.extend(split_balanced(part, part_count))
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_window(item: dict[str, Any], delay: float) -> tuple[float, float]:
    start = float(item.get("original_srt_start", item.get("start", 0)) or 0)
    end = float(item.get("source_end", 0) or 0)
    if end <= start:
        end = float(item.get("end", start) or start) + max(
            delay,
            int(item.get("start_delay_ms", 0) or 0) / 1000,
        )
    return max(0.0, start), max(start + 0.35, end)


def migrate_legacy_audio_repair(root: Path, request: dict[str, Any]) -> bool:
    repair_path = root / "input" / "audio_repair.json"
    segments_path = root / "segments_ru_final.json"
    if not repair_path.is_file() or not segments_path.is_file():
        return False
    repair = json.loads(repair_path.read_text(encoding="utf-8-sig"))
    old = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(repair, dict)
        or not repair.get("repair_all")
        or not isinstance(old, list)
        or not old
    ):
        return False
    delay_ms = max(0, int(request.get("russian_delay_ms") or 420))
    delay = delay_ms / 1000
    needs_migration = any(
        _legacy_window(item, delay)[1] - _legacy_window(item, delay)[0] > 6.2
        or item.get("quality_timing") != "global-delay-v4.5"
        for item in old
    )
    if not needs_migration:
        return False

    new: list[dict[str, Any]] = []
    for old_index, item in enumerate(old, 1):
        start, end = _legacy_window(item, delay)
        duration = end - start
        parts = split_text(str(item.get("text") or ""), duration)
        if not parts:
            raise RuntimeError(f"Реплика #{old_index} пуста и не может быть мигрирована.")
        weights = [words(part) for part in parts]
        total = sum(weights)
        cursor = start
        for part_index, (part, weight) in enumerate(zip(parts, weights, strict=True)):
            part_end = (
                end
                if part_index == len(parts) - 1
                else cursor + duration * weight / total
            )
            new.append(
                {
                    "id": len(new) + 1,
                    "start": round(cursor, 3),
                    "end": round(part_end, 3),
                    "start_delay_ms": delay_ms,
                    "reference_profile": "extended",
                    "tail_guard": 0.18,
                    "text": part,
                    "source_end": round(part_end, 3),
                    "source": str(item.get("source") or ""),
                    "quality_timing": "global-delay-v4.5",
                    "migrated_from_segment": int(item.get("id") or old_index),
                }
            )
            cursor = part_end
    for index, item in enumerate(new, 1):
        item["id"] = index
        if index == len(new) or index % 5 == 0:
            item["reference_profile"] = "composite"
            item["tail_guard"] = 0.22

    old_words = " ".join(
        " ".join(str(item.get("text") or "").split()) for item in old
    ).split()
    new_words = " ".join(
        " ".join(str(item.get("text") or "").split()) for item in new
    ).split()
    if old_words != new_words:
        raise RuntimeError("Миграция изменила русский текст; операция остановлена.")

    backup = root / "segments_ru_final.pre_v45.json"
    if not backup.exists():
        shutil.copy2(segments_path, backup)
    segments_path.write_text(
        json.dumps(new, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    repair.update(
        segment_ids=[item["id"] for item in new],
        segments_sha256=sha256(segments_path),
        migration=POLICY,
    )
    repair_path.write_text(
        json.dumps(repair, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path = root / "output" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if isinstance(manifest, dict):
            manifest.update(
                segments=len(new),
                audio_segmentation=POLICY,
                legacy_segments_backup=str(backup),
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    log(f"legacy timing migrated: {len(old)} -> {len(new)} segments; text preserved")
    return True


def _shifted(
    groups: list[dict[str, Any]],
    texts: Iterable[str],
    *,
    delay_ms: int,
    duration: float,
    direct: bool,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    delay = max(0, int(delay_ms)) / 1000
    result: list[dict[str, Any]] = []
    subtitles: list[pipeline.Cue] = []
    text_list = list(texts)
    if len(groups) != len(text_list):
        raise RuntimeError("Количество окон и реплик не совпадает.")
    for index, (group, text) in enumerate(zip(groups, text_list, strict=True), 1):
        start = max(0.0, float(group["start"]))
        source_end = min(duration, float(group["end"]))
        end = min(source_end, max(start + 0.35, duration - delay))
        profile = "composite" if index == len(groups) or index % 5 == 0 else "extended"
        item: dict[str, Any] = {
            "id": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_delay_ms": int(delay * 1000),
            "reference_profile": profile,
            "tail_guard": 0.22 if profile == "composite" else 0.18,
            "text": str(text).strip(),
            "source_end": round(source_end, 3),
            "source": str(group.get("source") or group.get("english") or ""),
            "quality_timing": "global-delay-v4.5",
        }
        if direct:
            item.update(
                text_policy="verbatim_user_srt",
                original_srt_start=round(start, 3),
                timing_window_expanded=False,
            )
        result.append(item)
        subtitles.append(
            pipeline.Cue(
                min(duration, start + delay),
                min(duration, source_end + delay),
                str(text).strip(),
            )
        )
    return result, subtitles


def build_render_segments_v45(
    groups: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    return _shifted(
        groups,
        (item["russian"] for item in translations),
        delay_ms=delay_ms,
        duration=duration,
        direct=False,
    )


def build_direct_segments_v45(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    return _shifted(
        groups,
        (item["source"] for item in groups),
        delay_ms=delay_ms,
        duration=duration,
        direct=True,
    )


def _decode(source: Path, output: Path) -> tuple[np.ndarray, int]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        "highpass=f=45,lowpass=f=7600",
        "-c:a",
        "pcm_f32le",
        str(output),
    ]
    if subprocess.run(command, check=False).returncode != 0:
        raise RuntimeError("Не удалось подготовить голосовой референс.")
    samples, sample_rate = sf.read(output, dtype="float32")
    return np.asarray(samples, dtype=np.float32), int(sample_rate)


def build_reference_v45(
    source: Path,
    intervals: list[tuple[float, float]],
    output: Path,
    *,
    target_seconds: float,
) -> None:
    runs: list[list[float]] = []
    for start, end in sorted(
        (max(0.0, float(left)), max(0.0, float(right)))
        for left, right in intervals
        if float(right) - float(left) >= 0.35
    ):
        if runs and start - runs[-1][1] <= 0.28:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    if not runs:
        raise RuntimeError("Нет пригодных интервалов для voice reference.")

    target = max(6.0, min(float(target_seconds), 10.0))
    window = min(5.0, max(3.2, target / 2))
    with tempfile.TemporaryDirectory(prefix="dub-ref-clean-") as raw:
        whole, sample_rate = _decode(source, Path(raw) / "source.wav")
        candidates: list[tuple[float, float, float, np.ndarray, dict[str, float]]] = []
        for left, right in runs:
            length = right - left
            if length < 2.2:
                continue
            span = min(window, length)
            steps = max(1, int((length - span) / 0.75))
            starts = [
                left + index * (length - span) / steps
                for index in range(steps + 1)
            ]
            for start in starts:
                clip = np.asarray(
                    whole[
                        int(start * sample_rate) :
                        int(min(right, start + span) * sample_rate)
                    ],
                    dtype=np.float32,
                )
                if len(clip) < sample_rate * 2:
                    continue
                pitch = pitch_profile(clip, sample_rate)
                activity = activity_stats(clip, sample_rate)
                score = (
                    pitch["f0_median"] * 0.45
                    + pitch["f0_p90"] * 0.18
                    + activity["max_internal_gap"] * 60
                    + abs(activity["active_ratio"] - 0.72) * 45
                )
                if pitch["voiced_ratio"] < 0.16:
                    score += 120
                candidates.append(
                    (
                        score,
                        start,
                        min(right, start + span),
                        clip,
                        {**pitch, **activity},
                    )
                )
        if not candidates:
            raise RuntimeError("Не найден устойчивый голосовой референс.")

        selected: list[tuple[float, float, float, np.ndarray, dict[str, float]]] = []
        total = 0.0
        for item in sorted(candidates, key=lambda value: value[0]):
            if any(
                min(item[2], existing[2]) - max(item[1], existing[1]) > 0.75
                for existing in selected
            ):
                continue
            selected.append(item)
            total += item[2] - item[1]
            if total >= target:
                break
        if not selected:
            raise RuntimeError("Не удалось собрать непересекающийся voice reference.")

        parts = [item[3] for item in selected]
        audio = dub_quality_v4._crossfade(parts, sample_rate)[: int(target * sample_rate)]
        rms = math.sqrt(float(np.mean(audio**2)) + 1e-12)
        peak = float(np.max(np.abs(audio))) + 1e-12
        # Conservative level bounds keep an unusually quiet source usable without
        # aggressive compression. No denoising or spectral rewriting is applied.
        gain = min(
            10 ** (-24 / 20) / rms,
            10 ** (-3 / 20) / peak,
            10 ** (5 / 20),
        )
        audio = np.clip(audio * gain, -0.999, 0.999).astype(np.float32)
        fade = min(int(sample_rate * 0.025), len(audio) // 8)
        if fade > 1:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, audio, sample_rate, subtype="PCM_24")

    report = {
        "policy": POLICY,
        "denoise": False,
        "selected": [
            {
                "start": round(item[1], 3),
                "end": round(item[2], 3),
                "score": round(item[0], 3),
                **{
                    key: round(float(value), 4)
                    for key, value in item[4].items()
                },
            }
            for item in selected
        ],
    }
    output.with_suffix(".selection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(
        "reference calm windows, denoise=False: "
        + ", ".join(f"{item[1]:.2f}-{item[2]:.2f}" for item in selected)
    )


def verify_timeline_v45(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    failed, report = _ORIGINAL_VERIFY(timeline, segments, report_path)
    failed_ids = set(map(int, failed))
    checks = {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    with tempfile.TemporaryDirectory(prefix="dub-v45-qa-") as raw:
        temp = Path(raw)
        for item in segments:
            segment_id = int(item["id"])
            delay = max(0, int(item.get("start_delay_ms", 0))) / 1000
            clip = temp / f"{segment_id}.wav"
            semantic_tts_guard_v4.legacy._extract_clip(
                timeline,
                clip,
                float(item["start"]) + delay,
                max(0.35, float(item["end"]) - float(item["start"])),
            )
            samples, sample_rate = semantic_tts_guard_v4.legacy._read_pcm_mono(clip)
            stats = activity_stats(np.asarray(samples), int(sample_rate))
            allowed = 0.78 if re.search(r"[.!?…;:]", str(item.get("text") or "")) else 0.58
            passed = stats["max_internal_gap"] <= allowed and stats["active_ratio"] >= 0.20
            check = checks.setdefault(segment_id, {"id": segment_id, "passed": True})
            check["continuity_v45"] = {
                **stats,
                "max_allowed": allowed,
                "passed": passed,
            }
            check["passed"] = bool(check.get("passed") and passed)
            if not check["passed"]:
                failed_ids.add(segment_id)
    result = sorted(failed_ids)
    report.update(
        professional_audio_policy=POLICY,
        passed=not result,
        failed_segment_ids=result,
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result, report
