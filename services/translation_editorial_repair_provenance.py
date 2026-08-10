#!/usr/bin/env python3
"""Immutable provenance for deterministic translation-editorial repairs."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.translation_editorial import remap_after_drops, sha256_file

REPAIR_PROVENANCE_SCHEMA_NAME = "mp3telegrambot.translation-editorial-repair-result"
REPAIR_PROVENANCE_SCHEMA_VERSION = 1


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    token = str(value or "")
    return (
        len(token) == 71
        and token.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in token[7:])
    )


def _merge_drop_spans(repairs: Iterable[dict[str, Any]]) -> list[list[float]]:
    spans = sorted(
        [float(item["start_seconds"]), float(item["end_seconds"])]
        for item in repairs
        if item.get("type") == "drop_span"
    )
    merged: list[list[float]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [[round(start, 3), round(end, 3)] for start, end in merged]


def remap_timestamp_from_review_timeline(
    seconds: float,
    provenance: dict[str, Any],
) -> float:
    """Map a pre-repair translated-video timestamp onto the cleaned master."""
    drops = [tuple(item) for item in provenance.get("drop_spans") or []]
    return remap_after_drops(float(seconds), drops)


async def build_repair_provenance(
    *,
    manifest: dict[str, Any],
    review_path: Path,
    output_path: Path,
    repairs: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Bind one exact reviewed source and repair plan to one exact clean output."""
    review_path = Path(review_path)
    output_path = Path(output_path)
    if not review_path.is_file():
        raise FileNotFoundError(review_path)
    if not output_path.is_file() or output_path.stat().st_size <= 1024:
        raise FileNotFoundError(output_path)
    probe = await probe_media_async(output_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("editorial repair output failed provenance media probe")
    assert probe is not None
    output_sha, review_sha = await asyncio.gather(
        asyncio.to_thread(sha256_file, output_path),
        asyncio.to_thread(sha256_file, review_path),
    )
    repair_list = [
        {
            "type": str(item.get("type") or ""),
            "start_seconds": round(float(item["start_seconds"]), 3),
            "end_seconds": round(float(item["end_seconds"]), 3),
        }
        for item in repairs
    ]
    source = (manifest.get("source") or {}).get("translated_video") or {}
    document = {
        "schema_name": REPAIR_PROVENANCE_SCHEMA_NAME,
        "schema_version": REPAIR_PROVENANCE_SCHEMA_VERSION,
        "review_pack_id": manifest.get("review_pack_id"),
        "review_sha256": review_sha,
        "source": {
            "local_path": str(source.get("local_path") or ""),
            "sha256": source.get("sha256"),
            "bytes": source.get("bytes"),
            "duration_seconds": (manifest.get("source") or {}).get("duration_seconds"),
        },
        "repairs": repair_list,
        "drop_spans": _merge_drop_spans(repair_list),
        "timeline": {
            "input": "review/translated-video timeline",
            "output": "cleaned-master timeline",
            "mapping": "output_t = input_t - removed_drop_duration_before_input_t",
            "mute_span_changes_duration": False,
        },
        "output": {
            "local_path": str(output_path.resolve(strict=False)),
            "sha256": output_sha,
            "bytes": output_path.stat().st_size,
            "duration_seconds": round(float(probe.duration), 3),
        },
    }
    document["repair_result_id"] = _canonical_sha256(document)
    return document


def validate_repair_provenance_document(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schema_name") != REPAIR_PROVENANCE_SCHEMA_NAME:
        errors.append("wrong repair provenance schema_name")
    if document.get("schema_version") != REPAIR_PROVENANCE_SCHEMA_VERSION:
        errors.append("wrong repair provenance schema_version")
    try:
        expected = _canonical_sha256(
            {key: value for key, value in document.items() if key != "repair_result_id"}
        )
    except (TypeError, ValueError):
        expected = ""
        errors.append("repair provenance contains non-serializable values")
    if document.get("repair_result_id") != expected:
        errors.append("repair_result_id does not match document content")
    if not _is_sha256(document.get("review_pack_id")):
        errors.append("repair provenance review_pack_id is invalid")
    if not _is_sha256(document.get("review_sha256")):
        errors.append("repair provenance review_sha256 is invalid")
    source = document.get("source")
    output = document.get("output")
    if not isinstance(source, dict) or not isinstance(output, dict):
        errors.append("repair provenance source/output must be objects")
    else:
        if not _is_sha256(source.get("sha256")):
            errors.append("repair provenance source SHA-256 is invalid")
        if not _is_sha256(output.get("sha256")):
            errors.append("repair provenance output SHA-256 is invalid")
    repairs = document.get("repairs")
    if not isinstance(repairs, list):
        errors.append("repair provenance repairs must be a list")
    drops = document.get("drop_spans")
    if not isinstance(drops, list):
        errors.append("repair provenance drop_spans must be a list")
    return errors


async def verify_repair_provenance(
    sidecar_path: Path,
    *,
    expected_output_path: Path | None = None,
) -> dict[str, Any]:
    """Reload and rehash the exact clean output named by a repair sidecar."""
    sidecar_path = Path(sidecar_path)
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("repair provenance root must be an object")
    errors = validate_repair_provenance_document(data)
    if errors:
        raise ValueError("repair provenance validation failed: " + "; ".join(errors))
    output = data["output"]
    output_path = Path(str(output.get("local_path") or ""))
    if expected_output_path is not None:
        expected = Path(expected_output_path).resolve(strict=False)
        if output_path.resolve(strict=False) != expected:
            raise ValueError("repair provenance output path does not match expected clean master")
    if not output_path.is_file() or output_path.stat().st_size <= 1024:
        raise FileNotFoundError(output_path)
    if output_path.stat().st_size != int(output.get("bytes") or 0):
        raise RuntimeError("repair provenance output size changed")
    actual_sha = await asyncio.to_thread(sha256_file, output_path)
    if actual_sha != output.get("sha256"):
        raise RuntimeError("repair provenance output bytes changed")
    probe = await probe_media_async(output_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("repair provenance output failed media probe")
    assert probe is not None
    if abs(float(probe.duration) - float(output.get("duration_seconds") or 0.0)) > 0.75:
        raise RuntimeError("repair provenance output duration changed")
    return data


def write_repair_provenance(path: Path, document: dict[str, Any]) -> Path:
    """Write one immutable sidecar; an identical rerun may reuse it."""
    path = Path(path)
    errors = validate_repair_provenance_document(document)
    if errors:
        raise ValueError("repair provenance validation failed: " + "; ".join(errors))
    payload = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    if path.exists():
        if path.read_text(encoding="utf-8") == payload:
            return path
        raise FileExistsError(f"refusing to overwrite different repair provenance: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "REPAIR_PROVENANCE_SCHEMA_NAME",
    "REPAIR_PROVENANCE_SCHEMA_VERSION",
    "build_repair_provenance",
    "remap_timestamp_from_review_timeline",
    "validate_repair_provenance_document",
    "verify_repair_provenance",
    "write_repair_provenance",
]
