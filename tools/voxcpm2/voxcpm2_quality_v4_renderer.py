#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality v4.2 entrypoint around the proven reference-only NoChew renderer.

It deliberately does not patch VoxCPM.generate. The underlying renderer keeps
its original min_len=2, retry_badcase=False and requested CFG. This adapter
scores unstable starts and normalizes candidate edge silence before timeline fit.
"""
from __future__ import annotations

import json
import math
import os
import re
import runpy
import sys
from pathlib import Path
from typing import Any, Callable

# This file is executed by the separate VoxCPM CPU interpreter as a script, not
# as ``python -m``. In that mode Python adds tools/voxcpm2 to sys.path but may
# omit the repository root, so absolute ``tools.voxcpm2`` imports fail before
# the model is even loaded. Make the file entrypoint independent of cwd and of
# any caller-specific PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import soundfile as sf

from tools.voxcpm2.activity_quality import sustained_activity_index

_QUALITY_VERSION = "voxcpm2-quality-v4.2"
_PROGRESS_PREFIX = "DUB_PROGRESS "
_SEGMENT_LINE_RE = re.compile(r"^\[(\d+)\s*/\s*(\d+)\]")
_ATTEMPT_LINE_RE = re.compile(r"^attempt\s+(\d+):", flags=re.I)
_REQUIRED_RENDER_HOOKS = (
    "candidate_score",
    "fit_without_slowdown",
    "log",
    "set_seed",
)


def log(message: str) -> None:
    print(f"[VOXCPM2-QUALITY-V4] {message}", flush=True)


def _required_callable(namespace: dict[str, Any], name: str) -> Callable[..., Any]:
    value = namespace.get(name)
    if not callable(value):
        raise RuntimeError(
            f"Исходный NoChew renderer не экспортирует обязательную функцию {name!r}."
        )
    return value


def _has_render_hooks(namespace: object) -> bool:
    return isinstance(namespace, dict) and all(
        callable(namespace.get(name)) for name in _REQUIRED_RENDER_HOOKS
    )


def _execution_globals(namespace: dict[str, Any]) -> dict[str, Any]:
    """Find globals used by the underlying renderer through wrapper closures.

    Active production wraps ``main`` for failure recovery. Its immediate
    ``__globals__`` therefore belongs to the wrapper module, while the captured
    original function owns candidate scoring, fitting, logging and seeding.
    Traverse callable closure cells fail-closed instead of patching a dictionary
    that the renderer never reads.
    """
    main_hook = _required_callable(namespace, "main")
    queue: list[Callable[..., Any]] = [main_hook]
    seen: set[int] = set()
    while queue:
        hook = queue.pop(0)
        identity = id(hook)
        if identity in seen:
            continue
        seen.add(identity)
        globals_dict = getattr(hook, "__globals__", None)
        if _has_render_hooks(globals_dict):
            return globals_dict
        closure = getattr(hook, "__closure__", None) or ()
        for cell in closure:
            try:
                captured = cell.cell_contents
            except ValueError:
                continue
            if callable(captured):
                queue.append(captured)
    if _has_render_hooks(namespace):
        return namespace
    missing = [
        name for name in _REQUIRED_RENDER_HOOKS if not callable(namespace.get(name))
    ]
    raise RuntimeError(
        "Не найден active renderer namespace с обязательными hooks: "
        + ", ".join(missing)
    )


def _emit_progress(progress: int, stage: str, message: str) -> None:
    """Emit a machine-readable progress line while remaining human-readable."""
    payload = {
        "progress": max(0, min(int(progress), 99)),
        "stage": str(stage)[:160],
        "message": str(message)[:800],
    }
    print(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


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


def _sustained_activity(active: np.ndarray, *, from_start: bool) -> int | None:
    return sustained_activity_index(active, reverse=not from_start)


def _activity_bounds(samples: np.ndarray, sample_rate: int) -> tuple[int, int] | None:
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if len(audio) < int(sample_rate * 0.20):
        return None
    levels, starts, frame = _frame_rms(audio, sample_rate)
    peak_db = 20.0 * math.log10(float(np.max(levels)) + 1e-12)
    threshold_db = max(-49.0, peak_db - 33.0)
    active = 20.0 * np.log10(levels + 1e-12) >= threshold_db
    first_index = _sustained_activity(active, from_start=True)
    last_index = _sustained_activity(active, from_start=False)
    if first_index is None or last_index is None or last_index < first_index:
        return None
    return int(starts[first_index]), min(len(audio), int(starts[last_index]) + frame)


def trim_candidate_edges(
    samples: np.ndarray,
    sample_rate: int,
    *,
    pre_roll: float = 0.065,
    post_roll: float = 0.140,
) -> tuple[np.ndarray, dict[str, float]]:
    """Remove variable model silence/chirps while preserving a fixed natural edge."""
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    bounds = _activity_bounds(audio, sample_rate)
    if bounds is None:
        return audio, {"trimmed_leading": 0.0, "trimmed_trailing": 0.0}
    speech_start, speech_end = bounds
    cut_start = max(0, speech_start - int(sample_rate * pre_roll))
    cut_end = min(len(audio), speech_end + int(sample_rate * post_roll))
    if cut_end - cut_start < int(sample_rate * 0.22):
        return audio, {"trimmed_leading": 0.0, "trimmed_trailing": 0.0}

    result = audio[cut_start:cut_end].copy()
    fade = min(int(sample_rate * 0.008), len(result) // 8)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        result[:fade] *= ramp
        result[-fade:] *= ramp[::-1]
    return result, {
        "trimmed_leading": cut_start / sample_rate,
        "trimmed_trailing": (len(audio) - cut_end) / sample_rate,
        "speech_preroll": pre_roll,
        "speech_postroll": post_roll,
    }


def _initial_artifact_score(candidate: dict[str, Any]) -> float:
    audio = np.asarray(candidate.get("samples"), dtype=np.float32).reshape(-1)
    sample_rate = int(candidate.get("sample_rate") or 0)
    if sample_rate <= 0 or len(audio) < int(sample_rate * 0.20):
        return 120.0
    bounds = _activity_bounds(audio, sample_rate)
    if bounds is None:
        return 140.0
    speech_start, _ = bounds
    onset = speech_start / sample_rate
    pre_end = max(0, speech_start - int(sample_rate * 0.012))
    pre = audio[:pre_end]
    speech_probe = audio[speech_start : min(len(audio), speech_start + int(sample_rate * 0.30))]
    speech_rms = math.sqrt(float(np.mean(speech_probe**2)) + 1e-12) if len(speech_probe) else 0.0
    pre_peak = float(np.max(np.abs(pre))) if len(pre) else 0.0
    pre_step = float(np.max(np.abs(np.diff(pre)))) if len(pre) > 1 else 0.0
    pre_rms = math.sqrt(float(np.mean(pre**2)) + 1e-12) if len(pre) else 0.0
    pre_zcr = (
        float(np.mean(np.signbit(pre[1:]) != np.signbit(pre[:-1])))
        if len(pre) > 1
        else 0.0
    )

    score = 0.0
    if onset > 0.14:
        score += 24.0 + (onset - 0.14) * 95.0
    if pre_step > 0.30 and pre_peak > 0.12:
        score += 95.0 + min(35.0, pre_step * 20.0)
    elif (
        onset >= 0.09
        and pre_rms > 0.006
        and pre_zcr > 0.22
        and pre_peak > max(0.08, speech_rms * 1.45)
    ):
        score += 60.0 + min(30.0, pre_zcr * 50.0)
    return score


def main() -> None:
    original = Path(os.environ.get("VOXCPM_ORIGINAL_RENDERER", "")).expanduser().resolve()
    if not original.is_file():
        raise RuntimeError(f"Исходный NoChew renderer не найден: {original}")

    namespace = runpy.run_path(str(original), run_name="voxcpm2_quality_v4_base")
    base_main = _required_callable(namespace, "main")
    base_globals = _execution_globals(namespace)
    original_score = _required_callable(base_globals, "candidate_score")
    original_fit = _required_callable(base_globals, "fit_without_slowdown")
    original_log = _required_callable(base_globals, "log")
    original_set_seed = _required_callable(base_globals, "set_seed")
    progress_state = {"position": 0, "total": 0, "attempt": 0}

    def quality_score(
        candidate: dict[str, Any],
        speech_slot: float,
        *extra: Any,
    ) -> float:
        return float(original_score(candidate, speech_slot, *extra)) + _initial_artifact_score(candidate)

    def quality_fit(
        clean_path: Path,
        fitted_path: Path,
        target_duration: float,
        tail_guard: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        samples, sample_rate = sf.read(clean_path, dtype="float32")
        if np.asarray(samples).ndim > 1:
            samples = np.asarray(samples, dtype=np.float32).mean(axis=1)
        trimmed, trim_report = trim_candidate_edges(np.asarray(samples), int(sample_rate))
        sf.write(clean_path, trimmed, int(sample_rate), subtype="PCM_24")
        report = dict(
            original_fit(
                clean_path,
                fitted_path,
                target_duration,
                tail_guard,
                **kwargs,
            )
        )
        report.update(trim_report)
        report["quality_version"] = _QUALITY_VERSION
        return report

    def progress_log(message: str) -> None:
        original_log(message)
        text = str(message or "").strip()
        lowered = text.casefold()
        if "voxcpm2 final production cpu render" in lowered:
            _emit_progress(6, "Загрузка VoxCPM2", "Локальная модель загружается в память CPU")
            return
        if "модель загружена за" in lowered:
            _emit_progress(10, "VoxCPM2 загружен", text)
            return

        segment_match = _SEGMENT_LINE_RE.match(text)
        if segment_match:
            position = max(1, int(segment_match.group(1)))
            total = max(position, int(segment_match.group(2)))
            progress_state.update(position=position, total=total, attempt=0)
            restored = "checkpoint" in lowered
            fraction = position / total if restored else (position - 1) / total
            progress = 10 + round(fraction * 78)
            stage = f"Реплика {position}/{total}"
            detail = "готова из checkpoint" if restored else "подготовка вариантов голоса"
            _emit_progress(progress, stage, f"{stage}: {detail}")
            return

        attempt_match = _ATTEMPT_LINE_RE.match(text)
        if attempt_match and progress_state["total"]:
            attempt = max(1, int(attempt_match.group(1)))
            progress_state["attempt"] = attempt
            position = int(progress_state["position"])
            total = int(progress_state["total"])
            fraction = ((position - 1) + min(attempt, 3) / 3.0 * 0.82) / total
            progress = 10 + round(fraction * 78)
            stage = f"Реплика {position}/{total}, вариант {attempt} готов"
            _emit_progress(progress, stage, text)
            return

        if lowered.startswith("выбран attempt") and progress_state["total"]:
            position = int(progress_state["position"])
            total = int(progress_state["total"])
            progress = 10 + round(position / total * 78)
            stage = f"Реплика {position}/{total} прошла локальную обработку"
            _emit_progress(progress, stage, text)
            return
        if "final synthesis готов" in lowered:
            _emit_progress(92, "Русская дорожка собрана", "Переход к акустической QA и master")

    def progress_set_seed(seed: int, torch_module: Any) -> None:
        progress_state["attempt"] = int(progress_state.get("attempt") or 0) + 1
        position = int(progress_state.get("position") or 0)
        total = int(progress_state.get("total") or 0)
        attempt = int(progress_state["attempt"])
        if position > 0 and total > 0:
            fraction = ((position - 1) + min(attempt, 3) / 3.0 * 0.42) / total
            progress = 10 + round(fraction * 78)
            stage = f"Реплика {position}/{total}, генерация варианта {attempt}"
            _emit_progress(progress, stage, "CPU выполняет VoxCPM2-синтез; процесс может молчать несколько минут")
        original_set_seed(seed, torch_module)

    base_globals["candidate_score"] = quality_score
    base_globals["fit_without_slowdown"] = quality_fit
    base_globals["log"] = progress_log
    base_globals["set_seed"] = progress_set_seed
    log("reference-only NoChew; requested CFG preserved; nested retry remains disabled")
    base_main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        import traceback

        print(f"ОШИБКА QUALITY V4.2: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
