#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio-only and segment-only repair controls for completed Dub Studio projects."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, studio_root, utc_now
from tools.voxcpm2 import clean_production_core as strict_core

_MSG_ONLY = filters.UpdateType.MESSAGE
_GENERIC_RECIPE = "generic_short_v1"
_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
_ACTIVE_JOB_STATES = {"queued", "running", "cancel_requested"}
_DUBFIX_LOCK = asyncio.Lock()
_DUBFIX_PROCESS_LOCK_STALE_SECONDS = 30 * 60


def _process_lock_path() -> Path:
    root = Path(studio_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / ".dubfix.request.lock"


@contextmanager
def _dubfix_process_lock() -> Iterator[Path]:
    """Hold an atomic cross-process lock for request-write + enqueue."""
    path = _process_lock_path()
    descriptor: int | None = None
    for attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError as exc:
            try:
                age = max(0.0, time.time() - path.stat().st_mtime)
            except FileNotFoundError:
                continue
            if age > _DUBFIX_PROCESS_LOCK_STALE_SECONDS and attempt == 0:
                path.unlink(missing_ok=True)
                continue
            raise RuntimeError(
                "Другой процесс уже создаёт /dubfix request; повторите команду после завершения."
            ) from exc
    if descriptor is None:
        raise RuntimeError("Не удалось захватить межпроцессный /dubfix lock.")
    try:
        payload = json.dumps(
            {"pid": os.getpid(), "acquired_unix": time.time()},
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        os.write(descriptor, payload)
        os.fsync(descriptor)
        yield path
    finally:
        try:
            os.close(descriptor)
        finally:
            path.unlink(missing_ok=True)


def _strict_ids(values: Any, *, field: str) -> list[int]:
    if not isinstance(values, (list, tuple)) or not values:
        raise RuntimeError(f"{field} должен быть непустым списком ID.")
    result: list[int] = []
    seen: set[int] = set()
    for position, value in enumerate(values, start=1):
        item_id = strict_core._strict_int(
            value, field=f"{field}[{position}]", low=1, high=2**31 - 1
        )
        if item_id in seen:
            raise RuntimeError(f"{field} содержит повторный ID={item_id}.")
        seen.add(item_id)
        result.append(item_id)
    return result


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
        raise RuntimeError(
            "У проекта ещё нет segments_ru_final.json; сначала завершите обычный рендер."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("segments_ru_final.json повреждён.") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Список реплик проекта пуст или повреждён.")
    result: list[dict[str, Any]] = []
    raw_ids: list[Any] = []
    for position, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(
                f"segment[{position}] должен быть JSON-объектом, получено {type(raw).__name__}."
            )
        item = dict(raw)
        raw_ids.append(item.get("id"))
        start = strict_core._finite(item.get("start"), field=f"segment[{position}].start")
        end = strict_core._finite(item.get("end"), field=f"segment[{position}].end")
        if start < 0.0 or end <= start:
            raise RuntimeError(f"Некорректный timing segment[{position}].")
        item["start"] = start
        item["end"] = end
        if item.get("source_end") is not None:
            source_end = strict_core._finite(
                item.get("source_end"), field=f"segment[{position}].source_end"
            )
            if source_end < start:
                raise RuntimeError(f"source_end segment[{position}] раньше start.")
            item["source_end"] = source_end
        item["start_delay_ms"] = strict_core._strict_int(
            item.get("start_delay_ms", 0),
            field=f"segment[{position}].start_delay_ms", low=0, high=1500,
        )
        if not str(item.get("text") or "").strip():
            raise RuntimeError(f"segment[{position}] не содержит текста.")
        result.append(item)
    ids = _strict_ids(raw_ids, field="segments.id")
    for item, segment_id in zip(result, ids, strict=True):
        item["id"] = segment_id
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
    if not isinstance(project, dict):
        raise RuntimeError("Project должен быть JSON-объектом.")
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        raise RuntimeError("Project ID пуст.")
    owner_id = strict_core._strict_int(
        requested_by, field="audio_repair.requested_by", low=1, high=2**63 - 1
    )
    all_ids = _strict_ids(
        [item.get("id") if isinstance(item, dict) else None for item in segments],
        field="segments.id",
    )
    selected = _strict_ids(selected_ids, field="audio_repair.segment_ids")
    selected_set = set(selected)
    all_set = set(all_ids)
    if not selected_set.issubset(all_set):
        raise RuntimeError("Выбраны неизвестные segment ID.")
    root = _project_root(project_id)
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    segments_path = _segments_path(project_id)
    if not segments_path.is_file():
        raise RuntimeError("segments_ru_final.json исчез до создания repair request.")
    digest = hashlib.sha256(segments_path.read_bytes()).hexdigest()
    payload = {
        "schema_version": 1,
        "project_id": project_id,
        "segment_ids": selected,
        "repair_all": selected_set == all_set,
        "segments_sha256": digest,
        "requested_by": owner_id,
        "requested_at": utc_now(),
    }
    destination = input_dir / "audio_repair.json"
    temporary = destination.with_name(destination.name + f".tmp.{os.getpid()}.{id(payload)}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
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


async def _dubfix_command_unlocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


async def dubfix_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _DUBFIX_LOCK:
        try:
            with _dubfix_process_lock():
                await _dubfix_command_unlocked(update, context)
        except RuntimeError as exc:
            if update.effective_message is None:
                raise
            await update.effective_message.reply_text(f"⚠️ {exc}")


def register_dub_audio_repair_handlers(application: Any) -> None:
    if application.bot_data.get("dub_audio_repair_handlers_registered"):
        return
    application.add_handler(CommandHandler("dubsegments", dubsegments_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubfix", dubfix_command, filters=_MSG_ONLY))
    application.bot_data["dub_audio_repair_handlers_registered"] = True


__all__ = [
    "_dubfix_process_lock",
    "_write_repair_request",
    "_ensure_repair_slot",
    "dubfix_command",
    "dubsegments_command",
    "load_repair_segments",
    "parse_segment_selector",
    "register_dub_audio_repair_handlers",
]
