#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lazy backend session and evidence-gated reference reuse."""
from __future__ import annotations

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


class MutableAudioSpec:
    def __init__(self, encode: int, output: int, cache_length: int) -> None:
        self.encode_sample_rate = int(encode)
        self.output_sample_rate = int(output)
        self.seconds_per_step: float | None = None
        self.cache_length = int(cache_length)

    def update(self, value: Any) -> None:
        encode = int(value.encode_sample_rate)
        output = int(value.output_sample_rate)
        if (encode, output) != (
            self.encode_sample_rate,
            self.output_sample_rate,
        ):
            raise RuntimeError(
                "Загруженная модель изменила заявленный аудиотракт: "
                f"{encode}->{output}."
            )
        self.seconds_per_step = (
            float(value.seconds_per_step)
            if value.seconds_per_step is not None
            else None
        )
        if value.cache_length is not None:
            self.cache_length = int(value.cache_length)

    def as_dict(self) -> dict[str, Any]:
        return {
            "encode_sample_rate": self.encode_sample_rate,
            "output_sample_rate": self.output_sample_rate,
            "seconds_per_step": self.seconds_per_step,
            "cache_length": self.cache_length,
            "lazy_session_policy": POLICY,
        }


class LazySession:
    def __init__(
        self,
        backend: Any,
        config: Any,
        *,
        encode: int,
        output: int,
        log: Callable[[str], Any],
    ) -> None:
        self._backend = backend
        self._config = config
        self._real: Any | None = None
        self._log = log
        options = getattr(config, "options", {}) or {}
        self.audio_spec = MutableAudioSpec(
            encode,
            output,
            int(options.get("cache_length", 4096)),
        )
        try:
            self.supports_continuation_context = bool(
                backend.capabilities().continuation_context
            )
        except Exception:
            self.supports_continuation_context = True

    @property
    def opened(self) -> bool:
        return self._real is not None

    def ensure_open(self) -> Any:
        if self._real is None:
            started = time.perf_counter()
            real = self._backend.open_session(self._config)
            self.audio_spec.update(real.audio_spec)
            self._real = real
            self._log(
                "Модель реально загружена перед первым отсутствующим сегментом "
                f"за {time.perf_counter() - started:.1f} сек."
            )
        return self._real

    def generate(self, request: Any) -> Any:
        return self.ensure_open().generate(request)


class LazyBackend:
    def __init__(
        self,
        backend: Any,
        *,
        encode: int,
        output: int,
        log: Callable[[str], Any],
    ) -> None:
        self._backend = backend
        self._encode = int(encode)
        self._output = int(output)
        self._log = log
        self._session: LazySession | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

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


def cached_reference(
    *,
    source: Path,
    output: Path,
    hash_file: Callable[[Path], str],
    expected_sample_rate: int,
) -> dict[str, Any] | None:
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if not source_path.is_file() or not output_path.is_file():
        return None
    entry = _read_cache(output_path.parent / "references.json").get(
        output_path.stem
    )
    if not isinstance(entry, dict):
        return None
    spectrum = entry.get("spectral_envelope")
    bands = spectrum.get("bands") if isinstance(spectrum, dict) else None
    valid = bool(
        str(entry.get("source_sha256") or "") == hash_file(source_path)
        and str(entry.get("sha256") or "") == hash_file(output_path)
        and int(entry.get("sample_rate") or 0) == int(expected_sample_rate)
        and 5.0 <= _finite(entry.get("duration")) <= 31.0
        and _finite(entry.get("voiced_ratio")) >= 0.12
        and _finite(entry.get("active_ratio")) >= 0.20
        and _finite(entry.get("max_internal_gap"), 99.0) <= 1.20
        and _finite(entry.get("clipping_ratio"), 1.0) <= 0.005
        and isinstance(spectrum, dict)
        and int(spectrum.get("frames") or 0) > 0
        and isinstance(bands, list)
        and bool(bands)
    )
    if not valid:
        return None
    result = dict(entry)
    result["reference_cache_hit"] = True
    result["reference_cache_policy"] = _REFERENCE_CACHE_POLICY
    return result


def enrich_reference_report(
    report: dict[str, Any],
    *,
    source: Path,
    hash_file: Callable[[Path], str],
) -> dict[str, Any]:
    result = dict(report)
    result["source_sha256"] = hash_file(Path(source).resolve())
    result["reference_cache_hit"] = False
    result["reference_cache_policy"] = _REFERENCE_CACHE_POLICY
    return result


__all__ = [
    "LazyBackend",
    "LazySession",
    "MutableAudioSpec",
    "POLICY",
    "cached_reference",
    "enrich_reference_report",
]
