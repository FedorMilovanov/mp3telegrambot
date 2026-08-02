#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Advance exact-scope retry state for universal early-stop failures."""
from __future__ import annotations

import json
import re
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

POLICY = "universal-early-stop-retry-recovery-v1"
_RECOVERABLE = (
    "адаптивный бюджет",
    "не помещается естественно",
)
_SEGMENT_RE = re.compile(r"Сегмент\s+#(\d+)")


def _flag(name: str) -> str:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return ""
    if index + 1 >= len(sys.argv):
        return ""
    return str(sys.argv[index + 1]).strip()


def _segment_from_json(path: Path, segment_id: int) -> dict[str, Any] | None:
    try:
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


def install_main_failure_recovery(namespace: MutableMapping[str, Any]) -> None:
    """Wrap direct main without changing ordinary or already-invalidated errors."""
    original = namespace.get("main")
    invalidate = namespace.get("invalidate_segment_for_retry")
    if not callable(original) or not callable(invalidate):
        raise RuntimeError("direct main recovery contract is incomplete.")

    def main() -> Any:
        try:
            return original()
        except RuntimeError as exc:
            message = str(exc)
            if not any(marker in message for marker in _RECOVERABLE):
                raise
            match = _SEGMENT_RE.search(message)
            work_value = _flag("--work-dir")
            segments_value = _flag("--segments-json")
            if match is None or not work_value or not segments_value:
                raise
            segment_id = int(match.group(1))
            work_dir = Path(work_value).resolve()
            segment = _segment_from_json(Path(segments_value).resolve(), segment_id)
            if not isinstance(segment, dict):
                raise
            try:
                result = invalidate(
                    work_dir,
                    segment,
                    reason="raw_candidate_hard_failure",
                    evidence={
                        "policy": POLICY,
                        "early_stop_message": message[:1000],
                    },
                )
            except Exception as recovery_error:
                raise RuntimeError(
                    f"{message} Retry-state recovery failed: "
                    f"{type(recovery_error).__name__}: {recovery_error}"
                ) from exc
            raise RuntimeError(
                f"{message} Retry scope advanced to "
                f"{result.get('retry_epoch', 'unknown')}."
            ) from exc

    namespace["main"] = main
    namespace["EARLY_STOP_RECOVERY_POLICY"] = POLICY


__all__ = ["POLICY", "install_main_failure_recovery"]
