#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-only normalization of historical Dub Studio speech windows.

No translation or TTS is performed here. Long windows are split and tiny or
one-word windows are merged with a neighbouring phrase when that remains within
the clean 5.4 second production limit. The ordered Russian token stream must be
identical before and after normalization.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_v45

POLICY = "clean-segments-v1"
MAX_SECONDS = 5.4
MIN_SECONDS = 1.15


def _words(text: str) -> int:
    return max(1, len(re.findall(r"\w+", str(text or ""), flags=re.UNICODE)))


def _tokens(items: list[dict[str, Any]]) -> list[str]:
    return " ".join(" ".join(str(item.get("text") or "").split()) for item in items).split()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _window(item: dict[str, Any], delay: float, duration: float) -> tuple[float, float]:
    start = max(
        0.0,
        float(item.get("original_srt_start", item.get("start", 0.0)) or 0.0),
    )
    source_end = float(item.get("source_end", 0.0) or 0.0)
    if source_end <= start:
        source_end = float(item.get("end", start) or start) + max(
            delay,
            int(item.get("start_delay_ms", 0) or 0) / 1000.0,
        )
    source_end = min(float(duration), max(start + 0.35, source_end))
    return start, source_end


def _split_long(
    items: list[dict[str, Any]],
    *,
    delay: float,
    duration: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for original_index, item in enumerate(items, start=1):
        start, source_end = _window(item, delay, duration)
        window = source_end - start
        text = str(item.get("text") or "").strip()
        parts = professional_audio_v45.split_text(
            text,
            window,
            max_seconds=MAX_SECONDS,
        )
        weights = [_words(part) for part in parts]
        total = sum(weights)
        cursor = start
        for part_index, (part, weight) in enumerate(zip(parts, weights, strict=True)):
            part_end = (
                source_end
                if part_index == len(parts) - 1
                else cursor + window * weight / max(1, total)
            )
            result.append(
                {
                    **item,
                    "start": cursor,
                    "source_end": part_end,
                    "text": part,
                    "normalized_from_segment": int(item.get("id") or original_index),
                }
            )
            cursor = part_end
    return result


def _merge_pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_source = str(left.get("source") or "").strip()
    right_source = str(right.get("source") or "").strip()
    return {
        **left,
        "start": float(left["start"]),
        "source_end": float(right["source_end"]),
        "text": f"{left.get('text', '')} {right.get('text', '')}".strip(),
        "source": f"{left_source} {right_source}".strip(),
        "normalized_from_segment": [
            left.get("normalized_from_segment", left.get("id")),
            right.get("normalized_from_segment", right.get("id")),
        ],
    }


def _merge_tiny(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(item) for item in items]
    changed = True
    while changed and len(result) >= 2:
        changed = False
        for index, item in enumerate(result):
            span = float(item["source_end"]) - float(item["start"])
            if span >= MIN_SECONDS and _words(str(item.get("text") or "")) >= 2:
                continue
            candidates: list[tuple[float, int]] = []
            if index > 0:
                previous = result[index - 1]
                combined = float(item["source_end"]) - float(previous["start"])
                if combined <= MAX_SECONDS + 0.05:
                    candidates.append((combined, index - 1))
            if index + 1 < len(result):
                following = result[index + 1]
                combined = float(following["source_end"]) - float(item["start"])
                if combined <= MAX_SECONDS + 0.05:
                    candidates.append((combined, index + 1))
            if not candidates:
                continue
            _, neighbour = min(candidates, key=lambda value: value[0])
            left_index = min(index, neighbour)
            right_index = max(index, neighbour)
            result[left_index] = _merge_pair(result[left_index], result[right_index])
            result.pop(right_index)
            changed = True
            break
    return result


def normalize(
    root: Path,
    request: dict[str, Any],
    *,
    duration: float,
) -> bool:
    segments_path = root / "segments_ru_final.json"
    repair_path = root / "input" / "audio_repair.json"
    if not segments_path.is_file() or not repair_path.is_file():
        return False
    payload = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    repair = json.loads(repair_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload or not isinstance(repair, dict):
        return False
    if not repair.get("repair_all"):
        return False

    original = [dict(item) for item in payload if isinstance(item, dict)]
    if len(original) != len(payload):
        raise RuntimeError("Список реплик содержит повреждённые элементы.")
    original_tokens = _tokens(original)
    delay_ms = max(0, int(request.get("russian_delay_ms") or 420))
    delay = delay_ms / 1000.0

    normalized = _merge_tiny(
        _split_long(original, delay=delay, duration=float(duration))
    )
    normalized_tokens = _tokens(normalized)
    if original_tokens != normalized_tokens:
        raise RuntimeError("Чистая нормализация изменила русский текст; операция остановлена.")

    changed = len(normalized) != len(original) or any(
        abs(float(new["start"]) - float(old.get("start", 0.0))) > 0.001
        or abs(float(new["source_end"]) - float(old.get("source_end", old.get("end", 0.0)))) > 0.001
        or str(new.get("text") or "") != str(old.get("text") or "")
        for new, old in zip(normalized, original)
    )

    for index, item in enumerate(normalized, start=1):
        start = float(item["start"])
        source_end = min(float(duration), float(item["source_end"]))
        render_end = min(source_end, max(start + 0.35, float(duration) - delay))
        profile = "composite" if index == len(normalized) or index % 5 == 0 else "extended"
        item.update(
            {
                "id": index,
                "start": round(start, 3),
                "end": round(render_end, 3),
                "source_end": round(source_end, 3),
                "start_delay_ms": delay_ms,
                "reference_profile": profile,
                "tail_guard": 0.22 if profile == "composite" else 0.18,
                "quality_timing": "single-global-delay",
                "segment_policy": POLICY,
            }
        )

    backup = root / "segments_ru_final.pre_clean.json"
    if changed and not backup.exists():
        shutil.copy2(segments_path, backup)
    segments_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    repair.update(
        segment_ids=[int(item["id"]) for item in normalized],
        segments_sha256=_sha256(segments_path),
        clean_segment_policy=POLICY,
    )
    repair_path.write_text(
        json.dumps(repair, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    subtitle_cues = [
        pipeline.Cue(
            min(float(duration), float(item["start"]) + delay),
            min(float(duration), float(item["source_end"]) + delay),
            str(item.get("text") or "").strip(),
        )
        for item in normalized
    ]
    pipeline.write_srt(subtitle_cues, root / "output" / "russian_subtitles.srt")

    manifest_path = root / "output" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if isinstance(manifest, dict):
            manifest["segments"] = len(normalized)
            manifest["clean_segment_policy"] = POLICY
            if changed:
                manifest["legacy_segments_backup"] = str(backup)
            production.save_json(manifest_path, manifest)

    production.log(
        f"clean segment normalization: {len(original)} -> {len(normalized)}; "
        f"changed={changed}; Russian tokens preserved"
    )
    return changed


__all__ = ["POLICY", "normalize"]
