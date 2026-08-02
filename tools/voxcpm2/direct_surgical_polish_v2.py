#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Second-pass fail-closed contracts for universal direct VoxCPM2 jobs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable

from tools.voxcpm2 import direct_retry_epoch as retry
from tools.voxcpm2 import direct_surgical_io as sio
from tools.voxcpm2 import direct_surgical_runtime as runtime
from tools.voxcpm2 import direct_timing_guard as guard

POLICY = "voxcpm2-surgical-polish-v2"
MARKER_POLICY = "voxcpm2-measured-timing-block-v3"
MARKER_SCHEMA_VERSION = 3
REFERENCE_CACHE_POLICY = "guarded-reference-cache-v2"
REFERENCE_CACHE_SCHEMA_VERSION = 2
RUNTIME_MARKER_POLICY = "direct-cli-runtime-marker-v2"
MAX_SCOPE_EPOCH = 3
MAX_SEGMENTS = 10_000
MAX_TEXT_CHARS = 20_000
MAX_TOTAL_TEXT_CHARS = 2_000_000
MAX_BYTES = 2 * 1024 * 1024
_INSTALLED = False
_REFERENCE_CONTRACT = ""
_HEX = re.compile(r"^[0-9a-f]{64}$")
_REF_FILES = (
    "tools/voxcpm2/direct_max_quality_analysis.py",
    "tools/voxcpm2/direct_timbre_analysis.py",
    "tools/voxcpm2/direct_max_quality_io.py",
    "tools/voxcpm2/direct_surgical_io.py",
    "tools/voxcpm2/direct_surgical_polish_v2.py",
)
_EXTRA_SCOPE = (
    "tools/voxcpm2/direct_surgical_polish_v2.py",
    "tools/voxcpm2/_direct_retry_epoch_base.py",
    "tools/voxcpm2/_direct_max_quality_cli_base.py",
    "tools/voxcpm2/direct_max_quality_io.py",
    "tools/voxcpm2/direct_max_quality_analysis.py",
    "tools/voxcpm2/direct_max_quality_render.py",
    "tools/voxcpm2/direct_russian_cadence.py",
    "tools/voxcpm2/direct_tail_artifact.py",
    "tools/voxcpm2/direct_source_prosody.py",
    "tools/voxcpm2/direct_timbre_analysis.py",
    "tools/voxcpm2/direct_timeline_delivery_qa.py",
    "tools/voxcpm2/direct_monolith_contract.py",
    "tools/voxcpm2/russian_pronunciation.py",
    "tools/voxcpm2/source_prosody_policy.py",
    "tools/voxcpm2/examples/john_piper_z20py4yqhyq/voxcpm2_cpu_shorts_production.py",
    "services/speech_backends/base.py",
    "services/speech_backends/control_plane.py",
    "services/speech_backends/execution_plan.py",
    "services/speech_backends/model_profiles.py",
    "services/speech_backends/registry.py",
)


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{name} не может быть bool.")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise RuntimeError(f"{name} должен быть целым числом.")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {name}: {value!r}") from exc


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{name} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {name}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{name} должен быть конечным числом.")
    return result


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _sha(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if _HEX.fullmatch(text) else ""


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _stable_hash(path: Path, hash_file: Callable[[Path], str]) -> str:
    target = Path(path).resolve()
    before = target.stat()
    digest = _sha(hash_file(target))
    after = target.stat()
    if not digest or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Нестабильный fingerprint: {target}")
    return digest


def _segments(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for position, item in enumerate(values, 1):
        if not isinstance(item, Mapping):
            raise RuntimeError(f"Сегмент {position} должен быть объектом.")
        result.append(dict(item))
    if not result or len(result) > MAX_SEGMENTS:
        raise RuntimeError("Некорректное количество сегментов.")
    seen, previous_end, total_text = set(), -1.0, 0
    for position, item in enumerate(result, 1):
        raw_id = item.get("id")
        sid = position if raw_id is None else _integer(raw_id, f"segment[{position}].id")
        start = _number(item.get("start"), f"segment[{sid}].start")
        end = _number(item.get("end"), f"segment[{sid}].end")
        tail = _number(item.get("tail_guard"), f"segment[{sid}].tail_guard")
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if sid <= 0 or sid in seen:
            raise RuntimeError(f"Некорректный или повторный ID сегмента: {sid}.")
        if start < 0 or end <= start or tail < 0 or tail >= end - start:
            raise RuntimeError(f"Сегмент #{sid}: некорректное речевое окно.")
        if start < previous_end - 1e-6:
            raise RuntimeError(f"Сегмент #{sid}: перекрытие или неправильный порядок.")
        if not text or len(text) > MAX_TEXT_CHARS:
            raise RuntimeError(f"Сегмент #{sid}: некорректная длина текста.")
        total_text += len(text)
        if total_text > MAX_TOTAL_TEXT_CHARS:
            raise RuntimeError("Суммарный текст слишком велик.")
        slot = end - start - tail
        if item.get("speech_slot") is not None and abs(
            _number(item["speech_slot"], f"segment[{sid}].speech_slot") - slot
        ) > 1e-6:
            raise RuntimeError(f"Сегмент #{sid}: сохранённый speech_slot не совпадает.")
        item.update(id=sid, text=text, start=start, end=end, tail_guard=tail)
        seen.add(sid)
        previous_end = end
    return result


def _segments_by_id(values: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    normalized = _segments(values)
    return {int(clean["id"]): source for clean, source in zip(normalized, values)}


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _runtime_marker(work_dir: Path) -> dict[str, Any]:
    value = _read(Path(work_dir).resolve() / "direct_cli_runtime.marker.json")
    try:
        cache = _integer(value.get("cache_length"), "runtime.cache_length")
    except RuntimeError:
        cache = 0
    digest = _sha(value.get("render_contract_sha256"))
    backend = str(value.get("speech_backend") or "").strip().casefold()
    python = str(value.get("python_executable") or "").strip()
    if (
        value.get("schema_version") != 2
        or value.get("policy") != RUNTIME_MARKER_POLICY
        or not digest
        or not backend
        or not python
        or not 2048 <= cache <= 131072
    ):
        return {"runtime_marker_policy": "missing-or-invalid-direct-runtime-marker"}
    return {
        "runtime_marker_policy": RUNTIME_MARKER_POLICY,
        "render_contract_sha256": digest,
        "runtime_speech_backend": backend,
        "runtime_python": python,
        "runtime_cache_length": cache,
    }


def _ref_contract(hash_file: Callable[[Path], str]) -> str:
    global _REFERENCE_CONTRACT
    if not _REFERENCE_CONTRACT:
        root = Path(__file__).resolve().parents[2]
        value = {"policy": REFERENCE_CACHE_POLICY}
        for relative in _REF_FILES:
            value[relative] = _stable_hash(root / relative, hash_file)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        _REFERENCE_CONTRACT = hashlib.sha256(encoded).hexdigest()
    return _REFERENCE_CONTRACT


def _spectrum(value: Any, count: int) -> bool:
    if not isinstance(value, Mapping):
        return False
    bands = value.get("bands")
    try:
        frames = _integer(value.get("frames"), "spectral.frames")
    except RuntimeError:
        return False
    return bool(
        frames > 0
        and count > 0
        and isinstance(bands, list)
        and len(bands) == count
        and all(math.isfinite(_finite(item, float("nan"))) for item in bands)
    )


def _cached_reference(
    *, source: Path, output: Path,
    hash_file: Callable[[Path], str], expected_sample_rate: int,
) -> dict[str, Any] | None:
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or not output.is_file():
        return None
    item = _read(output.parent / "references.json").get(output.stem)
    if not isinstance(item, dict):
        return None
    try:
        count = _integer(item.get("spectral_band_count"), "spectral_band_count")
        rate = _integer(item.get("sample_rate"), "sample_rate")
        source_hash = _stable_hash(source, hash_file)
        output_hash = _stable_hash(output, hash_file)
    except (OSError, RuntimeError):
        return None
    valid = bool(
        item.get("reference_cache_schema_version") == REFERENCE_CACHE_SCHEMA_VERSION
        and item.get("reference_cache_policy") == REFERENCE_CACHE_POLICY
        and _sha(item.get("reference_contract_sha256")) == _ref_contract(hash_file)
        and _sha(item.get("source_sha256")) == source_hash
        and _sha(item.get("sha256")) == output_hash
        and rate == _integer(expected_sample_rate, "expected_sample_rate")
        and 5 <= _finite(item.get("duration")) <= 31
        and _finite(item.get("voiced_ratio")) >= 0.12
        and _finite(item.get("active_ratio")) >= 0.20
        and _finite(item.get("max_internal_gap"), 99) <= 1.20
        and _finite(item.get("clipping_ratio"), 1) <= 0.005
        and _spectrum(item.get("spectral_envelope"), count)
    )
    if not valid:
        return None
    result = dict(item)
    result["reference_cache_hit"] = True
    return result


def _enrich_reference_report(
    report: dict[str, Any], *, source: Path, hash_file: Callable[[Path], str],
) -> dict[str, Any]:
    result = dict(report)
    spectrum = result.get("spectral_envelope")
    bands = spectrum.get("bands") if isinstance(spectrum, Mapping) else None
    if not isinstance(bands, list) or not bands:
        raise RuntimeError("Reference report не содержит spectral bands.")
    result.update(
        reference_cache_schema_version=REFERENCE_CACHE_SCHEMA_VERSION,
        reference_cache_policy=REFERENCE_CACHE_POLICY,
        reference_contract_sha256=_ref_contract(hash_file),
        spectral_band_count=len(bands),
        source_sha256=_stable_hash(Path(source), hash_file),
        reference_cache_hit=False,
    )
    return result


class _AudioSpec:
    def __init__(self, encode: int, output: int, cache_length: int) -> None:
        self.encode_sample_rate = _integer(encode, "encode_sample_rate")
        self.output_sample_rate = _integer(output, "output_sample_rate")
        self.cache_length = _integer(cache_length, "cache_length")
        self.seconds_per_step = None
        if min(self.encode_sample_rate, self.output_sample_rate) <= 0:
            raise RuntimeError("Sample rate должен быть > 0.")
        if not 2048 <= self.cache_length <= 131072:
            raise RuntimeError("cache_length вне диапазона.")

    def update(self, value: Any) -> None:
        if value is None:
            raise RuntimeError("Backend session не вернул audio_spec.")
        encode = _integer(getattr(value, "encode_sample_rate", None), "loaded.encode")
        output = _integer(getattr(value, "output_sample_rate", None), "loaded.output")
        if (encode, output) != (self.encode_sample_rate, self.output_sample_rate):
            raise RuntimeError("Загруженная модель изменила аудиотракт.")
        step = getattr(value, "seconds_per_step", None)
        if step is not None:
            self.seconds_per_step = _number(step, "loaded.seconds_per_step")
            if self.seconds_per_step <= 0:
                raise RuntimeError("seconds_per_step должен быть > 0.")
        cache = getattr(value, "cache_length", None)
        if cache is not None:
            cache = _integer(cache, "loaded.cache_length")
            if not 2048 <= cache <= 131072:
                raise RuntimeError("loaded.cache_length вне диапазона.")
            self.cache_length = cache

    def as_dict(self) -> dict[str, Any]:
        return {
            "encode_sample_rate": self.encode_sample_rate,
            "output_sample_rate": self.output_sample_rate,
            "seconds_per_step": self.seconds_per_step,
            "cache_length": self.cache_length,
            "lazy_session_policy": POLICY,
        }


class _LazySession:
    def __init__(self, backend, config, *, encode, output, log) -> None:
        self._backend, self._config, self._log, self._real = backend, config, log, None
        options = getattr(config, "options", {}) or {}
        if not isinstance(options, Mapping):
            raise RuntimeError("BackendSessionConfig.options должен быть mapping.")
        self.audio_spec = _AudioSpec(encode, output, options.get("cache_length", 4096))
        try:
            self.supports_continuation_context = bool(
                getattr(backend.capabilities(), "continuation_context", False)
            )
        except Exception:
            self.supports_continuation_context = False

    @property
    def opened(self) -> bool:
        return self._real is not None

    def ensure_open(self):
        if self._real is None:
            started = time.perf_counter()
            real = self._backend.open_session(self._config)
            if real is None or not callable(getattr(real, "generate", None)):
                raise RuntimeError("Backend open_session вернул некорректную session.")
            self.audio_spec.update(getattr(real, "audio_spec", None))
            self._real = real
            self._log(f"Модель реально загружена за {time.perf_counter() - started:.1f} сек.")
        return self._real

    def generate(self, request):
        return self.ensure_open().generate(request)


def _scope_epochs(payload: Mapping[str, Any]) -> dict[str, int]:
    result = {}
    raw = payload.get("scope_epochs")
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            fingerprint = _sha(key)
            try:
                epoch = _integer(value, "scope_epoch")
            except RuntimeError:
                continue
            if fingerprint and 0 <= epoch <= MAX_SCOPE_EPOCH:
                result[fingerprint] = max(result.get(fingerprint, 0), epoch)
    counts = {}
    for entry in payload.get("history") or []:
        if not isinstance(entry, Mapping):
            continue
        evidence = entry.get("evidence")
        fingerprint = _sha(
            evidence.get("failure_scope_fingerprint")
            if isinstance(evidence, Mapping)
            else ""
        )
        if not fingerprint:
            continue
        try:
            explicit = _integer(entry.get("scope_epoch_to"), "scope_epoch_to")
        except RuntimeError:
            explicit = 0
        counts[fingerprint] = min(
            MAX_SCOPE_EPOCH,
            max(counts.get(fingerprint, 0) + 1, explicit),
        )
    for key, value in counts.items():
        result[key] = max(result.get(key, 0), value)
    return result


def _marker_path(work_dir: Path, sid: int) -> Path:
    return Path(work_dir).resolve() / "timing_blocks" / f"segment_{sid:02d}.json"


def _archive(path: Path, reason: str) -> None:
    target = path.with_suffix(path.suffix + f".stale-{reason}-{uuid.uuid4().hex[:8]}")
    try:
        path.replace(target)
    except OSError:
        path.unlink(missing_ok=True)


def install_global_polish() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_preflight = guard.run_pre_model_guard
    original_persist = guard.persist_timing_block
    original_context = guard.load_signature_context
    original_invalidate = retry.invalidate_segment_for_retry

    def preflight(segments, *, work_dir, max_tempo, signature_context):
        return original_preflight(
            _segments(segments),
            work_dir=work_dir,
            max_tempo=max_tempo,
            signature_context=signature_context,
        )

    def context(work_dir):
        value = dict(original_context(work_dir))
        value.update(_runtime_marker(work_dir))
        value["surgical_polish_policy"] = POLICY
        return value

    def persist(work_dir, *, segment, signature_context, retry_epoch, evidence):
        clean = _segments([dict(segment)])[0]
        value = dict(original_persist(
            work_dir,
            segment=clean,
            signature_context=signature_context,
            retry_epoch=retry_epoch,
            evidence=evidence,
        ))
        value.update(
            schema_version=MARKER_SCHEMA_VERSION,
            policy=MARKER_POLICY,
            segment_id=int(clean["id"]),
            signature=guard.failure_scope_fingerprint(clean, signature_context=signature_context),
            speech_slot=round(clean["end"] - clean["start"] - clean["tail_guard"], 6),
            retry_epoch=_integer(retry_epoch, "retry_epoch"),
        )
        _atomic(_marker_path(work_dir, clean["id"]), value)
        return value

    def load_block(work_dir, *, segment, signature_context):
        clean = _segments([dict(segment)])[0]
        path = _marker_path(work_dir, clean["id"])
        if not path.is_file():
            return None
        value = _read(path)
        expected = guard.failure_scope_fingerprint(clean, signature_context=signature_context)
        slot = clean["end"] - clean["start"] - clean["tail_guard"]
        try:
            recommendation = value.get("recommendation")
            valid = bool(
                value.get("schema_version") == MARKER_SCHEMA_VERSION
                and value.get("policy") == MARKER_POLICY
                and _integer(value.get("segment_id"), "marker.id") == clean["id"]
                and _sha(value.get("signature")) == expected
                and 0 <= _integer(value.get("retry_epoch"), "marker.epoch") < MAX_SCOPE_EPOCH
                and abs(_number(value.get("speech_slot"), "marker.slot") - slot) <= 1e-6
                and isinstance(value.get("evidence"), dict)
                and isinstance(recommendation, Mapping)
                and _number(recommendation.get("hard_minimum_speech_slot"), "hard_slot") + 1e-6 >= slot
                and 0 <= _integer(recommendation.get("hard_shorten_percent"), "shorten") <= 100
            )
        except RuntimeError:
            valid = False
        if valid:
            return value
        _archive(path, "input-changed" if value.get("signature") != expected else "contract-mismatch")
        return None

    def invalidate(work_dir, segment, *, reason, fitted_path=None, evidence=None):
        evidence = dict(evidence or {})
        fingerprint = _sha(evidence.get("failure_scope_fingerprint"))
        result = dict(original_invalidate(
            work_dir,
            segment,
            reason=reason,
            fitted_path=fitted_path,
            evidence=evidence,
        ))
        result["raw_retry_epoch"] = int(result.get("retry_epoch") or 0)
        if fingerprint:
            epoch = retry.load_retry_epoch(
                work_dir,
                segment.get("id"),
                scope_fingerprint=fingerprint,
            )
            result.update(
                retry_epoch=epoch,
                scope_retry_epoch=epoch,
                last_scope_epoch=epoch,
                scope_fingerprint=fingerprint,
                policy=POLICY,
            )
        return result

    guard.run_pre_model_guard = preflight
    guard.load_signature_context = context
    guard.persist_timing_block = persist
    guard.load_matching_timing_block = load_block
    guard.MARKER_SCHEMA_VERSION = MARKER_SCHEMA_VERSION
    retry._scope_epochs = _scope_epochs
    retry.invalidate_segment_for_retry = invalidate
    sio.MutableAudioSpec = _AudioSpec
    sio.LazySession = _LazySession
    sio.cached_reference = _cached_reference
    sio.enrich_reference_report = _enrich_reference_report
    sio.POLICY = POLICY
    runtime._segments_by_id = _segments_by_id
    runtime._RUNTIME_SCOPE_FILES = tuple(dict.fromkeys((*runtime._RUNTIME_SCOPE_FILES, *_EXTRA_SCOPE)))
    runtime.POLICY = "voxcpm2-surgical-runtime-v2"
    _INSTALLED = True


__all__ = ["MARKER_SCHEMA_VERSION", "POLICY", "install_global_polish"]
