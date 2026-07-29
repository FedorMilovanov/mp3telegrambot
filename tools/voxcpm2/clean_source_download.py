#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed source cache for clean Dub Studio projects."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import generic_short_runtime as hardened

POLICY = "clean-source-download-manifest-v1"
MIN_SOURCE_BYTES = 100_000
_SAMPLE_BYTES = 256 * 1024


def _manifest_path(source: Path) -> Path:
    return source.with_suffix(source.suffix + ".download.json")


def _sampled_sha256(path: Path, *, block_size: int = _SAMPLE_BYTES) -> str:
    size = int(path.stat().st_size)
    block = max(4096, int(block_size))
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    if size <= block * 3:
        digest.update(path.read_bytes())
        return digest.hexdigest()
    positions = (0, max(0, (size - block) // 2), max(0, size - block))
    with path.open("rb") as handle:
        for position in positions:
            handle.seek(position)
            chunk = handle.read(block)
            digest.update(str(position).encode("ascii"))
            digest.update(len(chunk).to_bytes(8, "big"))
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _metadata(url: str) -> dict[str, Any]:
    process = pipeline.run_checked(
        [
            *hardened._ytdlp_base(),
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            str(url),
        ],
        capture=True,
        timeout=300,
    )
    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp вернул повреждённый metadata JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("yt-dlp metadata не является JSON-объектом.")
    video_id = str(payload.get("id") or "").strip()
    if not video_id:
        raise RuntimeError("yt-dlp metadata не содержит video ID.")
    return payload


def _cache_matches(
    source: Path,
    manifest: dict[str, Any] | None,
    *,
    video_id: str,
) -> bool:
    if not source.is_file() or source.stat().st_size < MIN_SOURCE_BYTES:
        return False
    if not isinstance(manifest, dict):
        return False
    try:
        expected_size = int(manifest.get("size_bytes") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if (
        manifest.get("policy") != POLICY
        or str(manifest.get("video_id") or "") != video_id
        or expected_size != int(source.stat().st_size)
    ):
        return False
    expected_hash = str(manifest.get("sampled_sha256") or "").strip().lower()
    if len(expected_hash) != 64:
        return False
    return _sampled_sha256(source) == expected_hash


def _write_manifest(
    path: Path,
    *,
    source: Path,
    source_url: str,
    metadata: dict[str, Any],
) -> None:
    payload = {
        "schema_version": 1,
        "policy": POLICY,
        "video_id": str(metadata["id"]),
        "source_url": str(source_url),
        "webpage_url": str(metadata.get("webpage_url") or ""),
        "size_bytes": int(source.stat().st_size),
        "sampled_sha256": _sampled_sha256(source),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def download_source(url: str, source: Path) -> dict[str, Any]:
    """Reuse source.mp4 only when its durable manifest proves the same video."""
    source = Path(source)
    source.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(source)
    metadata = _metadata(str(url))
    video_id = str(metadata["id"])
    manifest = _read_manifest(manifest_path)
    if _cache_matches(source, manifest, video_id=video_id):
        pipeline.log(
            f"Source cache verified: video_id={video_id}; "
            f"size={source.stat().st_size}; policy={POLICY}"
        )
        return metadata

    source.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)
    pipeline.log(f"Source cache missing or mismatched; downloading video_id={video_id}")
    pipeline.run_checked(
        [
            *hardened._ytdlp_base(),
            "--no-playlist",
            "--windows-filenames",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            str(source),
            str(url),
        ],
        timeout=1800,
    )
    if not source.is_file() or source.stat().st_size < MIN_SOURCE_BYTES:
        raise RuntimeError(
            f"yt-dlp не создал полноценный source.mp4: {source}"
        )
    _write_manifest(
        manifest_path,
        source=source,
        source_url=str(url),
        metadata=metadata,
    )
    pipeline.log(
        f"Source cache recorded: video_id={video_id}; "
        f"size={source.stat().st_size}; policy={POLICY}"
    )
    return metadata


__all__ = [
    "MIN_SOURCE_BYTES",
    "POLICY",
    "download_source",
]
