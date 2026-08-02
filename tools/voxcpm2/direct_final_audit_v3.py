#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final fail-closed audit layer for every direct VoxCPM2 CLI invocation.

This layer closes gaps that can exist between raw JSON input, the already
normalised renderer state and the outer bot preflight. It deliberately wraps
only public direct-CLI boundaries and keeps the previously audited base
implementations unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, Callable

from tools.voxcpm2 import direct_retry_epoch as retry
from tools.voxcpm2 import direct_timing_guard as guard

POLICY = "voxcpm2-final-audit-v3"
MAX_SEGMENTS_BYTES = 8 * 1024 * 1024
MAX_ARCHIVED_MARKERS = 8

_INSTALLED_NAMESPACES: set[int] = set()
_GUARD_PATCHED = False
_RETRY_PATCHED = False
_MODULE_SHA256 = ""


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{name} не может быть bool.")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise RuntimeError(f"{name} должен быть целым числом.")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {name}: {value!r}") from exc


def _strict_segment_id(value: Any) -> int:
    result = _integer(value, "segment_id")
    maximum = int(getattr(retry, "MAX_SEGMENT_ID", 1_000_000_000))
    if not 1 <= result <= maximum:
        raise RuntimeError(
            f"segment_id должен быть в диапазоне 1..{maximum}: {result}."
        )
    return result


def _raw_segments(path: Path) -> list[dict[str, Any]]:
    source = Path(path).resolve()
    try:
        if not source.is_file():
            raise RuntimeError(f"Не найден segments JSON: {source}")
        if source.stat().st_size > MAX_SEGMENTS_BYTES:
            raise RuntimeError(f"segments JSON слишком велик: {source}")
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Повреждён segments JSON: {source}") from exc
    except OSError as exc:
        raise RuntimeError(f"Не удалось прочитать segments JSON: {source}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments JSON должен содержать непустой список.")

    result: list[dict[str, Any]] = []
    for position, raw in enumerate(payload, 1):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"Сегмент #{position} должен быть JSON-объектом.")
        item = dict(raw)
        if "id" in item:
            _integer(item["id"], f"segment[{position}].id")
        for field in ("start", "end", "tail_guard"):
            if isinstance(item.get(field), bool):
                raise RuntimeError(f"segment[{position}].{field} не может быть bool.")
        if "start_delay_ms" in item:
            _integer(item["start_delay_ms"], f"segment[{position}].start_delay_ms")
        result.append(item)
    return result


def _module_sha256(hash_file: Callable[[Path], str]) -> str:
    global _MODULE_SHA256
    if not _MODULE_SHA256:
        value = str(hash_file(Path(__file__).resolve())).strip().casefold()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise RuntimeError("Некорректный SHA final-audit module.")
        _MODULE_SHA256 = value
    return _MODULE_SHA256


def _model_context(
    model_path: Path,
    hash_file: Callable[[Path], str],
) -> dict[str, Any]:
    model = Path(model_path).resolve()
    config = model / "config.json"
    config_sha = str(hash_file(config)).strip().casefold() if config.is_file() else ""
    files: list[dict[str, Any]] = []
    for pattern in ("*.safetensors", "*.bin", "*.json"):
        for item in sorted(model.glob(pattern), key=lambda value: value.name.casefold()):
            try:
                stat = item.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": item.name,
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
    encoded = json.dumps(
        {
            "path": str(model),
            "config_sha256": config_sha,
            "files": files,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "direct_model_path": str(model),
        "direct_model_snapshot": model.name,
        "direct_model_config_sha256": config_sha,
        "direct_model_snapshot_fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


def _marker_id(segment: Any) -> int:
    if not isinstance(segment, Mapping):
        return 0
    try:
        return _strict_segment_id(segment.get("id"))
    except RuntimeError:
        return 0


def _prune_timing_archives(work_dir: Path, segment_id: int) -> None:
    try:
        sid = _strict_segment_id(segment_id)
    except RuntimeError:
        return
    marker = Path(work_dir).resolve() / "timing_blocks" / f"segment_{sid:02d}.json"
    candidates = list(marker.parent.glob(marker.name + ".stale-*"))

    def modified(path: Path) -> int:
        try:
            return int(path.stat().st_mtime_ns)
        except OSError:
            return 0

    candidates.sort(key=modified, reverse=True)
    for stale in candidates[MAX_ARCHIVED_MARKERS:]:
        stale.unlink(missing_ok=True)


def _patch_shared_contracts() -> None:
    global _GUARD_PATCHED, _RETRY_PATCHED
    if not _RETRY_PATCHED:
        retry._strict_segment_id = _strict_segment_id
        _RETRY_PATCHED = True

    if not _GUARD_PATCHED:
        original_load = guard.load_matching_timing_block

        def load_matching_timing_block(
            work_dir: Path,
            *,
            segment: Mapping[str, Any],
            signature_context: Mapping[str, Any] | None,
        ) -> dict[str, Any] | None:
            try:
                return original_load(
                    work_dir,
                    segment=segment,
                    signature_context=signature_context,
                )
            finally:
                sid = _marker_id(segment)
                if sid:
                    _prune_timing_archives(work_dir, sid)

        guard.load_matching_timing_block = load_matching_timing_block
        _GUARD_PATCHED = True


def install_final_audit(namespace: MutableMapping[str, Any]) -> None:
    """Install raw-input, pre-reference and model-scope checks on one CLI namespace."""
    identity = id(namespace)
    if identity in _INSTALLED_NAMESPACES or namespace.get("FINAL_AUDIT_POLICY") == POLICY:
        return

    required = (
        "read_segments",
        "prepare_reference",
        "get_backend",
        "sha256_file",
        "MAX_TEMPO",
        "log",
    )
    missing = [name for name in required if name not in namespace]
    if missing:
        raise RuntimeError("final direct audit contract missing: " + ", ".join(missing))

    original_read: Callable[..., Any] = namespace["read_segments"]
    original_prepare: Callable[..., Any] = namespace["prepare_reference"]
    original_get_backend: Callable[..., Any] = namespace["get_backend"]
    hash_file: Callable[[Path], str] = namespace["sha256_file"]
    log: Callable[[str], Any] = namespace["log"]
    max_tempo = float(namespace["MAX_TEMPO"])
    state: dict[str, Any] = {
        "segments": [],
        "segments_json": None,
        "segments_json_sha256": "",
        "work_dir": None,
        "preflight_done": False,
        "model_context": {},
    }

    _patch_shared_contracts()

    def base_context() -> dict[str, Any]:
        return {
            "final_audit_policy": POLICY,
            "final_audit_sha256": _module_sha256(hash_file),
            "segments_json_sha256": state.get("segments_json_sha256") or "",
            **dict(state.get("model_context") or {}),
        }

    def persist_context() -> dict[str, Any]:
        work = state.get("work_dir")
        if work is None:
            return base_context()
        current = dict(guard.load_signature_context(Path(work)))
        current.update(base_context())
        guard.write_signature_context(Path(work), current)
        return current

    def read_segments(path: Path) -> list[dict[str, Any]]:
        source = Path(path).resolve()
        _raw_segments(source)
        values = list(original_read(source))
        if not values:
            raise RuntimeError("Direct renderer получил пустой список сегментов.")
        state.update(
            segments=values,
            segments_json=source,
            segments_json_sha256=str(hash_file(source)),
            work_dir=None,
            preflight_done=False,
            model_context={},
        )
        return values

    def prepare_reference(source: Path, output: Path, sf_module: Any) -> dict[str, Any]:
        target = Path(output).resolve()
        work = (
            target.parent.parent
            if target.parent.name == "references_guarded"
            else target.parent
        )
        state["work_dir"] = work
        if not bool(state.get("preflight_done")):
            segments = list(state.get("segments") or [])
            if not segments:
                raise RuntimeError("Direct timing preflight вызван до read_segments.")
            context = persist_context()
            report = guard.run_pre_model_guard(
                segments,
                work_dir=work,
                max_tempo=max_tempo,
                signature_context=context,
            )
            state["preflight_done"] = True
            warnings = report.get("warning_ids") if isinstance(report, Mapping) else []
            log(
                "direct final timing preflight passed before references/model: "
                f"warnings={warnings or []}"
            )
        return dict(original_prepare(source, output, sf_module))

    def get_backend(name: str) -> Any:
        backend = original_get_backend(name)
        if str(getattr(backend, "backend_id", "")).strip().casefold() != "voxcpm2":
            return backend
        if bool(getattr(backend, "_final_audit_v3_wrapped", False)):
            return backend
        discover = getattr(backend, "discover_model", None)
        if not callable(discover):
            raise RuntimeError("VoxCPM2 backend не содержит discover_model().")

        def discover_model(archive_root: Path) -> Path:
            model = Path(discover(archive_root)).resolve()
            state["model_context"] = _model_context(model, hash_file)
            persist_context()
            return model

        backend.discover_model = discover_model
        backend._final_audit_v3_wrapped = True
        return backend

    namespace["read_segments"] = read_segments
    namespace["prepare_reference"] = prepare_reference
    namespace["get_backend"] = get_backend
    namespace["FINAL_AUDIT_POLICY"] = POLICY
    namespace["_FINAL_AUDIT_STATE"] = state
    _INSTALLED_NAMESPACES.add(identity)


__all__ = [
    "MAX_ARCHIVED_MARKERS",
    "POLICY",
    "install_final_audit",
]
