#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade for the explicit-profile Dub Studio wizard.

The state machine and Telegram UI live in ``handlers/dub_wizard.py``. This
package keeps historical import paths and optional function arguments stable,
routes YouTube parsing through the production parser, and installs one
validated atomic request writer. It does not duplicate TTS selection logic.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from services.speech_backends import DEFAULT_MODEL_PROFILE_ID
from services.tts_profile_selection import write_durable_request
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import generic_project_runtime

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_wizard.py"
_SPEC = importlib.util.spec_from_file_location(
    "handlers._dub_wizard_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый Dub wizard: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_request_payload = _legacy._request_payload
_legacy_create_generic_project = _legacy._create_generic_project


def _extract_youtube_video_id(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    video_id = clean_source_download._url_video_id(raw)
    if not video_id:
        raise ValueError(
            "Нужна каноническая ссылка на один YouTube-ролик: "
            "watch, youtu.be, Shorts, live или embed."
        )
    return video_id, f"https://youtube.com/watch?v={video_id}"


def _request_payload(
    video_id: str,
    url: str,
    mode: str,
    profile_id: str = DEFAULT_MODEL_PROFILE_ID,
) -> dict[str, Any]:
    """Preserve the old three-argument call while binding an explicit profile."""
    return dict(_legacy_request_payload(video_id, url, mode, profile_id))


def _validated_atomic_write(destination: Path, payload: Any) -> Path:
    validated = generic_project_runtime.validate_request_payload(payload)
    target = Path(destination).resolve()
    write_durable_request(target, validated)
    return target


def _write_request(project_id: str, payload: Any) -> Path:
    """Historical request-writer seam backed by the new atomic service."""
    root = generic_project_runtime.project_root(project_id)
    destination = (root / "request.json").resolve()
    return _validated_atomic_write(destination, payload)


async def _create_generic_project(
    update: Any,
    context: Any,
    url: str,
    mode: str,
    profile_id: str = DEFAULT_MODEL_PROFILE_ID,
) -> None:
    """Preserve the old call shape and delegate to the canonical state machine."""
    await _legacy_create_generic_project(update, context, url, mode, profile_id)


# Raw callbacks resolve these globals at execution time. Patch only compatibility
# seams; the raw module remains the single owner of UI and state transitions.
_legacy._extract_youtube_video_id = _extract_youtube_video_id
_legacy._request_payload = _request_payload
_legacy.write_durable_request = _validated_atomic_write
_legacy._write_request = _write_request
_legacy._create_generic_project = _create_generic_project

# Explicit assignments override names copied from the raw module above.
globals()["_extract_youtube_video_id"] = _extract_youtube_video_id
globals()["_request_payload"] = _request_payload
globals()["_write_request"] = _write_request
globals()["_create_generic_project"] = _create_generic_project

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "_create_generic_project",
        "_extract_youtube_video_id",
        "_request_payload",
        "_validated_atomic_write",
        "_write_request",
        "generic_project_runtime",
    }
)
