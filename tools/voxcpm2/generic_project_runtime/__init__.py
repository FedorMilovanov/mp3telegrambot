#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict compatibility facade for the universal Dub project runtime.

The established pipeline stays in ``generic_project_runtime.py``. This package
preserves its API while validating project/request identity and making every
runtime JSON write atomic and non-finite-safe.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as strict_core
from tools.voxcpm2 import clean_source_download

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "generic_project_runtime.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._generic_project_runtime_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить generic project runtime: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_ALLOWED_TRANSLATION_MODES = {"gemini", "custom", "direct"}


def project_root(project_id: str | None = None) -> Path:
    value = str(project_id or _legacy.current_project_id()).strip().lower()
    if not _legacy._PROJECT_RE.fullmatch(value):
        raise RuntimeError("Некорректный Dub Studio project ID.")
    root = (_legacy.studio_root() / "projects" / value).resolve()
    allowed = (_legacy.studio_root() / "projects").resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Project root escaped Dub Studio projects directory.") from exc
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_request(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    path = root / "request.json"
    if not path.is_file():
        raise RuntimeError(f"Не найден request.json проекта: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён request.json проекта: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("request.json должен быть JSON-объектом.")
    schema = strict_core._strict_int(
        payload.get("schema_version"),
        field="request.schema_version",
        low=1,
        high=1,
    )
    if schema != 1:
        raise RuntimeError("Неподдерживаемый request.json проекта.")

    video_id = str(payload.get("video_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
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
    mode = str(payload.get("translation_mode") or "").strip()
    if mode not in _ALLOWED_TRANSLATION_MODES:
        raise RuntimeError(f"Некорректный translation_mode={mode!r} в request.json.")
    return payload


def save_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}.{id(payload)}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# Legacy orchestration resolves these globals at call time.
_legacy.project_root = project_root
_legacy.load_request = load_request
_legacy.save_json = save_json

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"load_request", "project_root", "save_json"}
)
