#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semantic and timing QA without prompt-continuation or nested VoxCPM retries."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess as _subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from tools.voxcpm2 import semantic_tts_guard as legacy

_GUARD_VERSION = "semantic-tts-guard-v4.1"
_SYNTH_NAME = "voxcpm2_cpu_shorts_production.py"
_MASTER_NAME = "master_constant_mix.py"
_QUALITY_RENDERER = "voxcpm2_quality_v4_renderer.py"
_QUALITY_MASTER = "master_quality_v4.py"
_REAL_SUBPROCESS = _subprocess
_LOCK = threading.RLock()
_INSTALLED = False


def log(message: str) -> None:
    print(f"[TTS-QA-V4] {message}", flush=True)


def _is_named_command(command: Any, filename: str) -> bool:
    return bool(
        isinstance(command, (list, tuple))
        and any(Path(str(part)).name.casefold() == filename.casefold() for part in command)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pipeline_signature(command: Sequence[str], guarded_segments: Path, renderer: Path) -> str:
    extended = Path(legacy._flag_value(command, "--extended-reference")).resolve()
    composite = Path(legacy._flag_value(command, "--composite-reference")).resolve()
    payload = {
        "guard_version": _GUARD_VERSION,
        "segments_sha256": _sha256_file(guarded_segments),
        "extended_sha256": _sha256_file(extended),
        "composite_sha256": _sha256_file(composite),
        "renderer_sha256": _sha256_file(renderer),
        "steps": legacy._flag_value(command, "--steps"),
        "cfg": legacy._flag_value(command, "--cfg"),
        "cache_length": legacy._flag_value(command, "--cache-length"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _invalidate_stale_checkpoints(work_dir: Path, pipeline_signature: str) -> None:
    marker = work_dir / "semantic_guard.marker.json"
    current: dict[str, Any] | None = None
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8-sig"))
            current = payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            current = None
    if (
        current
        and current.get("guard_version") == _GUARD_VERSION
        and current.get("pipeline_signature") == pipeline_signature
    ):
        return
    for path in (work_dir / "checkpoints").glob("segment_*.json"):
        path.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    log("stale checkpoints invalidated: text/reference/renderer signature changed")


def _retarget(
    work_dir: Path,
    *,
    good_ids: Iterable[int],
    failed_ids: Iterable[int],
    new_base_seed: int,
) -> None:
    legacy._retarget_checkpoints(
        work_dir,
        good_ids=good_ids,
        failed_ids=failed_ids,
        new_base_seed=new_base_seed,
    )


def _frame_rms(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray, int]:
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    frame = max(64, int(sample_rate * 0.020))
    hop = max(32, int(sample_rate * 0.010))
    starts = np.arange(0, max(1, len(audio) - frame + 1), hop, dtype=np.int64)
    levels = np.asarray(
        [math.sqrt(float(np.mean(audio[start : start + frame] ** 2)) + 1e-12) for start in starts],
        dtype=np.float64,
    )
    return levels, starts, frame


def _sustained_index(active: np.ndarray, *, reverse: bool = False) -> int | None:
    indices = range(len(active) - 1, -1, -1) if reverse else range(len(active))
    for index in indices:
        left = max(0, index - 2)
        right = min(len(active), index + 3)
        if active[index] and int(np.count_nonzero(active[left:right])) >= 3:
            return int(index)
    return None


def measure_timing_quality(
    samples: np.ndarray,
    sample_rate: int,
    *,
    max_onset_ms: int = 220,
    min_trailing_ms: int = 45,
) -> dict[str, Any]:
    """Reject late starts, clipped endings and isolated pre-speech clicks."""
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not len(audio) or sample_rate <= 0:
        return {"passed": False, "reason": "empty_audio"}
    levels, starts, frame = _frame_rms(audio, sample_rate)
    peak_db = 20.0 * math.log10(float(np.max(levels)) + 1e-12)
    threshold_db = max(-49.0, peak_db - 33.0)
    level_db = 20.0 * np.log10(levels + 1e-12)
    active = level_db >= threshold_db
    first_index = _sustained_index(active)
    last_index = _sustained_index(active, reverse=True)
    if first_index is None or last_index is None or last_index < first_index:
        return {"passed": False, "reason": "no_sustained_speech"}

    speech_start = int(starts[first_index])
    speech_end = min(len(audio), int(starts[last_index]) + frame)
    onset_ms = speech_start * 1000.0 / sample_rate
    trailing_ms = (len(audio) - speech_end) * 1000.0 / sample_rate

    pre_end = max(0, speech_start - int(sample_rate * 0.012))
    pre = audio[:pre_end]
    pre_peak = float(np.max(np.abs(pre))) if len(pre) else 0.0
    pre_step = float(np.max(np.abs(np.diff(pre)))) if len(pre) > 1 else 0.0
    speech_probe = audio[speech_start : min(len(audio), speech_start + int(sample_rate * 0.30))]
    speech_rms = math.sqrt(float(np.mean(speech_probe**2)) + 1e-12) if len(speech_probe) else 0.0
    isolated_artifact = bool(
        len(pre)
        and (
            (pre_step > 0.30 and pre_peak > 0.12)
            or (onset_ms >= 90.0 and pre_peak > max(0.18, speech_rms * 3.2))
        )
    )
    passed = bool(
        onset_ms <= float(max_onset_ms)
        and trailing_ms >= float(min_trailing_ms)
        and not isolated_artifact
    )
    return {
        "passed": passed,
        "onset_ms": round(onset_ms, 3),
        "trailing_ms": round(trailing_ms, 3),
        "max_onset_ms": int(max_onset_ms),
        "min_trailing_ms": int(min_trailing_ms),
        "pre_peak": round(pre_peak, 6),
        "pre_max_step": round(pre_step, 6),
        "speech_probe_rms": round(speech_rms, 6),
        "isolated_start_artifact": isolated_artifact,
    }


def verify_timeline_v4(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    failed, report = legacy.verify_timeline(timeline, segments, report_path)
    failed_set = {int(value) for value in failed}
    checks_by_id = {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    max_onset_ms = max(80, min(500, int(os.getenv("DUB_TTS_MAX_ONSET_MS", "220") or "220")))
    min_trailing_ms = max(20, min(300, int(os.getenv("DUB_TTS_MIN_TRAILING_MS", "45") or "45")))

    with tempfile.TemporaryDirectory(prefix="tts-timing-qa-v4-") as temp_raw:
        temp = Path(temp_raw)
        for item in segments:
            segment_id = int(item["id"])
            delay = max(0, int(item.get("start_delay_ms", 0))) / 1000.0
            start = float(item["start"]) + delay
            duration = max(0.35, float(item["end"]) - float(item["start"]))
            clip = temp / f"segment_{segment_id:03d}.wav"
            legacy._extract_clip(timeline, clip, start, duration)
            samples, sample_rate = legacy._read_pcm_mono(clip)
            timing = measure_timing_quality(
                samples,
                sample_rate,
                max_onset_ms=max_onset_ms,
                min_trailing_ms=min_trailing_ms,
            )
            check = checks_by_id.setdefault(segment_id, {"id": segment_id, "passed": True})
            check["timing"] = timing
            check["passed"] = bool(check.get("passed") and timing.get("passed"))
            if not check["passed"]:
                failed_set.add(segment_id)

    failed_ids = sorted(failed_set)
    report["guard_version"] = _GUARD_VERSION
    report["passed"] = not failed_ids
    report["failed_segment_ids"] = failed_ids
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return failed_ids, report


def _run_quality_synth(command: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    original_command = [str(part) for part in command]
    env = dict(kwargs.get("env") or os.environ)
    work_dir = Path(legacy._flag_value(original_command, "--work-dir")).resolve()
    timeline = Path(legacy._flag_value(original_command, "--output")).resolve()
    source_segments = Path(legacy._flag_value(original_command, "--segments-json")).resolve()
    base_seed = int(legacy._flag_value(original_command, "--base-seed"))

    guard_dir = work_dir / "semantic_guard_v4"
    guard_dir.mkdir(parents=True, exist_ok=True)
    guarded_segments = guard_dir / "segments_guarded.json"
    segments = legacy._prepare_guarded_segments(source_segments, guarded_segments)

    renderer = Path(__file__).resolve().parent / _QUALITY_RENDERER
    if not renderer.is_file():
        raise RuntimeError(f"Не найден Quality v4 renderer: {renderer}")
    rewritten = list(original_command)
    original_renderer: Path | None = None
    for index, part in enumerate(rewritten):
        if Path(part).name.casefold() == _SYNTH_NAME.casefold():
            original_renderer = Path(part).resolve()
            rewritten[index] = str(renderer)
            break
    if original_renderer is None:
        raise RuntimeError("Не найден исходный NoChew renderer в команде.")
    legacy._replace_flag(rewritten, "--segments-json", str(guarded_segments))

    env.pop("VOXCPM_PROMPT_TEXTS_JSON", None)
    env["VOXCPM_ORIGINAL_RENDERER"] = str(original_renderer)
    env["VOXCPM_SEMANTIC_GUARD_VERSION"] = _GUARD_VERSION
    kwargs["env"] = env

    pipeline_signature = _pipeline_signature(original_command, guarded_segments, renderer)
    _invalidate_stale_checkpoints(work_dir, pipeline_signature)
    max_rounds = max(1, min(4, int(os.getenv("DUB_TTS_QA_MAX_ROUNDS", "3") or "3")))
    all_ids = {int(item["id"]) for item in segments}
    last_report: dict[str, Any] = {}

    for round_index in range(max_rounds):
        round_seed = base_seed + round_index * 100_000
        legacy._replace_flag(rewritten, "--base-seed", str(round_seed))
        log(f"reference-only NoChew, QA round {round_index + 1}/{max_rounds}, seed={round_seed}")
        result = _REAL_SUBPROCESS.run(rewritten, *args, **kwargs)
        if int(getattr(result, "returncode", 1)) != 0:
            return result
        report_path = timeline.with_suffix(f".semantic_qa_v4.round{round_index + 1}.json")
        failed, last_report = verify_timeline_v4(timeline, segments, report_path)
        if not failed:
            (work_dir / "semantic_guard.marker.json").write_text(
                json.dumps(
                    {
                        "guard_version": _GUARD_VERSION,
                        "base_seed": round_seed,
                        "pipeline_signature": pipeline_signature,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            timeline.with_suffix(".semantic_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log("все реплики прошли акустическую, семантическую и timing-проверку")
            return result
        log(f"не прошли реплики {failed}; повторяю только их с новым seed")
        next_seed = base_seed + (round_index + 1) * 100_000
        _retarget(
            work_dir,
            good_ids=all_ids - set(failed),
            failed_ids=failed,
            new_base_seed=next_seed,
        )

    timeline.with_suffix(".semantic_qa.json").write_text(
        json.dumps(last_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    raise RuntimeError(
        "VoxCPM2 Quality v4.1 не прошёл проверку после "
        f"{max_rounds} раундов. Сегменты: {last_report.get('failed_segment_ids', [])}."
    )


def _run_quality_master(command: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    master = Path(__file__).resolve().parent / _QUALITY_MASTER
    if not master.is_file():
        raise RuntimeError(f"Не найден Quality v4 master: {master}")
    rewritten = [str(part) for part in command]
    for index, part in enumerate(rewritten):
        if Path(part).name.casefold() == _MASTER_NAME.casefold():
            rewritten[index] = str(master)
            break
    log("master: Russian-first loudness; exact source gain; no whole-mix loudnorm")
    return _REAL_SUBPROCESS.run(rewritten, *args, **kwargs)


class QualityV4SubprocessProxy:
    def __init__(self, real: Any) -> None:
        self._real = real

    def run(self, command: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_named_command(command, _SYNTH_NAME):
            return _run_quality_synth(command, *args, **kwargs)
        if _is_named_command(command, _MASTER_NAME):
            return _run_quality_master(command, *args, **kwargs)
        return self._real.run(command, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def install() -> None:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        proxy = QualityV4SubprocessProxy(_REAL_SUBPROCESS)
        try:
            import tools.voxcpm2.generic_short_production as pipeline

            pipeline.subprocess = proxy
        except Exception as exc:
            log(f"legacy pipeline patch failed: {type(exc).__name__}: {exc}")

        for module in list(sys.modules.values()):
            if module is None:
                continue
            file_name = Path(str(getattr(module, "__file__", "") or "")).name.casefold()
            if file_name not in {"generic_project_runtime.py", "generic_direct_runtime.py"}:
                continue
            if hasattr(module, "subprocess"):
                setattr(module, "subprocess", proxy)
        _INSTALLED = True
        log("Quality v4.1 guard installed for Gemini MAX and ready SRT")


__all__ = [
    "QualityV4SubprocessProxy",
    "install",
    "measure_timing_quality",
    "verify_timeline_v4",
]
