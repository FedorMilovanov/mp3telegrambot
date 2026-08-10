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


def load_verified_review_pack(pack_path: Path) -> dict[str, Any]:
    """Verify identity-bearing evidence plus human/model-facing ZIP instructions."""
    pack_path = Path(pack_path)
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
