#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded segment-only recovery after independent direct-render QA.

The clean renderer already performs two seed rounds and preserves every accepted
checkpoint. Historically, if the same segment failed the independent semantic /
timing / voice gate in both rounds, orchestration stopped even though the failure
was local and every other checkpoint was reusable. This module continues only
that exact, report-backed quality failure across additional seed pairs.

No QA threshold is relaxed. A retry is allowed only when the authoritative clean
QA JSON names non-passing segment IDs that exist in the current segments file.
Good checkpoint metadata is retargeted to the next base seed and failed IDs are
removed through the established semantic guard helper before the next renderer
call.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

POLICY = "bounded-independent-qa-segment-retry-v1"
MAX_RECOVERY_CYCLES = 3
INTERNAL_SEED_ROUNDS_PER_CALL = 2
_FAILURE_MARKER = (
    "чистый direct renderer не прошел независимый qa после одного "
    "прицельного повтора"
)
_INSTALLED = False


def _runtime_settings(request: dict[str, Any], duration: Any) -> dict[str, Any]:
    from tools.voxcpm2 import clean_runtime_contract

    return dict(clean_runtime_contract.normalize_settings(request, duration=duration))


def _retry_seed_offset() -> int:
    from tools.voxcpm2 import clean_runtime_contract

    value = int(clean_runtime_contract.RETRY_SEED_OFFSET)
    if value <= 0:
        raise RuntimeError("RETRY_SEED_OFFSET должен быть положительным.")
    return value


def _retarget_checkpoints(
    work_dir: Path,
    *,
    good_ids: set[int],
    failed_ids: list[int],
    new_base_seed: int,
) -> None:
    from tools.voxcpm2 import semantic_tts_guard_v4

    semantic_tts_guard_v4._retarget(
        work_dir,
        good_ids=good_ids,
        failed_ids=failed_ids,
        new_base_seed=int(new_base_seed),
    )


def _log(message: str) -> None:
    try:
        from tools.voxcpm2 import clean_production_core

        clean_production_core.log(message)
    except Exception:
        print(f"[INDEPENDENT-QA-RETRY] {message}", flush=True)


def _normalized_detail(value: Any) -> str:
    return str(value or "").casefold().replace("ё", "е")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _strict_int(value: Any, *, field: str) -> int:
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
    return result


def _retry_context(
    detail: str,
    kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a fail-closed retry plan for one authoritative independent-QA failure."""
    if _FAILURE_MARKER not in _normalized_detail(detail):
        return None

    root_raw = kwargs.get("root")
    request = kwargs.get("request")
    segments_raw = kwargs.get("segments_json")
    duration = kwargs.get("duration")
    if (
        root_raw is None
        or not isinstance(request, dict)
        or segments_raw is None
        or duration is None
    ):
        return None

    try:
        root = Path(root_raw).resolve()
        segments_path = Path(segments_raw).resolve()
    except (TypeError, ValueError, OSError):
        return None
    if not root.is_dir() or not segments_path.is_file():
        return None

    try:
        settings = _runtime_settings(dict(request), duration)
        base_seed = _strict_int(settings.get("base_seed"), field="base_seed")
    except (RuntimeError, TypeError, ValueError, OverflowError):
        return None
    video_id = str(settings.get("video_id") or "").strip()
    if not video_id:
        return None

    report_path = root / "audio" / f"{video_id}_ru_timeline.clean_qa.json"
    report = _read_json(report_path)
    segments = _read_json(segments_path)
    if not isinstance(report, dict) or not isinstance(segments, list) or not segments:
        return None
    if report.get("passed") is True:
        return None

    all_ids: set[int] = set()
    try:
        for position, item in enumerate(segments, start=1):
            if not isinstance(item, dict):
                return None
            segment_id = _strict_int(item.get("id"), field=f"segment[{position}].id")
            if segment_id <= 0 or segment_id in all_ids:
                return None
            all_ids.add(segment_id)
    except RuntimeError:
        return None

    raw_failed = report.get("failed_segment_ids")
    if not isinstance(raw_failed, list) or not raw_failed:
        return None
    try:
        failed_ids = sorted(
            {
                _strict_int(value, field="failed_segment_id")
                for value in raw_failed
            }
        )
    except RuntimeError:
        return None
    if not failed_ids or not set(failed_ids).issubset(all_ids):
        return None

    checks = {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }
    if any(
        not isinstance(checks.get(segment_id), dict)
        or checks[segment_id].get("passed") is True
        for segment_id in failed_ids
    ):
        return None

    return {
        "root": root,
        "request": dict(request),
        "segments_path": segments_path,
        "report_path": report_path,
        "base_seed": base_seed,
        "all_ids": all_ids,
        "failed_ids": failed_ids,
    }


def _run_with_recovery(
    original: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Continue exact independent-QA failures without recomputing good segments."""
    active_kwargs = dict(kwargs)
    for recovery_index in range(MAX_RECOVERY_CYCLES + 1):
        try:
            return original(*args, **active_kwargs)
        except RuntimeError as exc:
            context = _retry_context(str(exc), active_kwargs)
            if context is None:
                raise
            if recovery_index >= MAX_RECOVERY_CYCLES:
                raise RuntimeError(
                    "Независимый QA исчерпал ограниченное segment-only восстановление "
                    f"({MAX_RECOVERY_CYCLES} дополнительных циклов). "
                    f"Последняя точная причина: {exc}"
                ) from exc

            stride = _retry_seed_offset()
            next_base_seed = int(context["base_seed"]) + (
                INTERNAL_SEED_ROUNDS_PER_CALL * stride
            )
            failed_ids = list(context["failed_ids"])
            all_ids = set(context["all_ids"])
            _retarget_checkpoints(
                Path(context["root"]) / "segment_work",
                good_ids=all_ids - set(failed_ids),
                failed_ids=failed_ids,
                new_base_seed=next_base_seed,
            )
            next_request = dict(context["request"])
            next_request["base_seed"] = next_base_seed
            active_kwargs["request"] = next_request
            _log(
                "independent QA отклонил только сегменты "
                f"{failed_ids}; хорошие checkpoints сохранены, проблемные ID "
                f"переведены на base seed {next_base_seed}; дополнительный цикл "
                f"{recovery_index + 1}/{MAX_RECOVERY_CYCLES}"
            )

    raise RuntimeError("Недостижимое состояние independent QA recovery.")


def install() -> None:
    """Install the recovery wrapper once in the ready-SRT child process."""
    global _INSTALLED

    from tools.voxcpm2 import clean_production_core

    current = clean_production_core.render_and_master
    if getattr(current, "_independent_qa_retry_policy", None) == POLICY:
        _INSTALLED = True
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return _run_with_recovery(current, *args, **kwargs)

    wrapped._independent_qa_retry_policy = POLICY  # type: ignore[attr-defined]
    wrapped._independent_qa_retry_original = current  # type: ignore[attr-defined]
    clean_production_core.render_and_master = wrapped
    _INSTALLED = True


__all__ = [
    "INTERNAL_SEED_ROUNDS_PER_CALL",
    "MAX_RECOVERY_CYCLES",
    "POLICY",
    "_retry_context",
    "_run_with_recovery",
    "install",
]
