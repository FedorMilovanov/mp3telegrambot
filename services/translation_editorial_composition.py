#!/usr/bin/env python3
"""Provenance-bound composition of reviewed translated media.

AI or a human editor may propose interesting continuous excerpts or chronological
multi-segment compositions. This module only validates an exact plan and renders
it from exact source bytes. It never invents segment boundaries and never
reorders source speech silently.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from services.async_process import run_cancellable_process
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.translation_editorial import sha256_file

COMPOSITION_SCHEMA_NAME = "mp3telegrambot.translation-editorial-composition"
COMPOSITION_SCHEMA_VERSION = 1
HANDOFF_SCHEMA_NAME = "mp3telegrambot.editorial-release-handoff"
HANDOFF_SCHEMA_VERSION = 1
PIECE_KINDS = {"full", "excerpt", "short"}
ASSEMBLY_MODES = {"continuous", "editorial_sequence"}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_name(value: str, fallback: str = "piece") -> str:
    safe = "".join(
        char if (char.isalnum() or char in "_-.") else "_"
        for char in str(value or fallback)
    ).strip("._")
    return safe[:120] or fallback


def build_composition_template(
    *,
    source_video_path: Path,
    source_duration: float,
    title: str = "",
    performer: str = "",
    source_review_pack_id: str = "",
    source_review_sha256: str = "",
    project_key: str = "",
    youtube_account_alias: str = "",
    youtube_channel_id: str = "",
) -> dict[str, Any]:
    """Create an empty exact-source composition document for external editing."""
    source_video_path = Path(source_video_path)
    if not source_video_path.exists() or source_video_path.stat().st_size <= 1024:
        raise FileNotFoundError(f"composition source missing/empty: {source_video_path}")
    document = {
        "schema_name": COMPOSITION_SCHEMA_NAME,
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "source": {
            "local_path": str(source_video_path.resolve(strict=False)),
            "sha256": sha256_file(source_video_path),
            "bytes": source_video_path.stat().st_size,
            "duration_seconds": round(float(source_duration), 3),
            "title": str(title or ""),
            "performer": str(performer or ""),
            "review_pack_id": str(source_review_pack_id or ""),
            "review_sha256": str(source_review_sha256 or ""),
        },
        "release_target": {
            "project_key": str(project_key or ""),
            "youtube_account_alias": str(youtube_account_alias or ""),
            "youtube_channel_id": str(youtube_channel_id or ""),
        },
        "pieces": [],
    }
    document["composition_id"] = composition_id(document)
    return document


def composition_id(document: dict[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "composition_id"}
    return _canonical_sha256(payload)


def piece_duration(piece: dict[str, Any]) -> float:
    total = 0.0
    for segment in piece.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        try:
            total += float(segment.get("end_seconds")) - float(segment.get("start_seconds"))
        except (TypeError, ValueError):
            continue
    return max(0.0, total)


def _validate_segment_order(
    segments: list[dict[str, Any]],
    *,
    source_duration: float,
    piece_id: str,
) -> list[str]:
    errors: list[str] = []
    previous_end: float | None = None
    for index, segment in enumerate(segments, 1):
        prefix = f"piece {piece_id} segment[{index}]"
        try:
            start = float(segment.get("start_seconds"))
            end = float(segment.get("end_seconds"))
        except (TypeError, ValueError):
            errors.append(f"{prefix}: invalid start/end")
            continue
        if start < 0 or end <= start:
            errors.append(f"{prefix}: invalid span")
            continue
        if end > source_duration + 0.25:
            errors.append(f"{prefix}: span exceeds source duration")
        if end - start < 0.20:
            errors.append(f"{prefix}: segment shorter than 0.20s")
        if previous_end is not None and start < previous_end:
            errors.append(f"{prefix}: segments must be chronological and non-overlapping")
        previous_end = end
    return errors


def validate_composition_document(document: dict[str, Any]) -> list[str]:
    """Validate source identity and editorial assembly semantics offline."""
    errors: list[str] = []
    if document.get("schema_name") != COMPOSITION_SCHEMA_NAME:
        errors.append("wrong composition schema_name")
    if document.get("schema_version") != COMPOSITION_SCHEMA_VERSION:
        errors.append("wrong composition schema_version")
    expected_id = composition_id(document)
    if document.get("composition_id") != expected_id:
        errors.append("composition_id does not match document content")

    source = document.get("source")
    if not isinstance(source, dict):
        return errors + ["source must be an object"]
    try:
        source_duration = float(source.get("duration_seconds"))
    except (TypeError, ValueError):
        source_duration = 0.0
        errors.append("source.duration_seconds must be positive")
    if source_duration <= 0:
        errors.append("source.duration_seconds must be positive")
    source_sha = str(source.get("sha256") or "")
    if not source_sha.startswith("sha256:") or len(source_sha) != 71:
        errors.append("source.sha256 must be canonical sha256:<64 hex>")
    if not str(source.get("local_path") or "").strip():
        errors.append("source.local_path is required")

    target = document.get("release_target")
    if target is not None and not isinstance(target, dict):
        errors.append("release_target must be an object")

    pieces = document.get("pieces")
    if not isinstance(pieces, list) or not pieces:
        errors.append("pieces must contain at least one output")
        return errors

    seen_ids: set[str] = set()
    for index, piece in enumerate(pieces, 1):
        prefix = f"piece[{index}]"
        if not isinstance(piece, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        piece_id = str(piece.get("piece_id") or "").strip()
        if not piece_id:
            errors.append(f"{prefix}: piece_id is required")
            piece_id = str(index)
        elif piece_id in seen_ids:
            errors.append(f"{prefix}: duplicate piece_id {piece_id}")
        seen_ids.add(piece_id)

        kind = str(piece.get("kind") or "")
        mode = str(piece.get("assembly_mode") or "")
        if kind not in PIECE_KINDS:
            errors.append(f"piece {piece_id}: invalid kind")
        if mode not in ASSEMBLY_MODES:
            errors.append(f"piece {piece_id}: invalid assembly_mode")

        segments = piece.get("segments")
        if not isinstance(segments, list) or not segments:
            errors.append(f"piece {piece_id}: segments are required")
            continue
        if len(segments) > 12:
            errors.append(f"piece {piece_id}: more than 12 source segments is not allowed")
        normalized = [item for item in segments if isinstance(item, dict)]
        if len(normalized) != len(segments):
            errors.append(f"piece {piece_id}: every segment must be an object")
        errors.extend(
            _validate_segment_order(
                normalized,
                source_duration=source_duration,
                piece_id=piece_id,
            )
        )

        if mode == "continuous" and len(segments) != 1:
            errors.append(f"piece {piece_id}: continuous mode requires exactly one segment")
        if mode == "editorial_sequence":
            if len(segments) < 2:
                errors.append(f"piece {piece_id}: editorial_sequence requires at least two segments")
            if not str(piece.get("editorial_rationale") or "").strip():
                errors.append(f"piece {piece_id}: editorial_sequence requires editorial_rationale")

        total = piece_duration(piece)
        if kind == "short" and not 10.0 <= total <= 180.0:
            errors.append(f"piece {piece_id}: short duration must be 10..180 seconds")
        if kind == "excerpt" and not 120.0 <= total <= 1200.0:
            errors.append(f"piece {piece_id}: excerpt duration must be 2..20 minutes")
        if kind == "full" and total < min(source_duration * 0.50, 60.0):
            errors.append(f"piece {piece_id}: full output is implausibly short")

        metadata = piece.get("publication")
        if metadata is not None and not isinstance(metadata, dict):
            errors.append(f"piece {piece_id}: publication must be an object")
    return errors


def refresh_composition_id(document: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(document, ensure_ascii=False))
    copied["composition_id"] = composition_id(copied)
    return copied


def _piece_filter(piece: dict[str, Any]) -> tuple[str, float]:
    segments = piece.get("segments") or []
    parts: list[str] = []
    inputs: list[str] = []
    total = 0.0
    for index, segment in enumerate(segments):
        start = float(segment["start_seconds"])
        end = float(segment["end_seconds"])
        total += end - start
        parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]"
        )
        parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]"
        )
        inputs.append(f"[v{index}][a{index}]")
    parts.append("".join(inputs) + f"concat=n={len(segments)}:v=1:a=1[outv][outa]")
    return ";".join(parts), total


async def _verify_source(document: dict[str, Any]) -> tuple[Path, float]:
    source = document["source"]
    path = Path(str(source["local_path"]))
    if not path.exists() or path.stat().st_size <= 1024:
        raise FileNotFoundError(f"composition source missing/empty: {path}")
    if sha256_file(path) != source["sha256"]:
        raise RuntimeError("composition source bytes changed after plan approval")
    probe = await probe_media_async(path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("composition source failed video+audio media probe")
    assert probe is not None
    declared = float(source["duration_seconds"])
    if abs(float(probe.duration) - declared) > 1.5:
        raise RuntimeError(
            f"composition source duration drift: plan={declared:.3f}s probe={probe.duration:.3f}s"
        )
    return path, float(probe.duration)


async def render_composition(
    document: dict[str, Any],
    *,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Render every approved piece and write immutable provenance sidecars."""
    errors = validate_composition_document(document)
    if errors:
        raise ValueError("composition validation failed:\n- " + "\n- ".join(errors))
    source_path, source_duration = await _verify_source(document)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for piece in document["pieces"]:
        piece_id = str(piece["piece_id"])
        output_path = output_dir / f"{_safe_name(piece_id)}.mp4"
        sidecar_path = output_dir / f"{_safe_name(piece_id)}.provenance.json"
        if output_path.exists() or sidecar_path.exists():
            raise FileExistsError(f"refusing to overwrite existing composition output: {piece_id}")
        filter_complex, expected_duration = _piece_filter(piece)
        command = [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(source_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
        result = await run_cancellable_process(command, timeout=7200, text=True)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 1024:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg composition failed for {piece_id}")
        probe = await probe_media_async(output_path)
        if not media_probe_is_deliverable(probe):
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"rendered composition failed media probe: {piece_id}")
        assert probe is not None
        if abs(float(probe.duration) - expected_duration) > 1.5:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"composition duration mismatch {piece_id}: expected={expected_duration:.3f}s "
                f"actual={probe.duration:.3f}s"
            )
        provenance = {
            "schema_name": "mp3telegrambot.translation-editorial-composition-result",
            "schema_version": 1,
            "composition_id": document["composition_id"],
            "piece": piece,
            "source": {
                "local_path": str(source_path.resolve(strict=False)),
                "sha256": document["source"]["sha256"],
                "duration_seconds": round(source_duration, 3),
            },
            "output": {
                "local_path": str(output_path.resolve(strict=False)),
                "sha256": sha256_file(output_path),
                "bytes": output_path.stat().st_size,
                "duration_seconds": round(float(probe.duration), 3),
            },
        }
        provenance["result_id"] = _canonical_sha256(provenance)
        sidecar_path.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(
            {
                "piece_id": piece_id,
                "output_path": str(output_path),
                "provenance_path": str(sidecar_path),
                "output_sha256": provenance["output"]["sha256"],
                "duration_seconds": provenance["output"]["duration_seconds"],
            }
        )
    return results


def build_release_handoff(
    document: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create an immutable provider-inert package for a later release manager."""
    by_id = {str(item.get("piece_id")): item for item in results if isinstance(item, dict)}
    outputs: list[dict[str, Any]] = []
    for piece in document.get("pieces") or []:
        piece_id = str(piece.get("piece_id") or "")
        result = by_id.get(piece_id)
        if result is None:
            raise ValueError(f"missing rendered result for {piece_id}")
        outputs.append(
            {
                "piece_id": piece_id,
                "kind": piece.get("kind"),
                "media": {
                    "local_path": result.get("output_path"),
                    "sha256": result.get("output_sha256"),
                    "duration_seconds": result.get("duration_seconds"),
                    "provenance_path": result.get("provenance_path"),
                },
                "publication": piece.get("publication") or {},
                "source_segments": piece.get("segments") or [],
                "assembly_mode": piece.get("assembly_mode"),
                "editorial_rationale": piece.get("editorial_rationale") or "",
            }
        )
    handoff = {
        "schema_name": HANDOFF_SCHEMA_NAME,
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "composition_id": document.get("composition_id"),
        "source_sha256": (document.get("source") or {}).get("sha256"),
        "release_target": document.get("release_target") or {},
        "provider_write_authorized": False,
        "target_system": "video-channel-manager",
        "outputs": outputs,
    }
    handoff["handoff_id"] = _canonical_sha256(handoff)
    return handoff


def candidate_overlap_with_spans(
    candidate: dict[str, Any],
    spans: Iterable[tuple[float, float]],
) -> bool:
    try:
        start = float(candidate.get("start_seconds"))
        end = float(candidate.get("end_seconds"))
    except (TypeError, ValueError):
        return True
    return any(start < span_end and end > span_start for span_start, span_end in spans)


__all__ = [
    "ASSEMBLY_MODES",
    "COMPOSITION_SCHEMA_NAME",
    "COMPOSITION_SCHEMA_VERSION",
    "HANDOFF_SCHEMA_NAME",
    "HANDOFF_SCHEMA_VERSION",
    "PIECE_KINDS",
    "build_composition_template",
    "build_release_handoff",
    "candidate_overlap_with_spans",
    "composition_id",
    "piece_duration",
    "refresh_composition_id",
    "render_composition",
    "validate_composition_document",
]
