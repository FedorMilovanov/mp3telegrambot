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
import sys
import json
import math
import os
import re
import subprocess as _stdlib_subprocess
from pathlib import Path
from typing import Any

from tools.voxcpm2 import final_encoded_delivery_qa
from tools.voxcpm2 import semantic_block_runtime

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "clean_production_core.py"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._clean_production_core_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean production core: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

_legacy_build_direct_segments = _legacy.build_direct_segments

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

CHILD_PYTHON_POLICY = "repo-root-pythonpath-master-stderr-and-post-aac-v2"
DELIVERY_RETRY_POLICY = "bounded-checkpointed-delivery-retry-v1"
MAX_AUTOMATIC_DELIVERY_RETRIES = 3
_LAST_CHILD_STDERR = ""

# Explicit bindings keep static verification aligned with the dynamically
# imported legacy API used by this compatibility facade.
POLICY = _legacy.POLICY
MAX_SECONDS = _legacy.MAX_SECONDS
SEMANTIC_BLOCK_MAX_SECONDS = semantic_block_runtime.MAX_BLOCK_SECONDS
log = _legacy.log

_RETRYABLE_DELIVERY_MARKERS = (
    "следующий повтор использует seed epoch",
    "переведена на новый seed epoch",
    "переведен на новый seed epoch",
    "seed epochs",
    "hard-quality кандидат",
    "linked_phrase_gap",
    "late_broadband_burst",
    "late_broadband_tail",
    "assembled_delivery:",
    "post_aac_delivery:",
    "ending/tail qa",
)
_NON_RETRYABLE_INFRASTRUCTURE_MARKERS = (
    "modulenotfounderror",
    "filenotfounderror",
    "permissionerror",
    "preflight",
    "fingerprint",
    "не найден ffmpeg",
    "не найдены в path",
    "не найден cpu python",
    "не найден source",
    "не найден segments",
    "не найден voice reference",
    "http 403",
    "http 404",
)


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
    # A Linux CI process must still recognize the Windows commands production
    # will run. ``Path`` only treats separators from the host OS as separators.
    executable = re.split(r"[\\/]", str(command[0]))[-1].casefold()
    script = re.split(r"[\\/]", str(command[1]))[-1].casefold()
    return executable.startswith("python") and script.endswith(".py")


def _is_master_command(command: Any) -> bool:
    return bool(
        _is_python_script_command(command)
        and re.split(r"[\\/]", str(command[1]))[-1].casefold()
        == "master_constant_mix.py"
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
    global _LAST_CHILD_STDERR

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
        _LAST_CHILD_STDERR = detail
        raise RuntimeError("Прямой master завершился с точной причиной:\n" + detail)
    if is_master_release:
        try:
            _verify_post_aac_master_output(command)
        except Exception as exc:
            _LAST_CHILD_STDERR = str(exc)
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
        segment_limit = (
            SEMANTIC_BLOCK_MAX_SECONDS
            if str(item.get("semantic_block_policy") or "") == semantic_block_runtime.POLICY
            else MAX_SECONDS
        )
        if end - start > segment_limit + 0.30:
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


def build_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Select the direct planning policy without exposing model internals."""
    if any(str(item.get("semantic_block_policy") or "") == semantic_block_runtime.POLICY for item in groups):
        return semantic_block_runtime.build_direct_segments(
            groups,
            delay_ms=delay_ms,
            duration=duration,
        )
    return _legacy_build_direct_segments(
        groups,
        delay_ms=delay_ms,
        duration=duration,
    )


# Legacy functions resolve these names at call time. Replacing only the module
# reference keeps the standard subprocess module untouched for every other
# component and for the direct renderer code.
_legacy.build_direct_segments = build_direct_segments
_legacy.subprocess = _SubprocessProxy()
_legacy._finite = _finite
_legacy._mark_and_validate_segments = _mark_and_validate_segments
_legacy_render_and_master = _legacy.render_and_master


def _retryable_delivery_failure(detail: str) -> bool:
    """Accept only failures whose quality code already invalidated a checkpoint."""
    normalized = str(detail or "").casefold().replace("ё", "е")
    if not normalized:
        return False
    if any(marker in normalized for marker in _NON_RETRYABLE_INFRASTRUCTURE_MARKERS):
        return False
    return any(marker in normalized for marker in _RETRYABLE_DELIVERY_MARKERS)


def _direct_failure_report(root: Any) -> str:
    try:
        path = Path(root).resolve() / "segment_work" / "direct_renderer_failure.json"
    except (TypeError, ValueError, OSError):
        return ""
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    message = str(payload.get("message") or "").strip()
    error_type = str(payload.get("error_type") or "RuntimeError").strip()
    return f"{error_type}: {message}" if message else ""


def _delivery_failure_detail(
    exc: RuntimeError,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Recover the deepest child cause without treating an old report as current."""
    exception_detail = str(exc).strip()
    details: list[str] = []
    child_detail = str(_LAST_CHILD_STDERR or "").strip()
    if child_detail:
        details.append(child_detail)

    # The renderer deliberately streams logs instead of buffering many minutes
    # of output. Its fresh failure JSON is authoritative only when that exact
    # child process returned non-zero; otherwise an older report must not turn
    # an infrastructure error into a quality retry.
    if "Прямой VoxCPM2 renderer завершился с кодом" in exception_detail:
        root = kwargs.get("root")
        if root is None and args:
            root = args[0]
        report_detail = _direct_failure_report(root)
        if report_detail:
            details.append(report_detail)
    if exception_detail:
        details.append(exception_detail)

    unique: list[str] = []
    for value in details:
        if value not in unique:
            unique.append(value)
    return "\n".join(unique)


def render_and_master(*args: Any, **kwargs: Any) -> Any:
    """Retry quality-only failures in-place while preserving good checkpoints."""
    global _LAST_CHILD_STDERR

    try:
        retry_limit = int(MAX_AUTOMATIC_DELIVERY_RETRIES)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("MAX_AUTOMATIC_DELIVERY_RETRIES должен быть целым.") from exc
    retry_limit = max(0, min(8, retry_limit))

    for retry_index in range(retry_limit + 1):
        _LAST_CHILD_STDERR = ""
        try:
            return _legacy_render_and_master(*args, **kwargs)
        except RuntimeError as exc:
            detail = _delivery_failure_detail(exc, args, kwargs)
            if not _retryable_delivery_failure(detail):
                if detail and detail != str(exc).strip():
                    raise RuntimeError(detail) from exc
                raise
            if retry_index >= retry_limit:
                raise RuntimeError(
                    "Автоматическое checkpoint-восстановление исчерпано "
                    f"после {retry_limit} повторов. Последняя точная причина:\n{detail}"
                ) from exc
            log(
                "quality-only failure; сохраняю успешные checkpoints и запускаю "
                f"автоматический повтор {retry_index + 1}/{retry_limit}. "
                f"Причина: {detail[:1200]}"
            )

    raise RuntimeError("Недостижимое состояние automatic delivery retry.")

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "CHILD_PYTHON_POLICY",
        "DELIVERY_RETRY_POLICY",
        "MAX_AUTOMATIC_DELIVERY_RETRIES",
        "_LAST_CHILD_STDERR",
        "_child_python_env",
        "_command_flag",
        "_delivery_failure_detail",
        "_direct_failure_report",
        "_finite",
        "_is_master_command",
        "_is_master_release_command",
        "_is_python_script_command",
        "_legacy_render_and_master",
        "_mark_and_validate_segments",
        "_retryable_delivery_failure",
        "_run_child_process",
        "_strict_int",
        "_verify_post_aac_master_output",
        "render_and_master",
    }
)
