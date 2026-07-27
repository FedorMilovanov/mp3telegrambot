#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semantic QA v4 without prompt-continuation or nested VoxCPM retries."""
from __future__ import annotations

import json
import os
import subprocess as _subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.voxcpm2 import semantic_tts_guard as legacy

_GUARD_VERSION = "semantic-tts-guard-v4"
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


def _invalidate_v3_checkpoints(work_dir: Path) -> None:
    marker = work_dir / "semantic_guard.marker.json"
    current: dict[str, Any] | None = None
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8-sig"))
            current = payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            current = None
    if current and current.get("guard_version") == _GUARD_VERSION:
        return
    for path in (work_dir / "checkpoints").glob("segment_*.json"):
        path.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)


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

    # Quality v4 intentionally preserves the references built by dub_quality_v4.
    # No FFT denoise, no ASR prompt transcript, no forced CFG and no internal retry.
    env.pop("VOXCPM_PROMPT_TEXTS_JSON", None)
    env["VOXCPM_ORIGINAL_RENDERER"] = str(original_renderer)
    env["VOXCPM_SEMANTIC_GUARD_VERSION"] = _GUARD_VERSION
    kwargs["env"] = env

    _invalidate_v3_checkpoints(work_dir)
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
        failed, last_report = legacy.verify_timeline(timeline, segments, report_path)
        if not failed:
            (work_dir / "semantic_guard.marker.json").write_text(
                json.dumps(
                    {"guard_version": _GUARD_VERSION, "base_seed": round_seed},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            timeline.with_suffix(".semantic_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log("все реплики прошли акустическую и семантическую проверку")
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
        "VoxCPM2 Quality v4 не прошёл проверку после "
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
        log("Quality v4 guard installed for Gemini MAX and ready SRT")


__all__ = ["QualityV4SubprocessProxy", "install"]
