#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Advance exact-input retry state for structured universal early stops."""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from tools.voxcpm2 import direct_timing_guard

POLICY = "universal-early-stop-retry-recovery-v2"
_MAX_SEGMENTS_BYTES = 8 * 1024 * 1024
_LEGACY_RECOVERABLE = (
    "адаптивный бюджет",
    "не помещается естественно",
)
_SEGMENT_RE = re.compile(r"Сегмент\s+#(\d+)")


def _flag(name: str) -> str:
    prefix = name + "="
    for index, value in enumerate(sys.argv):
        text = str(value)
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
        if text == name and index + 1 < len(sys.argv):
            return str(sys.argv[index + 1]).strip()
    return ""


def _segment_from_json(path: Path, segment_id: int) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_SEGMENTS_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list):
        return None
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            current = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if current == segment_id:
            return dict(item)
    return None


def _reported_epoch(payload: Mapping[str, Any]) -> Any:
    for key in ("last_scope_epoch", "retry_epoch", "epoch"):
        value = payload.get(key)
        if value is not None:
            return value
    return "unknown"


def run_with_failure_recovery(
    original: Callable[[], Any],
    invalidate: Callable[..., Any],
) -> Any:
    """Advance once for a new synthesis failure; never advance a blocked repeat."""
    if not callable(original) or not callable(invalidate):
        raise TypeError("direct main recovery contract is incomplete.")
    try:
        return original()
    except RuntimeError as exc:
        message = str(exc)
        failure_type = getattr(direct_timing_guard, "RetryableSynthesisFailure", None)
        structured = bool(
            isinstance(failure_type, type) and isinstance(exc, failure_type)
        )
        if structured:
            if not bool(exc.advance_retry):
                raise
            segment = dict(exc.segment)
            evidence = {
                **dict(exc.evidence),
                "policy": POLICY,
                "early_stop_kind": exc.failure_kind,
                "early_stop_message": message[:1000],
            }
        else:
            if not any(marker in message for marker in _LEGACY_RECOVERABLE):
                raise
            match = _SEGMENT_RE.search(message)
            segments_value = _flag("--segments-json")
            if match is None or not segments_value:
                raise
            segment_id = int(match.group(1))
            segment = _segment_from_json(Path(segments_value).resolve(), segment_id)
            if not isinstance(segment, dict):
                raise
            evidence = {
                "policy": POLICY,
                "early_stop_kind": "legacy_message_fallback",
                "early_stop_message": message[:1000],
            }

        work_value = _flag("--work-dir")
        if not work_value:
            raise
        work_dir = Path(work_value).resolve()
        try:
            result = invalidate(
                work_dir,
                segment,
                reason="raw_candidate_hard_failure",
                evidence=evidence,
            )
        except Exception as recovery_error:
            raise RuntimeError(
                f"{message} Retry-state recovery failed: "
                f"{type(recovery_error).__name__}: {recovery_error}"
            ) from exc
        if not isinstance(result, Mapping):
            raise RuntimeError(
                f"{message} Retry-state recovery returned invalid payload."
            ) from exc
        raise RuntimeError(
            f"{message} Retry scope advanced to {_reported_epoch(result)}."
        ) from exc


__all__ = ["POLICY", "run_with_failure_recovery"]
