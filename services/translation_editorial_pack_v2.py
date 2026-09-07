#!/usr/bin/env python3
"""Translation Editorial immutable review-pack contract v2.

V2 deliberately layers the human/model-facing review contract on top of the
already-stable v1 evidence identity. This fixes the v1 hole where changing
REVIEW_INSTRUCTIONS.md or review_contract could reuse the same immutable ZIP
name and then fail a later verifier.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from services.translation_editorial import (
    ACTION_TYPES,
    EXECUTABLE_ACTIONS,
    ISSUE_SEVERITIES,
    PACK_SCHEMA_NAME,
    VERDICTS,
    build_review_pack as _build_review_pack_v1,
)

PACK_SCHEMA_VERSION = 2
_V1_SCHEMA_VERSION = 1


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def review_contract() -> dict[str, Any]:
    return {
        "verdicts": sorted(VERDICTS),
        "issue_severities": sorted(ISSUE_SEVERITIES),
        "action_types": sorted(ACTION_TYPES),
        "automatically_executable_actions": sorted(EXECUTABLE_ACTIONS),
        "borrow_span_policy": (
            "review-only; donor media is never synthesized or inserted automatically"
        ),
    }


def review_instructions(*, has_timeline: bool) -> str:
    if has_timeline:
        return (
            "# Translation Editorial Review v2\n\n"
            "Compare `original.srt` with the words actually heard in "
            "`russian_whisper.srt`. The two transcripts may use different timelines; "
            "read `manifest.json.timeline` before comparing nearby cues. Minor stylistic "
            "roughness is not a defect. Return a `review.json` bound to the exact "
            "`review_pack_id`.\n\n"
            "Safe automatic repairs are `drop_span` and `mute_span`. `borrow_span` may "
            "identify a same-voice donor candidate, but requires explicit approval and is "
            "intentionally not auto-executed.\n"
        )
    return (
        "# Translation Editorial Review v2\n\n"
        "Upload this ZIP to the editor. Compare `original.srt` with the words actually "
        "heard in `russian_whisper.srt`. Minor stylistic roughness is not a defect. "
        "Return a `review.json` bound to the exact `review_pack_id` in manifest.json.\n\n"
        "Safe automatic repairs are `drop_span` and `mute_span`. `borrow_span` may "
        "identify a same-voice donor candidate, but requires explicit human/editor "
        "approval and is intentionally not auto-executed.\n"
    )


def _stable_source_identity(source: Any) -> Any:
    """Match v1 path-stable intent without importing private implementation details."""
    if isinstance(source, dict):
        return {
            key: _stable_source_identity(value)
            for key, value in source.items()
            if key != "local_path"
        }
    if isinstance(source, list):
        return [_stable_source_identity(value) for value in source]
    return source


def _v1_evidence_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "schema_name": manifest.get("schema_name"),
        "schema_version": _V1_SCHEMA_VERSION,
        "source": _stable_source_identity(manifest.get("source")),
        "transcripts": manifest.get("transcripts"),
        "candidates": manifest.get("candidates"),
    }
    if "timeline" in manifest:
        identity["timeline"] = manifest.get("timeline")
    return identity


def _v2_identity(
    *,
    evidence_pack_id_v1: str,
    contract: dict[str, Any],
    instructions_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_name": PACK_SCHEMA_NAME,
        "schema_version": PACK_SCHEMA_VERSION,
        "evidence_pack_id_v1": evidence_pack_id_v1,
        "review_contract": contract,
        "review_instructions_sha256": instructions_sha256,
    }


def _safe_media_id(media_id: str) -> str:
    safe = "".join(
        char if (char.isalnum() or char in "_-") else "_"
        for char in str(media_id or "media")
    )
    return safe[:100] or "media"


def _verify_evidence_members(archive: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    transcripts = manifest.get("transcripts")
    if not isinstance(transcripts, dict):
        raise ValueError("translation editorial v2 transcripts must be an object")
    for key, entry in transcripts.items():
        if not isinstance(entry, dict):
            raise ValueError(f"translation editorial v2 transcript entry invalid: {key}")
        name = str(entry.get("file") or "")
        expected_sha = str(entry.get("sha256") or "")
        if not name or not expected_sha:
            raise ValueError(f"translation editorial v2 transcript hash missing: {key}")
        try:
            payload = archive.read(name)
        except KeyError as exc:
            raise ValueError(f"translation editorial v2 transcript missing: {name}") from exc
        if _sha256_bytes(payload) != expected_sha:
            raise ValueError(f"translation editorial v2 transcript hash mismatch: {name}")

    try:
        candidates = json.loads(archive.read("candidates.json").decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("translation editorial v2 candidates.json is invalid") from exc
    if candidates != manifest.get("candidates"):
        raise ValueError("translation editorial v2 candidates differ from manifest")


def load_verified_review_pack(pack_path: Path) -> dict[str, Any]:
    """Verify v2 evidence identity, exact review contract and exact instructions."""
    pack_path = Path(pack_path)
    if not pack_path.is_file() or pack_path.stat().st_size <= 0:
        raise FileNotFoundError(pack_path)

    with zipfile.ZipFile(pack_path, "r") as archive:
        names = [info.filename for info in archive.infolist()]
        if len(names) != len(set(names)):
            raise ValueError("translation editorial v2 ZIP contains duplicate members")
        if "manifest.json" not in names or "REVIEW_INSTRUCTIONS.md" not in names:
            raise ValueError("translation editorial v2 ZIP is missing contract members")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("translation editorial v2 manifest.json is invalid") from exc
        if not isinstance(manifest, dict):
            raise ValueError("translation editorial v2 manifest root must be an object")
        instructions_bytes = archive.read("REVIEW_INSTRUCTIONS.md")
        _verify_evidence_members(archive, manifest)

    if manifest.get("schema_name") != PACK_SCHEMA_NAME:
        raise ValueError("translation editorial v2 schema_name mismatch")
    if manifest.get("schema_version") != PACK_SCHEMA_VERSION:
        raise ValueError("translation editorial pack is not current v2")

    evidence_id = str(manifest.get("evidence_pack_id_v1") or "")
    expected_evidence_id = _canonical_sha256(_v1_evidence_identity(manifest))
    if evidence_id != expected_evidence_id:
        raise ValueError("translation editorial v2 evidence identity mismatch")

    contract = review_contract()
    if manifest.get("review_contract") != contract:
        raise ValueError("translation editorial v2 review_contract was modified")

    expected_instructions = review_instructions(has_timeline="timeline" in manifest)
    expected_instructions_bytes = expected_instructions.encode("utf-8")
    expected_instructions_sha = _sha256_bytes(expected_instructions_bytes)
    if manifest.get("review_instructions_sha256") != expected_instructions_sha:
        raise ValueError("translation editorial v2 instruction digest was modified")
    if instructions_bytes != expected_instructions_bytes:
        raise ValueError("translation editorial v2 REVIEW_INSTRUCTIONS.md was modified")

    expected_pack_id = _canonical_sha256(
        _v2_identity(
            evidence_pack_id_v1=evidence_id,
            contract=contract,
            instructions_sha256=expected_instructions_sha,
        )
    )
    if manifest.get("review_pack_id") != expected_pack_id:
        raise ValueError("translation editorial v2 review_pack_id mismatch")
    return manifest


def build_review_pack(*args: Any, **kwargs: Any) -> Path:
    """Build v1 evidence in an isolated staging dir, then seal it with v2 identity."""
    output_dir = Path(kwargs.get("output_dir") or args[0])
    media_id = str(kwargs.get("media_id") or (args[1] if len(args) > 1 else "media"))
    output_dir.mkdir(parents=True, exist_ok=True)

    staged_kwargs = dict(kwargs)
    with tempfile.TemporaryDirectory(prefix=".translation_editorial_v2_", dir=output_dir) as td:
        if "output_dir" in staged_kwargs:
            staged_kwargs["output_dir"] = Path(td)
            v1_pack = _build_review_pack_v1(*args, **staged_kwargs)
        else:
            staged_args = list(args)
            staged_args[0] = Path(td)
            v1_pack = _build_review_pack_v1(*staged_args, **staged_kwargs)

        with zipfile.ZipFile(v1_pack, "r") as source_zip:
            raw_members = {
                info.filename: source_zip.read(info.filename)
                for info in source_zip.infolist()
                if info.filename not in {"manifest.json", "REVIEW_INSTRUCTIONS.md"}
            }
            v1_manifest = json.loads(source_zip.read("manifest.json").decode("utf-8"))

    if not isinstance(v1_manifest, dict):
        raise ValueError("translation editorial v1 staging manifest is invalid")
    evidence_id = str(v1_manifest.get("review_pack_id") or "")
    if evidence_id != _canonical_sha256(_v1_evidence_identity(v1_manifest)):
        raise ValueError("translation editorial v1 evidence identity is invalid")

    contract = review_contract()
    instructions = review_instructions(has_timeline="timeline" in v1_manifest)
    instructions_sha = _sha256_bytes(instructions.encode("utf-8"))
    pack_id = _canonical_sha256(
        _v2_identity(
            evidence_pack_id_v1=evidence_id,
            contract=contract,
            instructions_sha256=instructions_sha,
        )
    )

    manifest = dict(v1_manifest)
    manifest.update(
        schema_version=PACK_SCHEMA_VERSION,
        evidence_pack_id_v1=evidence_id,
        review_contract=contract,
        review_instructions_sha256=instructions_sha,
        review_pack_id=pack_id,
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
        for name, payload in raw_members.items():
            archive.writestr(name, payload)
        archive.writestr("REVIEW_INSTRUCTIONS.md", instructions)

    suffix = pack_id[7:19]
    destination = output_dir / (
        f"{_safe_media_id(media_id)}_translation_editorial_v2_{suffix}.zip"
    )
    payload = buffer.getvalue()
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        existing = load_verified_review_pack(destination)
        if existing.get("review_pack_id") != pack_id:
            raise RuntimeError("immutable translation editorial v2 path collision")
        return destination

    try:
        load_verified_review_pack(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


__all__ = [
    "PACK_SCHEMA_VERSION",
    "build_review_pack",
    "load_verified_review_pack",
    "review_contract",
    "review_instructions",
]
