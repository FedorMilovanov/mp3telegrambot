#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict write-through facade for the universal Dub project runtime.

The established orchestration remains in ``generic_project_runtime.py``. Clean
Gemini/custom entrypoints configure that orchestration by assigning adapter
functions immediately before calling ``main()``. Because this package shadows
the sibling module, assignments must be mirrored into the legacy module whose
function globals are actually executed. This facade also keeps request identity
strict and JSON writes atomic, collision-safe and non-finite-safe.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import types
from typing import Any
import uuid

from services.speech_backends import DEFAULT_BACKEND_ID, resolve_backend_id
from tools.voxcpm2 import clean_source_download

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "generic_project_runtime.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._generic_project_runtime_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить generic project runtime: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "generic-project-runtime-write-through-v2"
_ALLOWED_TRANSLATION_MODES = {"gemini", "custom", "direct"}
_PROTECTED_HOOKS = {"project_root", "load_request", "save_json", "validate_request_payload"}


def _strict_schema_int(value: Any, *, field: str, low: int, high: int) -> int:
    """Validate control-plane integers without importing the synthesis engine."""
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


def project_root(project_id: str | None = None) -> Path:
    value = str(project_id or _legacy.current_project_id()).strip().lower()
    if not _legacy._PROJECT_RE.fullmatch(value):
        raise RuntimeError("Некорректный Dub Studio project ID.")
    allowed = (_legacy.studio_root() / "projects").resolve()
    root = (allowed / value).resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Project root escaped Dub Studio projects directory.") from exc
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_request_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("request.json должен быть JSON-объектом.")
    result = dict(payload)
    schema = _strict_schema_int(
        result.get("schema_version"),
        field="request.schema_version",
        low=1,
        high=1,
    )
    if schema != 1:
        raise RuntimeError("Неподдерживаемый request.json проекта.")
    result["schema_version"] = schema

    video_id = str(result.get("video_id") or "").strip()
    source_url = str(result.get("source_url") or "").strip()
    if not _legacy._VIDEO_ID_RE.fullmatch(video_id):
        raise RuntimeError("Некорректный video_id в request.json.")
    url_video_id = clean_source_download._url_video_id(source_url)
    if not url_video_id:
        raise RuntimeError("request.json содержит неканонический YouTube source_url.")
    if url_video_id != video_id:
        raise RuntimeError(
            "request.json source_url и video_id указывают на разные ролики: "
            f"url={url_video_id}, video_id={video_id}."
        )
    mode = str(result.get("translation_mode") or "").strip().lower()
    if mode not in _ALLOWED_TRANSLATION_MODES:
        raise RuntimeError(f"Некорректный translation_mode={mode!r} в request.json.")
    try:
        backend_id = resolve_backend_id(
            result.get("speech_backend") or DEFAULT_BACKEND_ID
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"Некорректный speech_backend={result.get('speech_backend')!r} в request.json."
        ) from exc
    result["video_id"] = video_id
    result["source_url"] = source_url
    result["translation_mode"] = mode
    result["speech_backend"] = backend_id
    return result


def load_request(root: Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    path = root / "request.json"
    if not path.is_file():
        raise RuntimeError(f"Не найден request.json проекта: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён request.json проекта: {path}") from exc
    return validate_request_payload(payload)


def save_json(path: Path, payload: Any) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        path.name + f".tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# Legacy orchestration resolves these globals at call time.
_legacy.project_root = project_root
_legacy.validate_request_payload = validate_request_payload
_legacy.load_request = load_request
_legacy.save_json = save_json


class _WriteThroughModule(types.ModuleType):
    """Mirror clean-route adapter assignments into legacy function globals."""

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PROTECTED_HOOKS:
            expected = globals().get(name)
            if expected is not None and value is not expected:
                raise RuntimeError(f"Strict project runtime hook {name} cannot be replaced.")
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str) -> Any:
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "POLICY",
        "load_request",
        "project_root",
        "save_json",
        "validate_request_payload",
    }
)
