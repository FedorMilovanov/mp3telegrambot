#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio-only and segment-only repair controls for completed Dub Studio projects."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, studio_root, utc_now

_MSG_ONLY = filters.UpdateType.MESSAGE
_GENERIC_RECIPE = "generic_short_v1"
_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
_ACTIVE_JOB_STATES = {"queued", "running", "cancel_requested"}


def _short(value: str, limit: int = 180) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Ремонт Dub Studio доступен только администратору.")
    return False


def _project_owned_by(project: dict[str, Any], user_id: int) -> bool:
    return int(project.get("owner_user_id") or 0) == int(user_id)


def _resolve_project(store: DubStore, user_id: int, token: str | None) -> dict[str, Any]:
    token = str(token or "").strip().lower()
    project = store.get_project(token) if token and token not in {"last", "последний"} else store.latest_project(owner_user_id=user_id)
    if not project:
        raise KeyError("Проект Dub Studio не найден.")
    if not _project_owned_by(project, user_id):
        raise PermissionError("Это не ваш проект.")
    if str(project.get("recipe_id")) != _GENERIC_RECIPE:
        raise RuntimeError("Аудиоремонт поддерживается только универсальным Dub Studio.")
    return project


def _ensure_repair_slot(store: DubStore, project_id: str) -> None:
    """Do not overwrite the request file of an already queued/running job."""
    for job in store.recent_jobs(project_id, limit=8):
        if str(job.get("status") or "").lower() in _ACTIVE_JOB_STATES:
            raise RuntimeError(
                f"У проекта уже выполняется задание #{job['id']}. "
                "Дождитесь завершения или остановите его через /dubcancel."
            )


def _project_root(project_id: str) -> Path:
    allowed = (studio_root() / "projects").resolve()
    root = (allowed / str(project_id)).resolve()
    root.relative_to(allowed)
    return root


def _segments_path(project_id: str) -> Path:
    return _project_root(project_id) / "segments_ru_final.json"


def load_repair_segments(project_id: str) -> list[dict[str, Any]]:
    path = _segments_path(project_id)
    if not path.is_file():
        raise RuntimeError("У проекта ещё нет segments_ru_final.json; сначала завершите обычный рендер.")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Список реплик проекта пуст или повреждён.")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise RuntimeError("Некорректная запись реплики в segments_ru_final.json.")
        item = dict(raw)
        segment_id = int(item.get("id") or 0)
        if segment_id <= 0 or segment_id in seen:
            raise RuntimeError("Некорректные или повторяющиеся ID реплик.")
        seen.add(segment_id)
        result.append(item)
    return sorted(result, key=lambda item: int(item["id"]))


def parse_segment_selector(value: str, available_ids: Iterable[int]) -> list[int]:
    available = sorted({int(item) for item in available_ids})
    allowed = set(available)
    selector = re.sub(r"\s+", "", str(value or "").casefold())
    if selector in {"all", "все", "всё"}:
        return available
    if not selector:
        raise ValueError("Не указаны номера реплик.")

    selected: set[int] = set()
    for token in selector.split(","):
        if not token:
            continue
        match = _RANGE_RE.fullmatch(token)
        if not match:
            raise ValueError(f"Некорректный диапазон: {token}")
        left = int(match.group(1))
        right = int(match.group(2) or left)
        if right < left:
            left, right = right, left
        if right - left > 100:
            raise ValueError("Один диапазон не может содержать больше 101 реплики.")
        selected.update(range(left, right + 1))
    missing = sorted(selected - allowed)
    if missing:
        raise ValueError("В проекте нет реплик: " + ", ".join(map(str, missing[:20])))
    if not selected:
        raise ValueError("Не выбрана ни одна реплика.")
    return sorted(selected)


def _segments_text(project_id: str, segments: list[dict[str, Any]]) -> str:
    lines = [
        f"🎚 <b>Реплики проекта {html.escape(project_id)}</b>",
        "",
    ]
    for item in segments[:60]:
        segment_id = int(item["id"])
        start = float(item.get("start") or 0.0) + max(0, int(item.get("start_delay_ms") or 0)) / 1000.0
        source_end = float(item.get("source_end") or item.get("end") or start)
        text = _short(str(item.get("display_text") or item.get("text") or ""), 115)
        lines.append(
            f"<code>{segment_id}</code> · {start:.2f}–{source_end:.2f} · {html.escape(text)}"
        )
    if len(segments) > 60:
        lines.append(f"… ещё {len(segments) - 60} реплик")
    lines.extend(
        [
            "",
            f"Только выбранные: <code>/dubfix {html.escape(project_id)} 2,4-5</code>",
            f"Весь звук без Gemini: <code>/dubfix {html.escape(project_id)} all</code>",
            "Текст, перевод, название и субтитры не создаются заново.",
        ]
    )
    return "\n".join(lines)


def _write_repair_request(
    project: dict[str, Any],
    segments: list[dict[str, Any]],
    selected_ids: list[int],
    *,
    requested_by: int,
) -> Path:
    root = _project_root(str(project["id"]))
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    segments_path = _segments_path(str(project["id"]))
    digest = hashlib.sha256(segments_path.read_bytes()).hexdigest()
    all_ids = [int(item["id"]) for item in segments]
    payload = {
        "schema_version": 1,
        "project_id": str(project["id"]),
        "segment_ids": selected_ids,
        "repair_all": selected_ids == all_ids,
        "segments_sha256": digest,
        "requested_by": int(requested_by),
        "requested_at": utc_now(),
    }
    destination = input_dir / "audio_repair.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


async def dubsegments_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    try:
        store = DubStore()
        project = _resolve_project(store, update.effective_user.id, (context.args or [None])[0])
        segments = load_repair_segments(str(project["id"]))
        await update.effective_message.reply_text(
            _segments_text(str(project["id"]), segments),
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.effective_message.reply_text("⚠️ " + html.escape(_short(str(exc), 1000)), parse_mode="HTML")


async def dubfix_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    args = list(context.args or [])
    try:
        store = DubStore()
        project = _resolve_project(store, update.effective_user.id, args[0] if args else None)
        project_id = str(project["id"])
        segments = load_repair_segments(project_id)
        if len(args) < 2:
            await update.effective_message.reply_text(_segments_text(project_id, segments), parse_mode="HTML")
            return

        selected = parse_segment_selector("".join(args[1:]), [int(item["id"]) for item in segments])
        _ensure_repair_slot(store, project_id)
        request_path = _write_repair_request(
            project,
            segments,
            selected,
            requested_by=update.effective_user.id,
        )
        try:
            job = store.enqueue_job(project_id, "repair_audio")
        except Exception:
            request_path.unlink(missing_ok=True)
            raise

        full_repair = len(selected) == len(segments)
        selected_text = "все" if full_repair else ", ".join(map(str, selected))
        details = (
            [
                "Старые TTS checkpoints и rescue-marker будут удалены.",
                "Референсы голоса будут заново выбраны из спокойных чистых фрагментов.",
                "Весь звук будет создан прямым NoChew renderer без wrapper-цепочки.",
            ]
            if full_repair
            else [
                "Выбранные реплики получают новый seed.",
                "Остальные берутся из успешного clean checkpoint baseline.",
            ]
        )
        await update.effective_message.reply_text(
            "\n".join(
                [
                    f"🩹 <b>Чистый аудиоремонт поставлен в очередь: #{job['id']}</b>",
                    f"Проект: <code>{html.escape(project_id)}</code>",
                    f"Реплики: <code>{html.escape(selected_text)}</code>",
                    "",
                    "Gemini, перевод и заголовок повторно не запускаются.",
                    *details,
                ]
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.effective_message.reply_text("⚠️ " + html.escape(_short(str(exc), 1100)), parse_mode="HTML")


def register_dub_audio_repair_handlers(application: Any) -> None:
    if application.bot_data.get("dub_audio_repair_handlers_registered"):
        return
    application.add_handler(CommandHandler("dubsegments", dubsegments_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubfix", dubfix_command, filters=_MSG_ONLY))
    application.bot_data["dub_audio_repair_handlers_registered"] = True


__all__ = [
    "_ensure_repair_slot",
    "dubfix_command",
    "dubsegments_command",
    "load_repair_segments",
    "parse_segment_selector",
    "register_dub_audio_repair_handlers",
]
