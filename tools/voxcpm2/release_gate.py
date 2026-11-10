#!/usr/bin/env python3
"""Fail-closed acceptance gate for local, human-reviewed dubbed-media repairs.

This is not a voice-quality detector. It checks evidence structure, the exact
source/output file hashes, and human review tied to the FINAL encoded artifact.
A clip-level approval never implies whole-film or publication approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, TypeGuard


HEX_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
REQUIRED_CHECKS = (
    "source_streams_verified",
    "pcm_scope_verified",
    "postmix_asr_completed",
    "meaning_verified",
    "join_tone_verified",
)
PATCH_TEXT_FIELDS = (
    "issue_id",
    "speaker",
    "source_en",
    "old_ru",
    "new_ru",
    "left_word",
    "right_word",
)


def _nonempty(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _sha(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and HEX_SHA256.fullmatch(value) is not None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_file(
    data: Any, field: str, manifest_path: Path, errors: list[str]
) -> tuple[Path | None, str | None]:
    if not isinstance(data, dict):
        errors.append(f"{field}: expected object with path and sha256")
        return None, None
    name, claimed_sha = data.get("path"), data.get("sha256")
    if not _nonempty(name):
        errors.append(f"{field}.path: missing")
        return None, None
    if not _sha(claimed_sha):
        errors.append(f"{field}.sha256: expected 64 hexadecimal characters")
        return None, None
    path = Path(name)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if not path.is_file():
        errors.append(f"{field}.path: file not found: {path}")
        return path, claimed_sha.lower()
    try:
        actual = _sha256_file(path)
    except OSError as exc:
        errors.append(f"{field}.path: unreadable: {exc}")
    else:
        if actual != claimed_sha.lower():
            errors.append(f"{field}.sha256: file changed since evidence was recorded")
    return path, claimed_sha.lower()


def _valid_time(value: Any) -> TypeGuard[int | float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _check_windows(data: Any, errors: list[str]) -> list[str]:
    if not isinstance(data, list) or not data:
        errors.append("changes: expected at least one changed window")
        return []
    ids: list[str] = []
    windows: list[tuple[float, float]] = []
    for index, item in enumerate(data):
        prefix = f"changes[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: expected object")
            continue
        for field in PATCH_TEXT_FIELDS:
            if not _nonempty(item.get(field)):
                errors.append(f"{prefix}.{field}: missing")
        issue_id = item.get("issue_id")
        if _nonempty(issue_id):
            ids.append(issue_id)
        start, end = item.get("start_s"), item.get("end_s")
        if _valid_time(start) and _valid_time(end) and start >= 0 and end > start:
            windows.append((float(start), float(end)))
        else:
            errors.append(f"{prefix}: invalid start_s/end_s")
        if item.get("postmix_aac_listened") is not True:
            errors.append(f"{prefix}.postmix_aac_listened: must be true")
        if item.get("asr_checked") is not True:
            errors.append(f"{prefix}.asr_checked: must be true")
        method = item.get("method")
        if method not in ("pcm_edit", "tts"):
            errors.append(f"{prefix}.method: must be pcm_edit or tts")
        elif method == "tts" and not _sha(item.get("native_wav_sha256")):
            errors.append(f"{prefix}.native_wav_sha256: required for TTS")
    if len(set(ids)) != len(ids):
        errors.append("changes: duplicate issue_id")
    for left, right in zip(sorted(windows), sorted(windows)[1:]):
        if right[0] < left[1]:
            errors.append("changes: overlapping edit windows")
            break
    return ids


def _check_review(data: Any, artifact_sha: str | None, ids: list[str], errors: list[str]) -> None:
    if not isinstance(data, dict):
        errors.append("human_review: missing human acceptance evidence")
        return
    reviewer = data.get("reviewer")
    if not _nonempty(reviewer) or reviewer.strip().lower() in ("whisper", "asr", "model", "ai"):
        errors.append("human_review.reviewer: actual human reviewer required")
    if data.get("verdict") != "APPROVED":
        errors.append("human_review.verdict: must be APPROVED")
    if data.get("listened_from_final_mp4") is not True:
        errors.append("human_review.listened_from_final_mp4: must be true")
    review_sha = data.get("artifact_sha256")
    if not _sha(review_sha) or artifact_sha is None or review_sha.lower() != artifact_sha:
        errors.append("human_review.artifact_sha256: must match final artifact")
    reviewed = data.get("accepted_issue_ids")
    if not isinstance(reviewed, list) or any(not _nonempty(x) for x in reviewed):
        errors.append("human_review.accepted_issue_ids: expected issue ID list")
    elif len(reviewed) != len(ids) or len(set(reviewed)) != len(reviewed) or set(reviewed) != set(ids):
        errors.append("human_review.accepted_issue_ids: not all edited windows accepted")
    timestamp = data.get("reviewed_at")
    try:
        if not _nonempty(timestamp):
            raise ValueError("missing")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone")
    except (ValueError, TypeError, OverflowError):
        errors.append("human_review.reviewed_at: timezone-aware ISO 8601 required")


def assess(manifest: Any, manifest_path: Path, target: str) -> dict[str, Any]:
    """Return an evidence gate verdict; never assert the sound was actually heard."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return {"passed": False, "status": "BLOCKED", "target": target, "errors": ["manifest: expected object"]}
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        errors.append("schema_version: expected 1")
    if target not in ("clip", "publication") or manifest.get("target") != target:
        errors.append("target: must match explicit --target clip or publication")
    source_path, source_sha = _verify_file(manifest.get("source"), "source", manifest_path, errors)
    artifact_path, artifact_sha = _verify_file(manifest.get("artifact"), "artifact", manifest_path, errors)
    if source_path is not None and source_path == artifact_path:
        errors.append("artifact: cannot overwrite source")
    if source_sha and artifact_sha and source_sha == artifact_sha:
        errors.append("artifact: same hash as source despite declared edits")

    ids = _check_windows(manifest.get("changes"), errors)
    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks: missing")
    else:
        for field in REQUIRED_CHECKS:
            if checks.get(field) is not True:
                errors.append(f"checks.{field}: must be true")
    review = manifest.get("human_review")
    _check_review(review, artifact_sha, ids, errors)

    disagreements = manifest.get("asr_disagreements")
    if not isinstance(disagreements, list) or any(not _nonempty(x) for x in disagreements):
        errors.append("asr_disagreements: expected list of issue IDs")
    elif len(set(disagreements)) != len(disagreements) or not set(disagreements).issubset(set(ids)):
        errors.append("asr_disagreements: IDs must be unique and refer to edited windows")
    elif disagreements:
        if not isinstance(review, dict) or review.get("asr_disagreements_resolved_by_listening") is not True:
            errors.append("human_review.asr_disagreements_resolved_by_listening: required")
        if not isinstance(review, dict) or not _nonempty(review.get("asr_resolution_note")):
            errors.append("human_review.asr_resolution_note: required")

    subtitle_status = manifest.get("subtitle_status")
    if subtitle_status not in ("PASS", "OPEN", "NOT_APPLICABLE"):
        errors.append("subtitle_status: expected PASS, OPEN or NOT_APPLICABLE")
    open_issues = manifest.get("open_issues")
    if not isinstance(open_issues, list) or any(not _nonempty(x) for x in open_issues):
        errors.append("open_issues: expected list of issue descriptions")
    if target == "publication":
        if subtitle_status != "PASS":
            errors.append("publication: subtitles must be PASS")
        if open_issues != []:
            errors.append("publication: open_issues must be empty")
        publishing = manifest.get("publication")
        if not isinstance(publishing, dict):
            errors.append("publication: missing approvals")
        else:
            for field in ("whole_film_listened", "rights_cleared", "publisher_approved"):
                if publishing.get(field) is not True:
                    errors.append(f"publication.{field}: must be true")

    passed = not errors
    status = "BLOCKED"
    if passed:
        status = "CLIP_ACCEPTED_ONLY_NOT_RELEASE_READY" if target == "clip" else "PUBLICATION_EVIDENCE_ACCEPTED"
    return {"passed": passed, "status": status, "target": target, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Local JSON manifest (paths relative to manifest)")
    parser.add_argument("--target", choices=("clip", "publication"), required=True)
    parser.add_argument("--report", type=Path, help="Optional machine-readable result JSON")
    args = parser.parse_args()

    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        result = {
            "passed": False,
            "status": "BLOCKED",
            "target": args.target,
            "errors": [f"manifest unreadable: {exc}"],
        }
    else:
        result = assess(payload, args.manifest.resolve(), args.target)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
