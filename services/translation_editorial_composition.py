#!/usr/bin/env python3
"""Provenance-bound composition of reviewed translated media.

AI or a human editor may propose continuous excerpts or chronological
multi-segment compositions. This module validates an exact plan and renders it
from exact source bytes. It never invents segment boundaries, silently reorders
speech, or treats an unverified previous output as resumable work.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from services.async_process import run_cancellable_process
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.translation_editorial import sha256_file

COMPOSITION_SCHEMA_NAME = "mp3telegrambot.translation-editorial-composition"
COMPOSITION_SCHEMA_VERSION = 1
RESULT_SCHEMA_NAME = "mp3telegrambot.translation-editorial-composition-result"
RESULT_SCHEMA_VERSION = 1
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
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _is_canonical_sha256(value: Any) -> bool:
    token = str(value or "")
    if not token.startswith("sha256:") or len(token) != 71:
        return False
    digest = token[7:]
    return all(char in "0123456789abcdef" for char in digest)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
    duration = _finite_float(source_duration)
    if duration is None or duration <= 0:
        raise ValueError("composition source duration must be finite and positive")
    if source_review_pack_id and not _is_canonical_sha256(source_review_pack_id):
        raise ValueError("source_review_pack_id must be canonical sha256:<64 hex>")
    if source_review_sha256 and not _is_canonical_sha256(source_review_sha256):
        raise ValueError("source_review_sha256 must be canonical sha256:<64 hex>")

    document = {
        "schema_name": COMPOSITION_SCHEMA_NAME,
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "source": {
            "local_path": str(source_video_path.resolve(strict=False)),
            "sha256": sha256_file(source_video_path),
            "bytes": source_video_path.stat().st_size,
            "duration_seconds": round(duration, 3),
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


def _segment_values(segment: dict[str, Any]) -> tuple[float, float] | None:
    start = _finite_float(segment.get("start_seconds"))
    end = _finite_float(segment.get("end_seconds"))
    if start is None or end is None:
        return None
    return start, end


def piece_duration(piece: dict[str, Any]) -> float:
    total = 0.0
    for segment in piece.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        values = _segment_values(segment)
        if values is None:
            continue
        start, end = values
        if end > start:
            total += end - start
    return total


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
        values = _segment_values(segment)
        if values is None:
            errors.append(f"{prefix}: start/end must be finite numbers")
            continue
        start, end = values
        if start < 0 or end <= start:
            errors.append(f"{prefix}: invalid span")
            continue
        if end > source_duration + 0.05:
            errors.append(f"{prefix}: span exceeds source duration")
        if end - start < 0.20:
            errors.append(f"{prefix}: segment shorter than 0.20s")
        if previous_end is not None and start < previous_end:
            errors.append(f"{prefix}: segments must be chronological and non-overlapping")
        previous_end = end
    return errors


def _validate_publication(piece_id: str, metadata: Any) -> list[str]:
    if metadata is None:
        return []
    if not isinstance(metadata, dict):
        return [f"piece {piece_id}: publication must be an object"]
    errors: list[str] = []
    limits = {
        "title": 300,
        "description": 12000,
        "playlist": 300,
        "schedule_at": 100,
    }
    for key, limit in limits.items():
        value = metadata.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"piece {piece_id}: publication.{key} must be a string")
        elif len(value) > limit:
            errors.append(f"piece {piece_id}: publication.{key} is too long")
    hashtags = metadata.get("hashtags")
    if hashtags is not None:
        if not isinstance(hashtags, list):
            errors.append(f"piece {piece_id}: publication.hashtags must be a list")
        else:
            if len(hashtags) > 30:
                errors.append(f"piece {piece_id}: publication.hashtags has too many items")
            for item in hashtags:
                if not isinstance(item, str) or not item.strip() or len(item) > 100:
                    errors.append(f"piece {piece_id}: publication.hashtags contains invalid item")
                    break
    return errors


def validate_composition_document(document: dict[str, Any]) -> list[str]:
    """Validate source identity and editorial assembly semantics offline."""
    errors: list[str] = []
    if document.get("schema_name") != COMPOSITION_SCHEMA_NAME:
        errors.append("wrong composition schema_name")
    if document.get("schema_version") != COMPOSITION_SCHEMA_VERSION:
        errors.append("wrong composition schema_version")
    try:
        expected_id = composition_id(document)
    except (TypeError, ValueError):
        expected_id = ""
        errors.append("composition contains non-finite or non-serializable values")
    if document.get("composition_id") != expected_id:
        errors.append("composition_id does not match document content")

    source = document.get("source")
    if not isinstance(source, dict):
        return errors + ["source must be an object"]
    source_duration = _finite_float(source.get("duration_seconds"))
    if source_duration is None or source_duration <= 0:
        source_duration = 0.0
        errors.append("source.duration_seconds must be finite and positive")
    source_sha = str(source.get("sha256") or "")
    if not _is_canonical_sha256(source_sha):
        errors.append("source.sha256 must be canonical sha256:<64 lowercase hex>")
    if not str(source.get("local_path") or "").strip():
        errors.append("source.local_path is required")
    try:
        source_bytes = int(source.get("bytes"))
    except (TypeError, ValueError):
        source_bytes = 0
    if source_bytes <= 1024:
        errors.append("source.bytes must describe a usable media file")
    for key in ("review_pack_id", "review_sha256"):
        value = str(source.get(key) or "")
        if value and not _is_canonical_sha256(value):
            errors.append(f"source.{key} must be canonical sha256:<64 lowercase hex>")

    target = document.get("release_target")
    if target is not None and not isinstance(target, dict):
        errors.append("release_target must be an object")
    elif isinstance(target, dict):
        target_values = {
            key: str(target.get(key) or "").strip()
            for key in ("project_key", "youtube_account_alias", "youtube_channel_id")
        }
        if any(target_values.values()):
            if not target_values["project_key"]:
                errors.append("release_target.project_key is required when target identity is supplied")
            if not target_values["youtube_channel_id"]:
                errors.append("release_target.youtube_channel_id is required when target identity is supplied")

    pieces = document.get("pieces")
    if not isinstance(pieces, list) or not pieces:
        errors.append("pieces must contain at least one output")
        return errors
    if len(pieces) > 50:
        errors.append("pieces may contain at most 50 outputs")

    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, piece in enumerate(pieces, 1):
        prefix = f"piece[{index}]"
        if not isinstance(piece, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        piece_id = str(piece.get("piece_id") or "").strip()
        if not piece_id:
            errors.append(f"{prefix}: piece_id is required")
            piece_id = str(index)
        elif len(piece_id) > 120:
            errors.append(f"{prefix}: piece_id is too long")
        if piece_id in seen_ids:
            errors.append(f"{prefix}: duplicate piece_id {piece_id}")
        seen_ids.add(piece_id)
        safe_name = _safe_name(piece_id)
        if safe_name != piece_id:
            errors.append(f"piece {piece_id}: piece_id must already be filesystem-safe")
        if safe_name in seen_names:
            errors.append(f"piece {piece_id}: output filename collides after normalization")
        seen_names.add(safe_name)

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
            rationale = str(piece.get("editorial_rationale") or "").strip()
            if len(rationale) < 12:
                errors.append(f"piece {piece_id}: editorial_sequence requires a meaningful rationale")

        total = piece_duration(piece)
        if kind == "short" and not 10.0 <= total <= 180.0:
            errors.append(f"piece {piece_id}: short duration must be 10..180 seconds")
        if kind == "excerpt" and not 120.0 <= total <= 1200.0:
            errors.append(f"piece {piece_id}: excerpt duration must be 2..20 minutes")
        if kind == "full" and source_duration > 0:
            coverage = total / source_duration
            if coverage < 0.85:
                errors.append(f"piece {piece_id}: full output must retain at least 85% of source duration")
            valid_values = [
                values
                for segment in normalized
                if (values := _segment_values(segment)) is not None
            ]
            if valid_values:
                edge_margin = max(15.0, min(60.0, source_duration * 0.02))
                if valid_values[0][0] > edge_margin:
                    errors.append(f"piece {piece_id}: full output starts too far into the source")
                if valid_values[-1][1] < source_duration - edge_margin:
                    errors.append(f"piece {piece_id}: full output ends too far before source end")

        errors.extend(_validate_publication(piece_id, piece.get("publication")))
    return errors


def refresh_composition_id(document: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False))
    copied["composition_id"] = composition_id(copied)
    return copied


def _piece_filter(piece: dict[str, Any]) -> tuple[str, float]:
    segments = piece.get("segments") or []
    parts: list[str] = []
    inputs: list[str] = []
    total = 0.0
    for index, segment in enumerate(segments):
        values = _segment_values(segment)
        if values is None:
            raise ValueError("piece contains non-finite segment values")
        start, end = values
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
    if path.stat().st_size != int(source["bytes"]):
        raise RuntimeError("composition source size changed after plan approval")
    if sha256_file(path) != source["sha256"]:
        raise RuntimeError("composition source bytes changed after plan approval")
    probe = await probe_media_async(path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("composition source failed video+audio media probe")
    assert probe is not None
    declared = float(source["duration_seconds"])
    tolerance = max(0.35, min(0.75, declared * 0.001))
    if abs(float(probe.duration) - declared) > tolerance:
        raise RuntimeError(
            f"composition source duration drift: plan={declared:.3f}s probe={probe.duration:.3f}s"
        )
    return path, float(probe.duration)


def _result_summary(provenance: dict[str, Any], sidecar_path: Path) -> dict[str, Any]:
    piece = provenance.get("piece") or {}
    output = provenance.get("output") or {}
    return {
        "piece_id": str(piece.get("piece_id") or ""),
        "output_path": str(output.get("local_path") or ""),
        "provenance_path": str(sidecar_path),
        "output_sha256": output.get("sha256"),
        "output_bytes": output.get("bytes"),
        "duration_seconds": output.get("duration_seconds"),
        "result_id": provenance.get("result_id"),
        "provenance_sha256": sha256_file(sidecar_path),
    }


def _load_verified_existing_result(
    document: dict[str, Any],
    piece: dict[str, Any],
    output_path: Path,
    sidecar_path: Path,
) -> dict[str, Any]:
    if not output_path.exists() or not sidecar_path.exists():
        raise FileExistsError(
            f"partial composition output exists for {piece.get('piece_id')}; refusing ambiguous resume"
        )
    try:
        provenance = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid composition provenance sidecar: {sidecar_path}") from exc
    if not isinstance(provenance, dict):
        raise RuntimeError("composition provenance sidecar must be a JSON object")
    if provenance.get("schema_name") != RESULT_SCHEMA_NAME or provenance.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise RuntimeError("unsupported composition result sidecar schema")
    if provenance.get("composition_id") != document.get("composition_id"):
        raise RuntimeError("existing composition output belongs to another composition_id")
    if provenance.get("piece") != piece:
        raise RuntimeError("existing composition output piece metadata changed")
    source = provenance.get("source") or {}
    if source.get("sha256") != (document.get("source") or {}).get("sha256"):
        raise RuntimeError("existing composition output source SHA does not match")
    output = provenance.get("output") or {}
    expected_path = str(output_path.resolve(strict=False))
    if str(output.get("local_path") or "") != expected_path:
        raise RuntimeError("existing composition output path does not match provenance")
    if not output_path.is_file() or output_path.stat().st_size <= 1024:
        raise RuntimeError("existing composition output is missing/empty")
    if output_path.stat().st_size != int(output.get("bytes") or 0):
        raise RuntimeError("existing composition output size changed")
    if sha256_file(output_path) != output.get("sha256"):
        raise RuntimeError("existing composition output bytes changed")
    expected_result_id = _canonical_sha256(
        {key: value for key, value in provenance.items() if key != "result_id"}
    )
    if provenance.get("result_id") != expected_result_id:
        raise RuntimeError("existing composition result_id is stale or corrupted")
    return _result_summary(provenance, sidecar_path)


def _publish_new_file(temp_path: Path, final_path: Path) -> None:
    """Publish a same-directory temporary file without overwriting an existing path."""
    try:
        os.link(temp_path, final_path)
    except FileExistsError:
        raise
    except OSError:
        with temp_path.open("rb") as source, final_path.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
    finally:
        temp_path.unlink(missing_ok=True)


async def _render_piece(
    document: dict[str, Any],
    piece: dict[str, Any],
    *,
    source_path: Path,
    source_duration: float,
    output_dir: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    piece_id = str(piece["piece_id"])
    safe_name = _safe_name(piece_id)
    output_path = output_dir / f"{safe_name}.mp4"
    sidecar_path = output_dir / f"{safe_name}.provenance.json"
    if output_path.exists() or sidecar_path.exists():
        return _load_verified_existing_result(document, piece, output_path, sidecar_path)

    filter_complex, expected_duration = _piece_filter(piece)
    fd, temp_name = tempfile.mkstemp(prefix=f".{safe_name}_", suffix=".mp4", dir=output_dir)
    os.close(fd)
    temp_output = Path(temp_name)
    temp_output.unlink(missing_ok=True)
    temp_sidecar = output_dir / f".{safe_name}_{os.getpid()}.provenance.tmp"
    temp_sidecar.unlink(missing_ok=True)
    try:
        command = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
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
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-n",
            str(temp_output),
        ]
        result = await run_cancellable_process(command, timeout=7200, text=True)
        if result.returncode != 0 or not temp_output.exists() or temp_output.stat().st_size <= 1024:
            raise RuntimeError(f"ffmpeg composition failed for {piece_id}")
        probe = await probe_media_async(temp_output)
        if not media_probe_is_deliverable(probe):
            raise RuntimeError(f"rendered composition failed media probe: {piece_id}")
        assert probe is not None
        tolerance = max(0.35, min(1.0, expected_duration * 0.01))
        if abs(float(probe.duration) - expected_duration) > tolerance:
            raise RuntimeError(
                f"composition duration mismatch {piece_id}: expected={expected_duration:.3f}s "
                f"actual={probe.duration:.3f}s"
            )
        provenance = {
            "schema_name": RESULT_SCHEMA_NAME,
            "schema_version": RESULT_SCHEMA_VERSION,
            "composition_id": document["composition_id"],
            "piece": piece,
            "source": {
                "local_path": str(source_path.resolve(strict=False)),
                "sha256": document["source"]["sha256"],
                "duration_seconds": round(source_duration, 3),
            },
            "output": {
                "local_path": str(output_path.resolve(strict=False)),
                "sha256": sha256_file(temp_output),
                "bytes": temp_output.stat().st_size,
                "duration_seconds": round(float(probe.duration), 3),
            },
        }
        provenance["result_id"] = _canonical_sha256(provenance)
        temp_sidecar.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        try:
            with sidecar_path.open("x", encoding="utf-8") as stream:
                stream.write(temp_sidecar.read_text(encoding="utf-8"))
            try:
                _publish_new_file(temp_output, output_path)
            except Exception:
                sidecar_path.unlink(missing_ok=True)
                raise
        finally:
            temp_sidecar.unlink(missing_ok=True)
        return _load_verified_existing_result(document, piece, output_path, sidecar_path)
    finally:
        temp_output.unlink(missing_ok=True)
        temp_sidecar.unlink(missing_ok=True)


async def render_composition(
    document: dict[str, Any],
    *,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Render every approved piece and resume only exact verified prior outputs."""
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
        results.append(
            await _render_piece(
                document,
                piece,
                source_path=source_path,
                source_duration=source_duration,
                output_dir=output_dir,
                ffmpeg=ffmpeg,
            )
        )
    return results


def _verified_handoff_result(
    document: dict[str, Any],
    piece: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    output_path = Path(str(result.get("output_path") or ""))
    sidecar_path = Path(str(result.get("provenance_path") or ""))
    verified = _load_verified_existing_result(document, piece, output_path, sidecar_path)
    for key in (
        "output_sha256",
        "output_bytes",
        "duration_seconds",
        "result_id",
        "provenance_sha256",
    ):
        if result.get(key) != verified.get(key):
            raise ValueError(f"rendered result field {key} does not match provenance for {piece['piece_id']}")
    return verified


def build_release_handoff(
    document: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create an exact provider-inert package for a later release manager."""
    errors = validate_composition_document(document)
    if errors:
        raise ValueError("composition validation failed before handoff")
    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("rendered result must be an object")
        piece_id = str(item.get("piece_id") or "")
        if not piece_id or piece_id in by_id:
            raise ValueError(f"duplicate or empty rendered piece_id: {piece_id}")
        by_id[piece_id] = item
    expected_ids = {str(piece.get("piece_id") or "") for piece in document.get("pieces") or []}
    extra = sorted(set(by_id) - expected_ids)
    if extra:
        raise ValueError("unexpected rendered results: " + ", ".join(extra))

    outputs: list[dict[str, Any]] = []
    for piece in document.get("pieces") or []:
        piece_id = str(piece.get("piece_id") or "")
        result = by_id.get(piece_id)
        if result is None:
            raise ValueError(f"missing rendered result for {piece_id}")
        verified = _verified_handoff_result(document, piece, result)
        outputs.append(
            {
                "piece_id": piece_id,
                "kind": piece.get("kind"),
                "media": {
                    "local_path": verified["output_path"],
                    "sha256": verified["output_sha256"],
                    "bytes": verified["output_bytes"],
                    "duration_seconds": verified["duration_seconds"],
                    "provenance_path": verified["provenance_path"],
                    "provenance_sha256": verified["provenance_sha256"],
                    "result_id": verified["result_id"],
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
    start = _finite_float(candidate.get("start_seconds"))
    end = _finite_float(candidate.get("end_seconds"))
    if start is None or end is None or end <= start:
        return True
    return any(start < span_end and end > span_start for span_start, span_end in spans)


__all__ = [
    "ASSEMBLY_MODES",
    "COMPOSITION_SCHEMA_NAME",
    "COMPOSITION_SCHEMA_VERSION",
    "HANDOFF_SCHEMA_NAME",
    "HANDOFF_SCHEMA_VERSION",
    "PIECE_KINDS",
    "RESULT_SCHEMA_NAME",
    "RESULT_SCHEMA_VERSION",
    "build_composition_template",
    "build_release_handoff",
    "candidate_overlap_with_spans",
    "composition_id",
    "piece_duration",
    "refresh_composition_id",
    "render_composition",
    "validate_composition_document",
]
