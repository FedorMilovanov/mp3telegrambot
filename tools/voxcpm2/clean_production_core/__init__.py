#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict compatibility facade for clean production orchestration.

The proven orchestration remains in ``clean_production_core.py``. This package
preserves its public API, validates segment fields before expensive work, makes
child-Python launches independent of cwd, preserves master stderr, and verifies
the actual encoded final Russian ending before a production project is released.
"""
from __future__ import annotations

import importlib.util
import math
import os
import re
import subprocess as _stdlib_subprocess
from pathlib import Path
from typing import Any

from tools.voxcpm2 import final_encoded_delivery_qa

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "clean_production_core.py"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._clean_production_core_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean production core: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

CHILD_PYTHON_POLICY = "repo-root-pythonpath-master-stderr-and-post-aac-v2"


def _child_python_env(value: Any) -> dict[str, str]:
    """Return an isolated environment with the repository import root first."""
    if value is None:
        env = dict(os.environ)
    elif isinstance(value, dict):
        env = {str(key): str(item) for key, item in value.items()}
    else:
        raise RuntimeError("subprocess env должен быть словарём или None.")

    repo = str(_REPO_ROOT)
    existing = str(env.get("PYTHONPATH") or "")
    parts = [item for item in existing.split(os.pathsep) if item]
    normalized = {os.path.normcase(os.path.abspath(item)) for item in parts}
    if os.path.normcase(os.path.abspath(repo)) not in normalized:
        parts.insert(0, repo)
    else:
        parts = [repo] + [
            item
            for item in parts
            if os.path.normcase(os.path.abspath(item))
            != os.path.normcase(os.path.abspath(repo))
        ]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _is_python_script_command(command: Any) -> bool:
    if not isinstance(command, (list, tuple)) or len(command) < 2:
        return False
    executable = Path(str(command[0])).name.casefold()
    script = str(command[1]).casefold()
    return executable.startswith("python") and script.endswith(".py")


def _is_master_command(command: Any) -> bool:
    return bool(
        _is_python_script_command(command)
        and Path(str(command[1])).name.casefold() == "master_constant_mix.py"
    )


def _is_master_release_command(command: Any) -> bool:
    """Distinguish a real production master from --help/import smoke tests."""
    if not _is_master_command(command) or not isinstance(command, (list, tuple)):
        return False
    values = [str(item) for item in command]
    if "--help" in values or "-h" in values:
        return False
    for flag in ("--work-dir", "--russian-only-video"):
        if flag not in values:
            return False
        index = values.index(flag)
        if index + 1 >= len(values) or not values[index + 1].strip():
            return False
    return True


def _command_flag(command: Any, flag: str) -> str:
    if not isinstance(command, (list, tuple)):
        raise RuntimeError("Master command должен быть списком аргументов.")
    values = [str(item) for item in command]
    try:
        index = values.index(flag)
    except ValueError as exc:
        raise RuntimeError(f"Master command не содержит {flag}.") from exc
    if index + 1 >= len(values) or not values[index + 1].strip():
        raise RuntimeError(f"Master command не содержит значение после {flag}.")
    return values[index + 1]


def _verify_post_aac_master_output(command: Any) -> dict[str, Any]:
    russian_only = Path(_command_flag(command, "--russian-only-video")).resolve()
    work_dir = Path(_command_flag(command, "--work-dir")).resolve()
    output_dir = russian_only.parent
    if output_dir.name.casefold() != "output":
        raise RuntimeError(
            "Russian-only MP4 должен находиться в стандартной project/output папке."
        )
    project_root = output_dir.parent
    return final_encoded_delivery_qa.verify_final_encoded_russian(
        russian_only_video=russian_only,
        segments_path=project_root / "segments_ru_final.json",
        report_path=work_dir / "final_encoded_delivery_qa.json",
        final_media_report_path=work_dir / "final_media_verification.json",
    )


def _run_child_process(command: Any, *args: Any, **kwargs: Any):
    """Run child commands with deterministic imports and fail-closed release QA."""
    is_python = _is_python_script_command(command)
    is_master = _is_master_command(command)
    is_master_release = _is_master_release_command(command)
    if is_python:
        kwargs["env"] = _child_python_env(kwargs.get("env"))
    if is_master and kwargs.get("stderr") is None:
        kwargs["stderr"] = _stdlib_subprocess.PIPE
        kwargs.setdefault("text", True)
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")

    result = _stdlib_subprocess.run(command, *args, **kwargs)
    if is_master and int(getattr(result, "returncode", 0) or 0) != 0:
        detail = str(getattr(result, "stderr", "") or "").strip()
        if detail:
            detail = detail[-12000:]
        else:
            detail = f"process exited with code {result.returncode} without stderr"
        raise RuntimeError("Прямой master завершился с точной причиной:\n" + detail)
    if is_master_release:
        try:
            _verify_post_aac_master_output(command)
        except Exception as exc:
            raise RuntimeError(
                "Прямой master создал файлы, но post-AAC ending/tail QA их отклонил:\n"
                + str(exc)
            ) from exc
    return result


class _SubprocessProxy:
    """Module-like proxy scoped to the legacy clean-core module only."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_stdlib_subprocess, name)

    @staticmethod
    def run(command: Any, *args: Any, **kwargs: Any):
        return _run_child_process(command, *args, **kwargs)


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result


def _strict_int(
    value: Any,
    *,
    field: str,
    low: int,
    high: int,
) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        raise RuntimeError(f"{field} должен быть целым числом.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{field}={result} вне диапазона {low}..{high}.")
    return result


def _mark_and_validate_segments(
    segments: list[dict[str, Any]],
    duration: float,
) -> None:
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration должен быть > 0.")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Список реплик перед VoxCPM пуст или повреждён.")

    previous_end = 0.0
    previous_effective_end = 0.0
    seen_ids: set[int] = set()
    for position, item in enumerate(segments, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"segment[{position}] должен быть JSON-объектом, "
                f"получено {type(item).__name__}."
            )
        segment_id = _strict_int(
            item.get("id"),
            field=f"segment[{position}].id",
            low=1,
            high=2**31 - 1,
        )
        if segment_id in seen_ids:
            raise RuntimeError(f"Повторный ID реплики: {segment_id}.")
        seen_ids.add(segment_id)
        item["id"] = segment_id
        item["production_policy"] = POLICY

        start = _finite(item.get("start"), field=f"segment[{segment_id}].start")
        end = _finite(item.get("end"), field=f"segment[{segment_id}].end")
        delay_ms = _strict_int(
            item.get("start_delay_ms", 0),
            field=f"segment[{segment_id}].start_delay_ms",
            low=0,
            high=1500,
        )
        item["start_delay_ms"] = delay_ms
        delay = delay_ms / 1000.0
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if start < 0.0 or not text or end <= start:
            raise RuntimeError(f"Некорректная реплика #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Реплика #{segment_id} пересекается с предыдущей.")
        effective_start = start + delay
        effective_end = end + delay
        if effective_start < previous_effective_end - 0.001:
            raise RuntimeError(
                f"Реплика #{segment_id} пересекается после применения delay."
            )
        if effective_end > duration_value + 0.02:
            raise RuntimeError(f"Реплика #{segment_id} выходит за конец видео.")
        if end - start > MAX_SECONDS + 0.30:
            raise RuntimeError(
                f"Реплика #{segment_id} слишком длинная: {end - start:.3f} сек."
            )
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        rate = words / max(0.35, end - start)
        if rate > 6.2:
            raise RuntimeError(
                f"Реплика #{segment_id} физически перегружена: {rate:.2f} слова/с."
            )
        item["start"] = start
        item["end"] = end
        item["text"] = text
        previous_end = end
        previous_effective_end = effective_end


# Legacy functions resolve these names at call time. Replacing only the module
# reference keeps the standard subprocess module untouched for every other
# component and for the direct renderer code.
_legacy.subprocess = _SubprocessProxy()
_legacy._finite = _finite
_legacy._mark_and_validate_segments = _mark_and_validate_segments

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "CHILD_PYTHON_POLICY",
        "_child_python_env",
        "_command_flag",
        "_finite",
        "_is_master_command",
        "_is_master_release_command",
        "_is_python_script_command",
        "_mark_and_validate_segments",
        "_run_child_process",
        "_strict_int",
        "_verify_post_aac_master_output",
    }
)
