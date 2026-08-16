#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lazy backend session and evidence-gated reference reuse."""
from __future__ import annotations

from tools.voxcpm2 import direct_surgical_polish_v2 as polish
import json
import math
import time
from pathlib import Path
from typing import Any, Callable

POLICY = "voxcpm2-surgical-io-v1"
_REFERENCE_CACHE_POLICY = "guarded-reference-cache-v1"
_MAX_REPORT_BYTES = 2 * 1024 * 1024


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


class LazyBackend:
    def __init__(
        self,
        backend: Any,
        *,
        encode: int,
        output: int,
        log: Callable[[str], Any],
        model_discovery_callback: Callable[[Path], Any] | None = None,
    ) -> None:
        self._backend = backend
        self._encode = int(encode)
        self._output = int(output)
        self._log = log
        self._model_discovery_callback = model_discovery_callback
        self._session: LazySession | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def set_model_discovery_callback(
        self,
        callback: Callable[[Path], Any] | None,
    ) -> None:
        self._model_discovery_callback = callback

    def discover_model(self, archive_root: Path) -> Path:
        model = Path(self._backend.discover_model(Path(archive_root))).resolve()
        callback = self._model_discovery_callback
        if callback is not None:
            callback(model)
        return model

    def open_session(self, config: Any) -> LazySession:
        if self._session is not None:
            raise RuntimeError("Backend session уже создана в render process.")
        self._session = LazySession(
            self._backend,
            config,
            encode=self._encode,
            output=self._output,
            log=self._log,
        )
        return self._session

    def plan_generation_length(self, _spec: Any, request: Any) -> Any:
        if self._session is None:
            raise RuntimeError("Length planning вызван до backend session.")
        real = self._session.ensure_open()
        return self._backend.plan_generation_length(real.audio_spec, request)

    def plan_generation_profile(self, request: Any) -> Any:
        return self._backend.plan_generation_profile(request)


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > _MAX_REPORT_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


# Source-owned strengthened IO contract. The implementation is shared with the
# pure polish policy module; no imported module is mutated.
POLICY = polish.POLICY
MutableAudioSpec = polish._AudioSpec
LazySession = polish._LazySession
cached_reference = polish._cached_reference
enrich_reference_report = polish._enrich_reference_report

__all__ = [
    "LazyBackend",
    "LazySession",
    "MutableAudioSpec",
    "POLICY",
    "cached_reference",
    "enrich_reference_report",
]
