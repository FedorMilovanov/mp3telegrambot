#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed compatibility facade for Dub audio-repair commands.

The established Telegram UI remains in ``handlers/dub_audio_repair.py``. This
package preserves it while making segment loading and request writing strict,
and serializing concurrent ``/dubfix`` commands in-process and across bot
processes so one request file cannot be overwritten by two queued jobs.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.util
import sys
import json
import os
from pathlib import Path
import time
from typing import Any, Iterator

from tools.voxcpm2 import clean_production_core as strict_core

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_audio_repair.py"
_SPEC = importlib.util.spec_from_file_location(
    "handlers._dub_audio_repair_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub audio repair handler: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_dubfix_command = _legacy.dubfix_command
_DUBFIX_LOCK = asyncio.Lock()
_DUBFIX_PROCESS_LOCK_STALE_SECONDS = 30 * 60


def _process_lock_path() -> Path:
    root = Path(_legacy.studio_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / ".dubfix.request.lock"


@contextmanager
def _dubfix_process_lock() -> Iterator[Path]:
    """Hold an atomic cross-process lock for request-write + enqueue."""
    path = _process_lock_path()
    descriptor: int | None = None
    for attempt in range(2):
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
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
            {
                "pid": os.getpid(),
                "acquired_unix": time.time(),
            },
            ensure_ascii=False,
            allow_nan=False,
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
            value,
            field=f"{field}[{position}]",
            low=1,
            high=2**31 - 1,
        )
        if item_id in seen:
            raise RuntimeError(f"{field} содержит повторный ID={item_id}.")
        seen.add(item_id)
        result.append(item_id)
    return result


def load_repair_segments(project_id: str) -> list[dict[str, Any]]:
    path = _legacy._segments_path(project_id)
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
                f"segment[{position}] должен быть JSON-объектом, "
                f"получено {type(raw).__name__}."
            )
        item = dict(raw)
        raw_ids.append(item.get("id"))
        start = strict_core._finite(
            item.get("start"),
            field=f"segment[{position}].start",
        )
        end = strict_core._finite(
            item.get("end"),
            field=f"segment[{position}].end",
        )
        if start < 0.0 or end <= start:
            raise RuntimeError(f"Некорректный timing segment[{position}].")
        item["start"] = start
        item["end"] = end
        if item.get("source_end") is not None:
            source_end = strict_core._finite(
                item.get("source_end"),
                field=f"segment[{position}].source_end",
            )
            if source_end < start:
                raise RuntimeError(f"source_end segment[{position}] раньше start.")
            item["source_end"] = source_end
        item["start_delay_ms"] = strict_core._strict_int(
            item.get("start_delay_ms", 0),
            field=f"segment[{position}].start_delay_ms",
            low=0,
            high=1500,
        )
        if not str(item.get("text") or "").strip():
            raise RuntimeError(f"segment[{position}] не содержит текста.")
        result.append(item)

    ids = _strict_ids(raw_ids, field="segments.id")
    for item, segment_id in zip(result, ids, strict=True):
        item["id"] = segment_id
    return sorted(result, key=lambda item: int(item["id"]))


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
        requested_by,
        field="audio_repair.requested_by",
        low=1,
        high=2**63 - 1,
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

    root = _legacy._project_root(project_id)
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    segments_path = _legacy._segments_path(project_id)
    if not segments_path.is_file():
        raise RuntimeError("segments_ru_final.json исчез до создания repair request.")
    digest = _legacy.hashlib.sha256(segments_path.read_bytes()).hexdigest()
    payload = {
        "schema_version": 1,
        "project_id": project_id,
        "segment_ids": selected,
        "repair_all": selected_set == all_set,
        "segments_sha256": digest,
        "requested_by": owner_id,
        "requested_at": _legacy.utc_now(),
    }
    destination = input_dir / "audio_repair.json"
    temporary = destination.with_name(
        destination.name + f".tmp.{os.getpid()}.{id(payload)}"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


async def dubfix_command(update: Any, context: Any) -> None:
    async with _DUBFIX_LOCK:
        try:
            with _dubfix_process_lock():
                await _legacy_dubfix_command(update, context)
        except RuntimeError as exc:
            message = getattr(update, "effective_message", None)
            if message is None:
                raise
            await message.reply_text(f"⚠️ {exc}")


# Legacy callbacks resolve these globals at execution/registration time.
_legacy.load_repair_segments = load_repair_segments
_legacy._write_repair_request = _write_repair_request
_legacy.dubfix_command = dubfix_command

register_dub_audio_repair_handlers = _legacy.register_dub_audio_repair_handlers
dubsegments_command = _legacy.dubsegments_command
parse_segment_selector = _legacy.parse_segment_selector

__all__ = [
    "_dubfix_process_lock",
    "_write_repair_request",
    "dubfix_command",
    "dubsegments_command",
    "load_repair_segments",
    "parse_segment_selector",
    "register_dub_audio_repair_handlers",
]
