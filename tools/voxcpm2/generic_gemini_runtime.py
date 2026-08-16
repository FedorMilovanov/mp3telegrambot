#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checked entrypoint for Gemini MAX Dub Studio production."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import expressive_translation
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import generic_short_production as pipeline

_TAG_RE = re.compile(r"<[^>]+>")
_NON_SPEECH_RE = re.compile(
    r"^\[(?:music|applause|laughter|laughs?|cheering|silence|inaudible|"
    r"crosstalk|noise|sighs?|gasps?|instrumental)\]$",
    flags=re.I,
)


def _require_file(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Gemini MAX не создал обязательный результат: {label} ({path}).")





def clean_manual_caption_line(value: str) -> str:
    """Remove VTT markup but preserve meaningful bracketed source text."""
    text = html.unescape(_TAG_RE.sub("", str(value or ""))).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if _NON_SPEECH_RE.fullmatch(text):
        return ""
    return text


def _merge_creator_caption_lines(values: list[str]) -> str:
    """Collapse rolling render states without deleting separated repetitions."""
    states: list[str] = []
    for value in values:
        cleaned = clean_manual_caption_line(value)
        if not cleaned:
            continue
        if not states:
            states.append(cleaned)
            continue
        previous = states[-1]
        previous_folded = previous.casefold()
        current_folded = cleaned.casefold()
        if current_folded == previous_folded:
            # Exact adjacent duplicates are the same VTT render state.
            continue
        if current_folded.startswith(previous_folded + " "):
            # YouTube rolling captions often replace a partial line with its
            # longer complete state. Keep only the complete state.
            states[-1] = cleaned
            continue
        if previous_folded.startswith(current_folded + " "):
            # Ignore a rollback to an older partial render state.
            continue
        # Do not deduplicate against the whole cue: A / B / A can be a real
        # rhetorical repetition and must reach the translator unchanged.
        states.append(cleaned)
    return " ".join(states).strip()


def parse_creator_vtt_preserving_text(path: Path) -> list[pipeline.Cue]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    cues: list[pipeline.Cue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = line.split("-->", 1)
        try:
            start = pipeline.parse_timestamp(left)
            end = pipeline.parse_timestamp(right.strip().split()[0])
        except ValueError:
            index += 1
            continue
        index += 1
        payload: list[str] = []
        while index < len(lines) and lines[index].strip():
            payload.append(lines[index])
            index += 1
        text = _merge_creator_caption_lines(payload)
        if text and end > start:
            cues.append(pipeline.Cue(start, end, text))
        index += 1
    return cues


def validate_completed_outputs(root: Path) -> dict[str, Any]:
    output = root / "output"
    mixed = output / "final_upload.mp4"
    russian_only = output / "russian_only.mp4"
    manifest_path = output / "manifest.json"
    _require_file(mixed, "главный MP4")
    _require_file(russian_only, "версия только с русским голосом")
    _require_file(manifest_path, "manifest")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("phase") != "completed":
        raise RuntimeError("Gemini MAX manifest не имеет состояния completed.")
    if manifest.get("translation_mode") != "gemini":
        raise RuntimeError("Gemini MAX manifest содержит неверный translation_mode.")

    telegram_outputs = manifest.get("telegram_outputs")
    if not isinstance(telegram_outputs, list) or not telegram_outputs:
        raise RuntimeError("Gemini MAX manifest не содержит Telegram outputs.")
    primary = [item for item in telegram_outputs if isinstance(item, dict) and item.get("primary")]
    if not primary:
        raise RuntimeError("Gemini MAX manifest не содержит основного видео.")
    primary_path = Path(str(primary[0].get("path") or "")).expanduser()
    _require_file(primary_path, "именованный основной MP4")
    return manifest



def _clean_source_groups(cues: list[pipeline.Cue]) -> list[dict[str, Any]]:
    groups = clean.group_source_cues(cues)
    for group in groups:
        group["source"] = group.pop("english")
    return groups


def _acquire_transcript_clean(*args: Any, **kwargs: Any) -> tuple[list[pipeline.Cue], str, str]:
    kwargs["manual_vtt_parser"] = parse_creator_vtt_preserving_text
    cues, caption_origin, source_language = production.acquire_transcript(*args, **kwargs)
    metadata = kwargs.get("metadata")
    if metadata is None and len(args) >= 4:
        metadata = args[3]
    if isinstance(metadata, dict):
        language = str(source_language or "unknown")
        metadata["language"] = language
        metadata["source_language"] = language
    return cues, caption_origin, source_language


def _run_clean_speech_and_master(
    *, root: Path, request: dict[str, Any], source: Path, cues: list[Any],
    duration: float, segments_json: Path, final_mixed: Path, final_russian: Path,
) -> Path:
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source, cues=cues, duration=duration, reference_dir=root / "references"
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _built, detail = controlled_reference_gate.build_or_keep_calm(
        source=source, segments=planned, output=composite, identity_reference=extended
    )
    production.log("source-guided emotional arc prepared; Russian text preserved; " + detail)
    return clean.render_and_master(
        root=root, request=request, source=source, duration=duration,
        segments_json=segments_json, extended_reference=extended,
        composite_reference=composite, final_mixed=final_mixed,
        final_russian=final_russian, force_fresh=True,
    )


def _finalize_clean_gemini(root: Path, request: dict[str, Any]) -> None:
    clean_request_settings.repair_manifest(root, request)
    validate_completed_outputs(root)


def main() -> None:
    route = production.ProjectRoute(
        download_source=clean_source_download.download_source,
        acquire_transcript=_acquire_transcript_clean,
        group_source=_clean_source_groups,
        translate_groups=expressive_translation.translate_groups,
        validate_translation=production._validate_translation_payload,
        build_render_segments=clean.build_render_segments,
        run_speech_and_master=_run_clean_speech_and_master,
        delay_ms=clean_request_settings.russian_delay_ms,
        finalize=_finalize_clean_gemini,
    )
    production.main(route)
    production.log("=== GEMINI MAX SOURCE-OWNED OUTPUT CONTRACT: OK ===")


if __name__ == "__main__":
    main()
