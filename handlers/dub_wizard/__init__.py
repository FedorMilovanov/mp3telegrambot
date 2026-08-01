#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade for the explicit-profile Dub Studio wizard.

The state machine and Telegram UI live in ``handlers/dub_wizard.py``. This
package keeps historical import paths and optional function arguments stable,
routes YouTube parsing through the production parser, installs one validated
atomic request writer, and exposes safe inactive-project TTS rebinding.
"""
from __future__ import annotations

import html
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from services.speech_backends import DEFAULT_MODEL_PROFILE_ID
from services.tts_profile_selection import (
    production_tts_profile_choice,
    rebind_inactive_project_tts_profile,
    write_durable_request,
)
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
_legacy_dubtts_command = _legacy.dubtts_command


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


def _compat_profile_id(value: str | None) -> str:
    if value is not None and str(value).strip():
        return str(value).strip()
    return (
        str(os.getenv("DUB_SPEECH_MODEL_PROFILE", "") or "").strip()
        or DEFAULT_MODEL_PROFILE_ID
    )


def _request_payload(
    video_id: str,
    url: str,
    mode: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Preserve the old three-argument call while binding a concrete profile."""
    return dict(
        _legacy_request_payload(
            video_id,
            url,
            mode,
            _compat_profile_id(profile_id),
        )
    )


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
    profile_id: str | None = None,
) -> None:
    """Preserve the old call shape and delegate to the canonical state machine."""
    await _legacy_create_generic_project(
        update,
        context,
        url,
        mode,
        _compat_profile_id(profile_id),
    )


async def dubtts_command(update: Any, context: Any) -> None:
    """List profiles or rebind one inactive project.

    Usage: ``/dubtts`` or ``/dubtts PROJECT_ID PROFILE_ID``.
    """
    if not await _legacy._admin(update):
        return
    args = [str(value).strip() for value in (context.args or []) if str(value).strip()]
    if not args:
        await _legacy_dubtts_command(update, context)
        return
    if len(args) != 2:
        await update.effective_message.reply_text(
            "Использование:\n"
            "<code>/dubtts</code> — каталог моделей\n"
            "<code>/dubtts PROJECT_ID PROFILE_ID</code> — сменить модель у "
            "draft/failed/cancelled проекта.",
            parse_mode="HTML",
        )
        return

    project_id, profile_id = args[0].casefold(), args[1]
    try:
        store = _legacy.DubStore()
        request_path = _legacy._project_root(project_id) / "request.json"
        result = rebind_inactive_project_tts_profile(
            store,
            project_id,
            owner_user_id=update.effective_user.id,
            request_path=request_path,
            profile_value=profile_id,
        )
        choice = production_tts_profile_choice(result.choice.profile_id)
        status = "уже был закреплён" if not result.changed else "закреплён"
        await update.effective_message.reply_text(
            "🎙 <b>TTS-профиль проекта обновлён</b>\n\n"
            f"Проект: <code>{html.escape(result.project_id)}</code>\n"
            f"Профиль {status}: <code>{html.escape(choice.profile_id)}</code>\n"
            f"Backend: <code>{html.escape(choice.backend_id)}</code>\n"
            f"Revision: <code>{html.escape(choice.model_revision)}</code>\n"
            f"Fingerprint: <code>{html.escape(choice.fingerprint[:12])}</code>\n\n"
            "Изменение разрешено только без active job. Параметры TTS нового "
            "профиля сброшены к его валидированным defaults.",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ TTS-профиль не изменён: "
            + html.escape(_legacy._short(str(exc), 1400)),
            parse_mode="HTML",
        )


# Raw callbacks resolve these globals at execution time. Patch only compatibility
# seams; the raw module remains the single owner of UI and state transitions.
_legacy._extract_youtube_video_id = _extract_youtube_video_id
_legacy._request_payload = _request_payload
_legacy.write_durable_request = _validated_atomic_write
_legacy._write_request = _write_request
_legacy._create_generic_project = _create_generic_project
_legacy.dubtts_command = dubtts_command

# Explicit assignments override names copied from the raw module above.
globals()["_extract_youtube_video_id"] = _extract_youtube_video_id
globals()["_request_payload"] = _request_payload
globals()["_write_request"] = _write_request
globals()["_create_generic_project"] = _create_generic_project
globals()["dubtts_command"] = dubtts_command

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "_create_generic_project",
        "_extract_youtube_video_id",
        "_request_payload",
        "_validated_atomic_write",
        "_write_request",
        "dubtts_command",
        "generic_project_runtime",
    }
)
