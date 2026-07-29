#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely migrate legacy long audio islands into short phrase windows."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as strict_core
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_v45 as policy


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_old(payload: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for position, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"legacy segment[{position}] должен быть JSON-объектом, "
                f"получено {type(item).__name__}."
            )
        copied = dict(item)
        segment_id = strict_core._strict_int(
            copied.get("id"),
            field=f"legacy segment[{position}].id",
            low=1,
            high=2**31 - 1,
        )
        if segment_id in seen:
            raise RuntimeError(f"Повторный legacy segment ID={segment_id}.")
        seen.add(segment_id)
        copied["id"] = segment_id
        if not str(copied.get("text") or "").strip():
            raise RuntimeError(f"Legacy segment #{segment_id} не содержит текста.")
        result.append(copied)
    return result


def _window(item: dict[str, Any], delay: float) -> tuple[float, float]:
    segment_id = int(item["id"])
    start_raw = (
        item.get("original_srt_start")
        if item.get("original_srt_start") is not None
        else item.get("start", 0.0)
    )
    start = strict_core._finite(
        start_raw,
        field=f"legacy segment[{segment_id}].start",
    )
    end = strict_core._finite(
        item.get("source_end", 0.0),
        field=f"legacy segment[{segment_id}].source_end",
    )
    if end <= start:
        render_end = strict_core._finite(
            item.get("end", start),
            field=f"legacy segment[{segment_id}].end",
        )
        old_delay_ms = strict_core._strict_int(
            item.get("start_delay_ms", 0),
            field=f"legacy segment[{segment_id}].start_delay_ms",
            low=0,
            high=1500,
        )
        end = render_end + max(delay, old_delay_ms / 1000.0)
    return max(0.0, start), max(start + 0.35, end)


def migrate(root: Path, request: dict[str, Any]) -> bool:
    repair_path = root / "input" / "audio_repair.json"
    segments_path = root / "segments_ru_final.json"
    if not repair_path.is_file() or not segments_path.is_file():
        return False
    repair = json.loads(repair_path.read_text(encoding="utf-8-sig"))
    payload = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(repair, dict)
        or not repair.get("repair_all")
        or not isinstance(payload, list)
        or not payload
    ):
        return False

    old = _validate_old(payload)
    delay_ms = clean_request_settings.russian_delay_ms(request)
    delay = delay_ms / 1000.0
    old_windows = [_window(item, delay) for item in old]
    if not any(
        end - start > 6.2
        or item.get("quality_timing") != "global-delay-v4.5"
        for item, (start, end) in zip(old, old_windows, strict=True)
    ):
        return False

    source = root / "source" / "source.mp4"
    source_duration = pipeline.ffprobe_duration(source) if source.is_file() else 0.0
    if source_duration:
        source_duration = strict_core._finite(
            source_duration,
            field="source_duration",
        )
        if source_duration <= 0.0:
            raise RuntimeError("source_duration должен быть > 0.")
    latest_render_end = max(0.35, source_duration - delay) if source_duration > 0 else 0.0

    migrated: list[dict[str, Any]] = []
    for item, (start, end) in zip(old, old_windows, strict=True):
        if latest_render_end > 0 and end > latest_render_end:
            end = latest_render_end
            if end - start < 0.35:
                start = max(0.0, end - 0.35)
        duration = end - start
        parts = policy.split_text(str(item.get("text") or ""), duration)
        if not parts:
            raise RuntimeError(
                f"Реплика #{item['id']} пуста и не может быть мигрирована."
            )
        weights = [policy.words(part) for part in parts]
        total = sum(weights)
        if total <= 0:
            raise RuntimeError(f"Реплика #{item['id']} не содержит произносимых слов.")
        cursor = start
        for part_index, (part, weight) in enumerate(
            zip(parts, weights, strict=True),
            start=1,
        ):
            part_end = (
                end
                if part_index == len(parts)
                else cursor + duration * weight / total
            )
            migrated.append(
                {
                    "id": len(migrated) + 1,
                    "start": round(cursor, 3),
                    "end": round(part_end, 3),
                    "start_delay_ms": delay_ms,
                    "reference_profile": "extended",
                    "tail_guard": 0.18,
                    "text": part,
                    "source_end": round(part_end, 3),
                    "source": str(item.get("source") or ""),
                    "quality_timing": "global-delay-v4.5",
                    "migrated_from_segment": int(item["id"]),
                    "migration_part": part_index,
                }
            )
            cursor = part_end

    for index, item in enumerate(migrated, start=1):
        item["id"] = index
        if index == len(migrated) or index % 5 == 0:
            item["reference_profile"] = "composite"
            item["tail_guard"] = 0.22

    old_tokens = " ".join(
        " ".join(str(item.get("text") or "").split()) for item in old
    ).split()
    new_tokens = " ".join(
        " ".join(str(item.get("text") or "").split()) for item in migrated
    ).split()
    if old_tokens != new_tokens:
        raise RuntimeError("Миграция изменила русский текст; операция остановлена.")

    validation_duration = source_duration or max(
        float(item["end"]) + delay for item in migrated
    )
    strict_core._mark_and_validate_segments(migrated, validation_duration)
    backup = root / "segments_ru_final.pre_v45.json"
    if not backup.exists():
        shutil.copy2(segments_path, backup)
    segments_path.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    repair.update(
        segment_ids=[int(item["id"]) for item in migrated],
        segments_sha256=_sha(segments_path),
        migration=policy.POLICY,
    )
    repair_path.write_text(
        json.dumps(repair, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    if source_duration > 0:
        subtitles = [
            pipeline.Cue(
                min(source_duration, float(item["start"]) + delay),
                min(source_duration, float(item["end"]) + delay),
                str(item["text"]),
            )
            for item in migrated
        ]
        pipeline.write_srt(subtitles, root / "output" / "russian_subtitles.srt")

    manifest_path = root / "output" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if isinstance(manifest, dict):
            manifest.update(
                segments=len(migrated),
                audio_segmentation=policy.POLICY,
                legacy_segments_backup=str(backup),
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
    policy.log(
        f"legacy timing migrated safely: {len(old)} -> {len(migrated)} "
        "segments; text preserved; final phrase protected"
    )
    return True
