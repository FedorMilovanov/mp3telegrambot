#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely migrate legacy long audio islands into short phrase windows."""
from __future__ import annotations

import hashlib, json, math, re, shutil
from pathlib import Path
from typing import Any

from tools.voxcpm2 import professional_audio_v45 as policy


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _window(item: dict[str, Any], delay: float) -> tuple[float, float]:
    start = float(item.get("original_srt_start", item.get("start", 0.0)) or 0.0)
    end = float(item.get("source_end", 0.0) or 0.0)
    if end <= start:
        end = float(item.get("end", start) or start) + max(delay, int(item.get("start_delay_ms", 0) or 0) / 1000.0)
    return max(0.0, start), max(start + 0.35, end)


def migrate(root: Path, request: dict[str, Any]) -> bool:
    repair_path = root / "input" / "audio_repair.json"
    segments_path = root / "segments_ru_final.json"
    if not repair_path.is_file() or not segments_path.is_file():
        return False
    repair = json.loads(repair_path.read_text(encoding="utf-8-sig"))
    old = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if not isinstance(repair, dict) or not repair.get("repair_all") or not isinstance(old, list) or not old:
        return False

    delay_ms = max(0, int(request.get("russian_delay_ms") or 420))
    delay = delay_ms / 1000.0
    if not any(_window(item, delay)[1] - _window(item, delay)[0] > 6.2 or item.get("quality_timing") != "global-delay-v4.5" for item in old):
        return False

    migrated: list[dict[str, Any]] = []
    for old_index, item in enumerate(old, start=1):
        start, end = _window(item, delay)
        duration = end - start
        parts = policy.split_text(str(item.get("text") or ""), duration)
        if not parts:
            raise RuntimeError(f"Реплика #{old_index} пуста и не может быть мигрирована.")
        weights = [policy.words(part) for part in parts]
        total = sum(weights)
        cursor = start
        for part_index, (part, weight) in enumerate(zip(parts, weights, strict=True), start=1):
            part_end = end if part_index == len(parts) else cursor + duration * weight / total
            migrated.append({
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
                "migrated_from_segment": int(item.get("id") or old_index),
                "migration_part": part_index,
            })
            cursor = part_end

    for index, item in enumerate(migrated, start=1):
        item["id"] = index
        if index == len(migrated) or index % 5 == 0:
            item["reference_profile"] = "composite"
            item["tail_guard"] = 0.22

    old_tokens = " ".join(" ".join(str(item.get("text") or "").split()) for item in old).split()
    new_tokens = " ".join(" ".join(str(item.get("text") or "").split()) for item in migrated).split()
    if old_tokens != new_tokens:
        raise RuntimeError("Миграция изменила русский текст; операция остановлена.")

    backup = root / "segments_ru_final.pre_v45.json"
    if not backup.exists():
        shutil.copy2(segments_path, backup)
    segments_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
    repair.update(
        segment_ids=[int(item["id"]) for item in migrated],
        segments_sha256=_sha(segments_path),
        migration=policy.POLICY,
    )
    repair_path.write_text(json.dumps(repair, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = root / "output" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if isinstance(manifest, dict):
            manifest.update(
                segments=len(migrated),
                audio_segmentation=policy.POLICY,
                legacy_segments_backup=str(backup),
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    policy.log(f"legacy timing migrated safely: {len(old)} -> {len(migrated)} segments; text preserved")
    return True
