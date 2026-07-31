#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable PowerShell/bot CLI for the direct VoxCPM2 max-quality renderer.

The entrypoint maintains a renderer-runtime marker in the work directory. Old
checkpoints are removed when the executable modules, model snapshot, CPU-venv
VoxCPM runtime or cache-length contract changes. This protects both bot and
manual PowerShell launches without patching VoxCPM internals.
"""
from __future__ import annotations

import json
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import direct_max_quality_cli as _direct_cli
from tools.voxcpm2.direct_max_quality_io import (
    MAX_TEMPO as HARD_MAX_TEMPO,
    PREFERRED_MAX_TEMPO,
)

# A candidate above the preferred ceiling must trigger another synthesis attempt
# instead of being accepted merely because its acoustic score is good. The tiny
# hard margin preserves the validated atempo=1.358 boundary case while anything
# materially faster still fails closed.
_ORIGINAL_CANDIDATE_SCORE = _direct_cli.candidate_score


def _tempo_policy_penalty(duration: float, speech_slot: float) -> float:
    ratio = float(duration) / max(0.1, float(speech_slot))
    if ratio <= PREFERRED_MAX_TEMPO:
        return 0.0
    return 90.0 + (ratio - PREFERRED_MAX_TEMPO) * 400.0


def _fit_aware_candidate_score(
    candidate: dict[str, Any],
    speech_slot: float,
    reference_voice: dict[str, Any],
) -> float:
    base = float(_ORIGINAL_CANDIDATE_SCORE(candidate, speech_slot, reference_voice))
    penalty = _tempo_policy_penalty(
        float(candidate.get("duration") or 0.0),
        float(speech_slot),
    )
    candidate["tempo_preference_penalty"] = float(penalty)
    candidate["required_tempo_estimate"] = float(candidate.get("duration") or 0.0) / max(
        0.1,
        float(speech_slot),
    )
    return base + penalty


_direct_cli.candidate_score = _fit_aware_candidate_score
_direct_cli.MAX_TEMPO = HARD_MAX_TEMPO
main = _direct_cli.main

MARKER_POLICY = "direct-cli-runtime-marker-v1"
_FAILURE_JSON = "direct_renderer_failure.json"
_FAILURE_TEXT = "direct_renderer_failure.txt"


def _flag(flag: str, *, default: str | None = None) -> str:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        if default is not None:
            return default
        raise RuntimeError(f"В direct CLI отсутствует обязательный параметр {flag}.")
    if index + 1 >= len(sys.argv):
        raise RuntimeError(f"После {flag} отсутствует значение.")
    return str(sys.argv[index + 1])


def _read_marker(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _clear_checkpoint_state(work_dir: Path) -> None:
    for name in ("checkpoints", "segments_clean", "segments_fitted", "attempts"):
        target = work_dir / name
        if target.is_dir():
            shutil.rmtree(target)


def _runtime_contract() -> tuple[Path, dict[str, Any]]:
    work_dir = Path(_flag("--work-dir")).resolve()
    archive = Path(_flag("--archive-root")).resolve()
    speech_backend = _flag("--speech-backend", default="voxcpm2")
    try:
        cache_length = int(_flag("--cache-length", default="4096"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Некорректный --cache-length.") from exc
    if cache_length < 2048 or cache_length > 131072:
        raise RuntimeError("--cache-length должен быть в диапазоне 2048..131072.")
    fingerprints = clean_runtime_contract.build_fingerprints(
        repo=REPO_ROOT,
        archive=archive,
        cpu_python=Path(sys.executable).resolve(),
        backend_id=speech_backend,
    )
    return work_dir, {
        "schema_version": 1,
        "policy": MARKER_POLICY,
        "speech_backend": speech_backend,
        "render_contract_sha256": fingerprints["render_contract_sha256"],
        "cache_length": cache_length,
        "python_executable": str(Path(sys.executable).resolve()),
    }


def _failure_paths(work_dir: Path) -> tuple[Path, Path]:
    return work_dir / _FAILURE_JSON, work_dir / _FAILURE_TEXT


def _clear_failure_report(work_dir: Path) -> None:
    for path in _failure_paths(work_dir):
        path.unlink(missing_ok=True)


def _write_failure_report(work_dir: Path, exc: BaseException) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    payload = {
        "schema_version": 1,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": trace,
    }
    json_path, text_path = _failure_paths(work_dir)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text_path.write_text(trace.rstrip() + "\n", encoding="utf-8")


def _prepare_runtime_marker() -> tuple[Path, dict[str, Any]]:
    work_dir, expected = _runtime_contract()
    work_dir.mkdir(parents=True, exist_ok=True)
    marker_path = work_dir / "direct_cli_runtime.marker.json"
    current = _read_marker(marker_path)
    checkpoints_exist = any((work_dir / "checkpoints").glob("segment_*.json"))
    if checkpoints_exist and current != expected:
        print(
            "[DIRECT-CLI] renderer/model/runtime fingerprint изменился; "
            "старые checkpoints удалены",
            flush=True,
        )
        _clear_checkpoint_state(work_dir)
    _clear_failure_report(work_dir)
    # Маркер описывает совместимость checkpoints, а не факт успешного завершения.
    # Пишем его до синтеза: если поздний сегмент упадёт, уже готовые сегменты
    # останутся безопасно возобновляемыми при следующем запуске.
    marker_path.write_text(
        json.dumps(expected, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return marker_path, expected


if __name__ == "__main__":
    marker_path: Path | None = None
    marker_payload: dict[str, Any] | None = None
    work_dir: Path | None = None
    try:
        marker_path, marker_payload = _prepare_runtime_marker()
        work_dir = marker_path.parent
        main()
        marker_path.write_text(
            json.dumps(marker_payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        _clear_failure_report(work_dir)
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        if work_dir is None:
            try:
                work_dir = Path(_flag("--work-dir")).resolve()
            except Exception:
                work_dir = None
        if work_dir is not None:
            try:
                _write_failure_report(work_dir, exc)
            except Exception as report_exc:
                print(f"Не удалось сохранить failure report: {report_exc}", file=sys.stderr)
        # Runtime marker intentionally remains: it proves checkpoint compatibility.
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
