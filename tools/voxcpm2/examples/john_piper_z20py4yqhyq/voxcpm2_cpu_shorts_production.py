#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable PowerShell/bot CLI for the direct VoxCPM2 max-quality renderer.

``direct_cli_runtime.marker.json`` proves checkpoint compatibility and is written
before synthesis so completed segments remain resumable after a later failure.
``direct_cli_runtime.completed.json`` is a separate transactional success marker
written only after the renderer returns successfully. This prevents a failed
run from being mistaken for a completed render without sacrificing resumability.
"""
from __future__ import annotations

from collections.abc import Callable
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

MARKER_POLICY = "direct-cli-runtime-marker-v2"
SUCCESS_MARKER_POLICY = "direct-cli-success-marker-v1"
_COMPATIBILITY_MARKER = "direct_cli_runtime.marker.json"
_SUCCESS_MARKER = "direct_cli_runtime.completed.json"
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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
        "schema_version": 2,
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
    _write_json_atomic(json_path, payload)
    text_path.write_text(trace.rstrip() + "\n", encoding="utf-8")


def _prepare_runtime_marker() -> tuple[Path, dict[str, Any]]:
    work_dir, expected = _runtime_contract()
    work_dir.mkdir(parents=True, exist_ok=True)
    marker_path = work_dir / _COMPATIBILITY_MARKER
    success_path = work_dir / _SUCCESS_MARKER
    success_path.unlink(missing_ok=True)
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
    _write_json_atomic(marker_path, expected)
    return marker_path, expected


def _commit_success_marker(marker_path: Path, compatibility: dict[str, Any]) -> Path:
    success_path = marker_path.parent / _SUCCESS_MARKER
    payload = {
        "schema_version": 1,
        "policy": SUCCESS_MARKER_POLICY,
        "compatibility": compatibility,
    }
    _write_json_atomic(success_path, payload)
    return success_path


def run(render: Callable[[], Any]) -> Any:
    """Run the renderer with explicit compatibility and success transactions."""
    marker_path, marker_payload = _prepare_runtime_marker()
    work_dir = marker_path.parent
    try:
        result = render()
    except BaseException as exc:
        (work_dir / _SUCCESS_MARKER).unlink(missing_ok=True)
        if isinstance(exc, Exception):
            _write_failure_report(work_dir, exc)
        raise
    _commit_success_marker(marker_path, marker_payload)
    _clear_failure_report(work_dir)
    return result


if __name__ == "__main__":
    try:
        run(main)
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
