#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict compatibility facade for the universal Dub Studio wizard.

The existing UI/state machine remains in ``handlers/dub_wizard.py``. This
package routes URL parsing through the production single-video parser and
replaces only project creation so ``request.json`` is validated and atomically
written before a Gemini job can enter the queue.
"""
from __future__ import annotations

import html
import importlib.util
from pathlib import Path
from typing import Any

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
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))


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
    request = _legacy._request_payload(video_id, canonical_url, mode)
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
_legacy._write_request = _write_request
_legacy._create_generic_project = _create_generic_project

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"_create_generic_project", "_extract_youtube_video_id", "_write_request"}
)
