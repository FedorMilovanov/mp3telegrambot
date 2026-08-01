#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict compatibility facade for the universal Dub Studio wizard.

The existing UI/state machine remains in ``handlers/dub_wizard.py``. This
package routes URL parsing through the production single-video parser and
replaces project creation so ``request.json`` is validated and atomically
written before a job can enter the queue. TTS selection is expressed as a
backend adapter, a concrete model profile and typed JSON option maps.
"""
from __future__ import annotations

import html
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from services.speech_backends import DEFAULT_BACKEND_ID, DEFAULT_MODEL_PROFILE_ID
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


def _env_json_object(name: str) -> dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} содержит некорректный JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name} должен содержать JSON-объект.")
    return dict(payload)


def _request_payload(video_id: str, url: str, mode: str) -> dict[str, Any]:
    """Create a model-neutral request while preserving legacy env overrides."""
    request = dict(_legacy_request_payload(video_id, url, mode))
    for key in ("threads", "steps", "cfg", "cache_length", "base_seed"):
        request.pop(key, None)
    for key in ("vox_archive", "cpu_venv"):
        request.pop(key, None)

    request["speech_backend"] = (
        os.getenv("DUB_SPEECH_BACKEND", DEFAULT_BACKEND_ID).strip()
        or DEFAULT_BACKEND_ID
    )
    request["speech_model_profile"] = (
        os.getenv("DUB_SPEECH_MODEL_PROFILE", DEFAULT_MODEL_PROFILE_ID).strip()
        or DEFAULT_MODEL_PROFILE_ID
    )

    options = _env_json_object("DUB_TTS_OPTIONS_JSON")
    legacy_options = {
        "DUB_VOX_THREADS": ("threads", int),
        "DUB_VOX_STEPS": ("steps", int),
        "DUB_VOX_CFG": ("cfg", float),
        "DUB_VOX_CACHE_LENGTH": ("cache_length", int),
        "DUB_VOX_BASE_SEED": ("base_seed", int),
    }
    for env_name, (option_name, parser) in legacy_options.items():
        if env_name in os.environ and option_name not in options:
            try:
                options[option_name] = parser(os.environ[env_name])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"{env_name} содержит некорректное значение.") from exc
    request["speech_options"] = options

    backend_config = _env_json_object("DUB_TTS_BACKEND_CONFIG_JSON")
    legacy_config = {
        "DUB_VOX_ARCHIVE": "vox_archive",
        "DUB_CPU_VENV": "cpu_venv",
    }
    for env_name, config_name in legacy_config.items():
        if env_name in os.environ and config_name not in backend_config:
            backend_config[config_name] = os.environ[env_name]
    request["speech_backend_config"] = backend_config
    return request


def _write_request(project_id: str, payload: Any) -> Path:
    root = generic_project_runtime.project_root(project_id)
    validated = generic_project_runtime.validate_request_payload(payload)
    destination = (root / "request.json").resolve()
    generic_project_runtime.save_json(destination, validated)
    return destination


async def _create_generic_project(
    update: Any,
    context: Any,
    url: str,
    mode: str,
) -> None:
    """Preserve the established wizard flow with a durable request barrier."""
    video_id, canonical_url = _extract_youtube_video_id(url)
    store = _legacy.DubStore()
    mode_label = "Gemini MAX" if mode == _legacy._GEMINI_MODE else "мой готовый SRT"
    project = store.create_project(
        _legacy._GENERIC_RECIPE,
        owner_user_id=update.effective_user.id,
        owner_chat_id=update.effective_chat.id,
        title=f"Видео {video_id} — {mode_label}",
        metadata={"video_id": video_id, "translation_mode": mode},
    )
    project_id = str(project["id"])
    request = _request_payload(video_id, canonical_url, mode)
    try:
        _write_request(project_id, request)
    except Exception:
        # A project without a request is not runnable. Keep the record visible
        # for diagnosis, but never enqueue a job whose durable input barrier
        # failed.
        raise

    if mode == _legacy._GEMINI_MODE:
        job = store.enqueue_job(project_id, "render_gemini")
        context.user_data.pop(_legacy._WIZARD_KEY, None)
        await update.effective_message.reply_text(
            "🚀 <b>Gemini MAX запущен</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            f"Видео: <code>{html.escape(video_id)}</code>\n"
            f"Задание: <b>#{job['id']}</b>\n\n"
            "Бот сам выберет лучший источник субтитров, выполнит многоступенчатый "
            "перевод, озвучит и пришлёт готовый MP4.\n\n"
            f"Статус: <code>/dubstatus {html.escape(project_id)}</code>",
            parse_mode="HTML",
        )
        return

    context.user_data[_legacy._WIZARD_KEY] = {
        "awaiting": "srt",
        "mode": _legacy._DIRECT_MODE,
        "project_id": project_id,
    }
    await update.effective_message.reply_text(
        "✅ <b>Ссылка принята</b>\n\n"
        f"Проект: <code>{html.escape(project_id)}</code>\n"
        f"Видео: <code>{html.escape(video_id)}</code>\n\n"
        "Теперь пришлите готовый русский файл <code>.srt</code>. "
        "Сразу после загрузки бот поставит озвучивание и сборку ролика в очередь. "
        "Никакой расшифровки, шаблонов и проверки перевода Gemini не будет.",
        parse_mode="HTML",
        reply_markup=_legacy._srt_keyboard(project_id),
    )


# Legacy callbacks resolve these globals at execution time.
_legacy._extract_youtube_video_id = _extract_youtube_video_id
_legacy._request_payload = _request_payload
_legacy._write_request = _write_request
_legacy._create_generic_project = _create_generic_project

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "_create_generic_project",
        "_env_json_object",
        "_extract_youtube_video_id",
        "_request_payload",
        "_write_request",
    }
)
