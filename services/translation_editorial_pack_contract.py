#!/usr/bin/env python3
"""Canonical non-media contract for Translation Editorial Review ZIPs."""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from services.translation_editorial import (
    ACTION_TYPES,
    EXECUTABLE_ACTIONS,
    ISSUE_SEVERITIES,
    VERDICTS,
    load_pack_manifest,
)

_MAX_ZIP_FILE_BYTES = 128 * 1024 * 1024
_MAX_ZIP_MEMBERS = 8
_MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MEMBER_LIMITS = {
    "manifest.json": 4 * 1024 * 1024,
    "candidates.json": 16 * 1024 * 1024,
    "original.srt": 64 * 1024 * 1024,
    "russian_whisper.srt": 64 * 1024 * 1024,
    "russian_whisper_words.json": 128 * 1024 * 1024,
    "REVIEW_INSTRUCTIONS.md": 256 * 1024,
}


def _review_contract() -> dict[str, Any]:
    return {
        "verdicts": sorted(VERDICTS),
        "issue_severities": sorted(ISSUE_SEVERITIES),
        "action_types": sorted(ACTION_TYPES),
        "automatically_executable_actions": sorted(EXECUTABLE_ACTIONS),
        "borrow_span_policy": (
            "review-only in v1; donor media is never synthesized or inserted automatically"
        ),
    }


def _legacy_instructions() -> str:
    return (
        "# Translation Editorial Review v1\n\n"
        "Upload this ZIP to the editor. Compare `original.srt` with the words actually "
        "heard in `russian_whisper.srt`. Minor stylistic roughness is not a defect. "
        "Return a `review.json` bound to the exact `review_pack_id` in manifest.json.\n\n"
        "Safe automatic repairs in v1: `drop_span` and `mute_span`. `borrow_span` may "
        "identify a same-voice donor candidate, but requires explicit human/editor approval "
        "and is intentionally not auto-executed.\n"
    )


def _timeline_instructions() -> str:
    return (
        "# Translation Editorial Review v1\n\n"
        "Compare `original.srt` with the words actually heard in `russian_whisper.srt`. "
        "The two transcripts may use different timelines; read `manifest.json.timeline` "
        "before comparing nearby cues. Minor stylistic roughness is not a defect. "
        "Return a `review.json` bound to the exact `review_pack_id`.\n\n"
        "Safe automatic repairs in v1: `drop_span` and `mute_span`. `borrow_span` may "
        "identify a same-voice donor candidate, but requires explicit approval and is "
        "intentionally not auto-executed.\n"
    )


def _preflight_zip(pack_path: Path) -> None:
    """Bound memory/disk-abuse properties before any ZIP member is read."""
    pack_path = Path(pack_path)
    if not pack_path.is_file():
        raise FileNotFoundError(pack_path)
    physical_size = pack_path.stat().st_size
    if physical_size <= 0 or physical_size > _MAX_ZIP_FILE_BYTES:
        raise ValueError("translation editorial ZIP physical size is outside the safe limit")

    with zipfile.ZipFile(pack_path, "r") as archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_ZIP_MEMBERS:
            raise ValueError("translation editorial ZIP member count is outside the safe limit")
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError("translation editorial ZIP contains duplicate members")

        total = 0
        for info in infos:
            name = str(info.filename)
            if not name or Path(name).name != name or info.is_dir():
                raise ValueError(f"unsafe translation editorial ZIP member name: {name}")
            if info.flag_bits & 0x1:
                raise ValueError(f"encrypted translation editorial ZIP member is not allowed: {name}")
            if info.file_size < 0 or info.compress_size < 0:
                raise ValueError(f"invalid translation editorial ZIP member sizes: {name}")
            limit = _MEMBER_LIMITS.get(name, 16 * 1024 * 1024)
            if info.file_size > limit:
                raise ValueError(f"translation editorial ZIP member exceeds safe limit: {name}")
            total += info.file_size
            if total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ValueError("translation editorial ZIP expands beyond the safe total limit")


def load_verified_review_pack(pack_path: Path) -> dict[str, Any]:
    """Verify bounded evidence plus human/model-facing ZIP instructions."""
    pack_path = Path(pack_path)
    _preflight_zip(pack_path)
    manifest = load_pack_manifest(pack_path)
    transcripts = manifest.get("transcripts") or {}
    expected_files = {
        "manifest.json",
        "candidates.json",
        "original.srt",
        "russian_whisper.srt",
        "REVIEW_INSTRUCTIONS.md",
    }
    words = transcripts.get("russian_whisper_words") if isinstance(transcripts, dict) else None
    if isinstance(words, dict) and words.get("file"):
        expected_files.add(str(words["file"]))

    with zipfile.ZipFile(pack_path, "r") as archive:
        names = [item.filename for item in archive.infolist()]
        if set(names) != expected_files:
            missing = sorted(expected_files - set(names))
            extra = sorted(set(names) - expected_files)
            details: list[str] = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("extra=" + ",".join(extra))
            raise ValueError("non-canonical translation editorial ZIP members: " + " ".join(details))
        instructions = archive.read("REVIEW_INSTRUCTIONS.md").decode("utf-8")

    if manifest.get("review_contract") != _review_contract():
        raise ValueError("translation editorial review_contract was modified")
    expected_instructions = (
        _timeline_instructions() if "timeline" in manifest else _legacy_instructions()
    )
    if instructions != expected_instructions:
        raise ValueError("translation editorial REVIEW_INSTRUCTIONS.md was modified")
    return manifest


__all__ = ["load_verified_review_pack"]
