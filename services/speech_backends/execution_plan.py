"""Immutable evidence for the exact backend model call."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

GENERATION_EXECUTION_PLAN_POLICY = "backend-generation-execution-plan-v1"
_APPEND_LOCK = threading.Lock()


def _safe_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return repr(value)


@dataclass(frozen=True)
class BackendGenerationExecutionPlan:
    backend_id: str
    adapter_policy: str
    request_fingerprint: str
    planned_max_len: int
    executed_max_len: int
    model_kwargs: Mapping[str, Any]
    accepted_optional_parameters: tuple[str, ...]
    omitted_optional_parameters: tuple[str, ...]

    def __post_init__(self) -> None:
        backend_id = str(self.backend_id or "").casefold().strip()
        if not backend_id:
            raise ValueError("Execution plan requires backend_id.")
        if int(self.planned_max_len) <= 0 or int(self.executed_max_len) <= 0:
            raise ValueError("Execution max lengths must be positive.")
        object.__setattr__(self, "backend_id", backend_id)
        object.__setattr__(self, "planned_max_len", int(self.planned_max_len))
        object.__setattr__(self, "executed_max_len", int(self.executed_max_len))
        object.__setattr__(self, "model_kwargs", dict(self.model_kwargs))
        object.__setattr__(
            self,
            "accepted_optional_parameters",
            tuple(sorted(str(item) for item in self.accepted_optional_parameters)),
        )
        object.__setattr__(
            self,
            "omitted_optional_parameters",
            tuple(sorted(str(item) for item in self.omitted_optional_parameters)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": GENERATION_EXECUTION_PLAN_POLICY,
            "backend_id": self.backend_id,
            "adapter_policy": self.adapter_policy,
            "request_fingerprint": self.request_fingerprint,
            "planned_max_len": self.planned_max_len,
            "executed_max_len": self.executed_max_len,
            "model_kwargs": _safe_value(self.model_kwargs),
            "accepted_optional_parameters": list(self.accepted_optional_parameters),
            "omitted_optional_parameters": list(self.omitted_optional_parameters),
        }


def request_fingerprint(*, text: str, reference_audio: Path, seed: int | None) -> str:
    payload = json.dumps(
        {
            "text": str(text),
            "reference_audio": str(Path(reference_audio)),
            "seed": seed,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def append_execution_plan_from_environment(
    plan: BackendGenerationExecutionPlan,
) -> Path | None:
    """Append durable JSONL evidence when orchestration supplies a report path."""
    raw = os.getenv("DUB_BACKEND_EXECUTION_PLAN_LOG", "").strip()
    if not raw:
        return None
    destination = Path(raw).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        plan.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with _APPEND_LOCK:
        with destination.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return destination


__all__ = [
    "GENERATION_EXECUTION_PLAN_POLICY",
    "BackendGenerationExecutionPlan",
    "append_execution_plan_from_environment",
    "request_fingerprint",
]
