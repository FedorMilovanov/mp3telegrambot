#!/usr/bin/env python3
"""Deterministic editorial QA exchange for Yandex LiveDub material.

The module keeps AI outside the media-execution boundary. It builds an immutable
review pack from the real source transcript, the Russian Whisper transcript and
Factory candidate metadata. ChatGPT, Gemini or a human editor may return a
versioned review document, but only deterministic actions validated against the
exact verified pack may reach FFmpeg.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from services.async_process import run_cancellable_process
from services.async_worker import await_owned_coroutine
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async

PACK_SCHEMA_NAME = "mp3telegrambot.translation-editorial-review-pack"
PACK_SCHEMA_VERSION = 1
REVIEW_SCHEMA_NAME = "mp3telegrambot.translation-editorial-review"
REVIEW_SCHEMA_VERSION = 1

VERDICTS = {"keep", "repair", "reject"}
ISSUE_SEVERITIES = {"roughness", "minor", "major", "critical"}
ACTION_TYPES = {"drop_span", "mute_span", "borrow_span", "reject_region"}
EXECUTABLE_ACTIONS = {"drop_span", "mute_span"}
MAX_DROP_SPAN_SECONDS = 8.0


@dataclass(frozen=True)
class SrtCue:
    index: int
    start: float
    end: float
    text: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _is_canonical_sha256(value: Any) -> bool:
    token = str(value or "")
    if not token.startswith("sha256:") or len(token) != 71:
        return False
    return all(char in "0123456789abcdef" for char in token[7:])


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def drop_span_budget_seconds(duration: float) -> float:
    """Return the total automatic timeline-removal budget for one source."""
    source_duration = _finite_float(duration)
    if source_duration is None or source_duration <= 0:
        raise ValueError("drop-span budget duration must be finite and positive")
    return min(60.0, max(5.0, source_duration * 0.02))


def validate_drop_span_budget(
    repairs: Iterable[dict[str, Any]],
    duration: float,
) -> list[str]:
    """Bound destructive automatic deletion independently of model/editor intent."""
    source_duration = _finite_float(duration)
    if source_duration is None or source_duration <= 0:
        return ["drop-span budget duration must be finite and positive"]
    errors: list[str] = []
    spans: list[tuple[float, float]] = []
    for index, item in enumerate(repairs, 1):
        if not isinstance(item, dict) or item.get("type") != "drop_span":
            continue
        start = _finite_float(item.get("start_seconds"))
        end = _finite_float(item.get("end_seconds"))
        if start is None or end is None or start < 0 or end <= start:
            errors.append(f"drop_span[{index}] has invalid span")
            continue
        if end > source_duration + 0.05:
            errors.append(f"drop_span[{index}] exceeds source duration")
            continue
        span_seconds = end - start
        if span_seconds > MAX_DROP_SPAN_SECONDS + 0.001:
            errors.append(
                f"drop_span[{index}] is {span_seconds:.3f}s; "
                f"maximum automatic drop is {MAX_DROP_SPAN_SECONDS:.3f}s"
            )
        spans.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    total_removed = sum(end - start for start, end in merged)
    total_budget = drop_span_budget_seconds(source_duration)
    if total_removed > total_budget + 0.001:
        errors.append(
            f"merged automatic drop removal is {total_removed:.3f}s; "
            f"source budget is {total_budget:.3f}s"
        )
    return errors


def _srt_seconds(value: str) -> float:
    token = str(value or "").strip().split()[0].replace(",", ".")
    clock, dot, millis = token.partition(".")
    parts = clock.split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    if dot and (not millis.isdigit() or len(millis) > 3):
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    hours, minutes, seconds = (int(part) for part in parts)
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    fraction = int((millis + "000")[:3]) / 1000.0 if dot else 0.0
    return hours * 3600 + minutes * 60 + seconds + fraction


def _srt_time(seconds: float) -> str:
    value = max(0.0, float(seconds))
    millis = int(round(value * 1000.0))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _srt_blocks(raw: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in str(raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def parse_srt_text(raw: str) -> list[SrtCue]:
    cues: list[SrtCue] = []
    for lines in _srt_blocks(raw):
        if len(lines) < 2:
            continue
        time_index = 1 if lines[0].isdigit() else 0
        if time_index >= len(lines) or "-->" not in lines[time_index]:
            continue
        left, right = [part.strip() for part in lines[time_index].split("-->", 1)]
        try:
            start = _srt_seconds(left)
            end = _srt_seconds(right)
        except (IndexError, ValueError):
            continue
        text = " ".join(lines[time_index + 1 :]).strip()
        if not text or end <= start:
            continue
        cue_index = int(lines[0]) if time_index == 1 else len(cues) + 1
        cues.append(SrtCue(cue_index, start, end, text))
    return cues


def parse_srt(path: Path) -> list[SrtCue]:
    return parse_srt_text(Path(path).read_text(encoding="utf-8", errors="replace"))


def _word_key(text: str) -> str:
    normalized = "".join(
        char if (char.isalnum() or char == "_") else " "
        for char in str(text or "").casefold()
    )
    return " ".join(normalized.split())


def _contains_normalized_phrase(text: str, phrase: str) -> bool:
    haystack = _word_key(text)
    needle = _word_key(phrase)
    if not haystack or not needle:
        return False
    return f" {needle} " in f" {haystack} "


def find_donor_cues(
    srt_path: Path,
    phrase: str,
    *,
    exclude_start: float | None = None,
    exclude_end: float | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return grounded same-voice donor cue candidates; never edits media."""
    if not _word_key(phrase):
        return []
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = 12
    safe_limit = max(1, min(parsed_limit, 100))
    out: list[dict[str, Any]] = []
    for cue in parse_srt(srt_path):
        if not _contains_normalized_phrase(cue.text, phrase):
            continue
        if (
            exclude_start is not None
            and exclude_end is not None
            and cue.start < float(exclude_end)
            and cue.end > float(exclude_start)
        ):
            continue
        out.append(
            {
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "text": cue.text,
            }
        )
        if len(out) >= safe_limit:
            break
    return out


def _candidate_payload(kind: str, candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, candidate in enumerate(candidates, 1):
        item = dict(candidate)
        candidate_id = str(item.get("candidate_id") or f"{kind}:{index}").strip()
        if not candidate_id or candidate_id in seen_ids:
            raise ValueError(f"duplicate/empty review candidate_id: {candidate_id}")
        seen_ids.add(candidate_id)
        item["candidate_id"] = candidate_id
        out.append(item)
    return out


def _validate_candidate_ranges(
    candidates: dict[str, list[dict[str, Any]]],
    duration: float,
) -> None:
    for group_name, group in candidates.items():
        for index, item in enumerate(group, 1):
            start = _finite_float(item.get("start_seconds"))
            end = _finite_float(item.get("end_seconds"))
            if start is None or end is None:
                raise ValueError(f"{group_name}[{index}] candidate start/end must be finite")
            if start < 0 or end <= start or end > duration + 0.05:
                raise ValueError(f"{group_name}[{index}] candidate span outside source duration")


def _file_entry(path: Path, *, role: str) -> dict[str, Any]:
    path = Path(path)
    return {
        "file": path.name,
        "role": role,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _safe_media_id(media_id: str) -> str:
    safe = "".join(
        char if (char.isalnum() or char in "_-") else "_"
        for char in str(media_id or "media")
    )
    return safe[:100] or "media"


def _path_stable_source_identity(source_entry: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(source_entry, ensure_ascii=False, allow_nan=False))
    translated = copied.get("translated_video")
    if isinstance(translated, dict):
        translated.pop("local_path", None)
    return copied


def build_review_pack(
    *,
    output_dir: Path,
    media_id: str,
    source_url: str,
    title: str,
    performer: str,
    duration: float,
    source_video_path: Path,
    original_srt_path: Path,
    russian_whisper_srt_path: Path,
    russian_words_path: Path | None = None,
    shorts_candidates: Iterable[dict[str, Any]] = (),
    long_candidates: Iterable[dict[str, Any]] = (),
    timeline_metadata: dict[str, Any] | None = None,
) -> Path:
    """Build an immutable small ZIP suitable for ChatGPT/Gemini/human review."""
    source_video_path = Path(source_video_path)
    original_srt_path = Path(original_srt_path)
    russian_whisper_srt_path = Path(russian_whisper_srt_path)
    for required in (source_video_path, original_srt_path, russian_whisper_srt_path):
        if not required.exists() or required.stat().st_size <= 0:
            raise FileNotFoundError(f"review pack input missing/empty: {required}")
    source_duration = _finite_float(duration)
    if source_duration is None or source_duration <= 0:
        raise ValueError("review pack duration must be finite and positive")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_media_id(media_id)
    with tempfile.TemporaryDirectory(prefix=f".{safe_id}_review_", dir=output_dir) as temp_name:
        root = Path(temp_name)
        original_copy = root / "original.srt"
        russian_copy = root / "russian_whisper.srt"
        shutil.copy2(original_srt_path, original_copy)
        shutil.copy2(russian_whisper_srt_path, russian_copy)

        words_copy: Path | None = None
        if russian_words_path is not None and Path(russian_words_path).exists():
            words_copy = root / "russian_whisper_words.json"
            shutil.copy2(Path(russian_words_path), words_copy)

        candidates = {
            "shorts": _candidate_payload("short", shorts_candidates),
            "long_clips": _candidate_payload("long", long_candidates),
        }
        _validate_candidate_ranges(candidates, source_duration)
        candidates_path = root / "candidates.json"
        candidates_path.write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

        source_entry = {
            "url": str(source_url or ""),
            "media_id": str(media_id or ""),
            "title": str(title or ""),
            "performer": str(performer or ""),
            "duration_seconds": round(source_duration, 3),
            "translated_video": {
                "local_path": str(source_video_path.resolve(strict=False)),
                "sha256": sha256_file(source_video_path),
                "bytes": source_video_path.stat().st_size,
            },
        }
        transcripts: dict[str, Any] = {
            "original": _file_entry(original_copy, role="source_original_srt"),
            "russian_whisper": _file_entry(russian_copy, role="heard_russian_asr"),
        }
        if words_copy is not None:
            transcripts["russian_whisper_words"] = _file_entry(
                words_copy,
                role="heard_russian_word_timestamps",
            )
        timeline = dict(timeline_metadata or {})
        identity_payload = {
            "schema_name": PACK_SCHEMA_NAME,
            "schema_version": PACK_SCHEMA_VERSION,
            "source": _path_stable_source_identity(source_entry),
            "transcripts": transcripts,
            "candidates": candidates,
            "timeline": timeline,
        }
        review_pack_id = _canonical_sha256(identity_payload)
        manifest = {
            "schema_name": PACK_SCHEMA_NAME,
            "schema_version": PACK_SCHEMA_VERSION,
            "source": source_entry,
            "transcripts": transcripts,
            "candidates": candidates,
            "timeline": timeline,
            "review_pack_id": review_pack_id,
            "review_contract": {
                "verdicts": sorted(VERDICTS),
                "issue_severities": sorted(ISSUE_SEVERITIES),
                "action_types": sorted(ACTION_TYPES),
                "automatically_executable_actions": sorted(EXECUTABLE_ACTIONS),
                "borrow_span_policy": (
                    "review-only in v1; donor media is never synthesized or inserted automatically"
                ),
            },
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        instructions = (
            "# Translation Editorial Review v1\n\n"
            "Compare `original.srt` with the words actually heard in `russian_whisper.srt`. "
            "The two transcripts may use different timelines; read `manifest.json.timeline` "
            "before comparing nearby cues. Minor stylistic roughness is not a defect. "
            "Return a `review.json` bound to the exact `review_pack_id`.\n\n"
            "Safe automatic repairs in v1: `drop_span` and `mute_span`. `borrow_span` may "
            "identify a same-voice donor candidate, but requires explicit approval and is "
            "intentionally not auto-executed.\n"
        )
        (root / "REVIEW_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")

        suffix = review_pack_id[7:19]
        zip_path = output_dir / f"{safe_id}_translation_editorial_v1_{suffix}.zip"
        if zip_path.exists():
            existing = load_pack_manifest(zip_path)
            if existing.get("review_pack_id") == review_pack_id:
                return zip_path
            raise FileExistsError(f"review pack filename collision: {zip_path}")
        fd, temp_zip_name = tempfile.mkstemp(
            prefix=f".{safe_id}_translation_editorial_v1_",
            suffix=".zip",
            dir=output_dir,
        )
        os.close(fd)
        temp_zip = Path(temp_zip_name)
        try:
            with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.iterdir()):
                    if path.is_file():
                        archive.write(path, arcname=path.name)
            created = False
            try:
                with zip_path.open("xb") as target, temp_zip.open("rb") as source:
                    created = True
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            except FileExistsError:
                existing = load_pack_manifest(zip_path)
                if existing.get("review_pack_id") == review_pack_id:
                    return zip_path
                raise FileExistsError(f"review pack filename collision: {zip_path}")
            except Exception:
                if created:
                    zip_path.unlink(missing_ok=True)
                raise
        finally:
            temp_zip.unlink(missing_ok=True)
        load_pack_manifest(zip_path)
        return zip_path


def _verified_pack_identity(data: dict[str, Any]) -> dict[str, Any]:
    source = data.get("source")
    if "timeline" in data and isinstance(source, dict):
        source = _path_stable_source_identity(source)
    identity = {
        "schema_name": data.get("schema_name"),
        "schema_version": data.get("schema_version"),
        "source": source,
        "transcripts": data.get("transcripts"),
        "candidates": data.get("candidates"),
    }
    # PR #113 v1 packs predate timeline metadata. Preserve their exact identity
    # instead of retroactively changing their source-path-sensitive formula.
    if "timeline" in data:
        identity["timeline"] = data.get("timeline") or {}
    return identity


def load_pack_manifest(pack_path: Path) -> dict[str, Any]:
    """Load and cryptographically re-verify all identity-bearing pack contents."""
    pack_path = Path(pack_path)
    with zipfile.ZipFile(pack_path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("translation editorial pack contains duplicate ZIP members")
        required = {"manifest.json", "candidates.json", "original.srt", "russian_whisper.srt"}
        missing = sorted(required - set(names))
        if missing:
            raise ValueError("translation editorial pack missing: " + ", ".join(missing))
        data = json.loads(archive.read("manifest.json").decode("utf-8"))
        candidates = json.loads(archive.read("candidates.json").decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(candidates, dict):
            raise ValueError("translation editorial pack JSON roots must be objects")
        if data.get("schema_name") != PACK_SCHEMA_NAME or data.get("schema_version") != PACK_SCHEMA_VERSION:
            raise ValueError("unsupported translation editorial pack schema")
        if data.get("candidates") != candidates:
            raise ValueError("candidates.json does not match manifest candidates")

        source = data.get("source")
        if not isinstance(source, dict):
            raise ValueError("manifest source must be an object")
        duration = _finite_float(source.get("duration_seconds"))
        translated = source.get("translated_video")
        if duration is None or duration <= 0 or not isinstance(translated, dict):
            raise ValueError("manifest source identity is invalid")
        if not _is_canonical_sha256(translated.get("sha256")):
            raise ValueError("manifest translated-video SHA-256 is invalid")
        try:
            translated_bytes = int(translated.get("bytes"))
        except (TypeError, ValueError):
            translated_bytes = 0
        if translated_bytes <= 0 or not str(translated.get("local_path") or "").strip():
            raise ValueError("manifest translated-video path/size is invalid")

        transcripts = data.get("transcripts")
        if not isinstance(transcripts, dict):
            raise ValueError("manifest transcripts must be an object")
        for entry in transcripts.values():
            if not isinstance(entry, dict):
                raise ValueError("manifest transcript entry must be an object")
            filename = str(entry.get("file") or "")
            if not filename or Path(filename).name != filename or filename not in names:
                raise ValueError(f"invalid/missing transcript member: {filename}")
            if not _is_canonical_sha256(entry.get("sha256")):
                raise ValueError(f"invalid transcript SHA-256: {filename}")
            payload = archive.read(filename)
            if int(entry.get("bytes") or -1) != len(payload):
                raise ValueError(f"transcript byte count changed: {filename}")
            if entry.get("sha256") != _sha256_bytes(payload):
                raise ValueError(f"transcript SHA-256 changed: {filename}")

    _validate_candidate_ranges(candidates, duration)
    expected_id = _canonical_sha256(_verified_pack_identity(data))
    if data.get("review_pack_id") != expected_id:
        raise ValueError("review_pack_id does not match verified pack contents")
    return data


def _candidate_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for group in (manifest.get("candidates") or {}).values():
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict) or not item.get("candidate_id"):
                continue
            candidate_id = str(item["candidate_id"])
            if candidate_id in out:
                raise ValueError(f"duplicate candidate_id in manifest: {candidate_id}")
            out[candidate_id] = item
    return out


def _validate_issue(
    issue: Any,
    *,
    prefix: str,
    duration: float,
    candidate: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(issue, dict):
        return [f"{prefix}: must be an object"]
    start = _finite_float(issue.get("start_seconds"))
    end = _finite_float(issue.get("end_seconds"))
    if start is None or end is None:
        return [f"{prefix}: start/end must be finite numbers"]
    if start < 0 or end <= start or end > duration + 0.05:
        errors.append(f"{prefix}: span outside source duration")
    if candidate is not None:
        candidate_start = _finite_float(candidate.get("start_seconds"))
        candidate_end = _finite_float(candidate.get("end_seconds"))
        if candidate_start is not None and candidate_end is not None:
            if start < candidate_start - 0.50 or end > candidate_end + 0.50:
                errors.append(f"{prefix}: issue span lies outside reviewed candidate")
    if issue.get("severity") not in ISSUE_SEVERITIES:
        errors.append(f"{prefix}: invalid severity")
    action = issue.get("action")
    if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
        errors.append(f"{prefix}: invalid action")
        return errors
    if action.get("type") == "drop_span" and end - start > MAX_DROP_SPAN_SECONDS + 0.001:
        errors.append(
            f"{prefix}: drop_span exceeds {MAX_DROP_SPAN_SECONDS:.3f}s automatic surgical limit"
        )
    if action.get("type") == "borrow_span":
        donor_start = _finite_float(action.get("donor_start_seconds"))
        donor_end = _finite_float(action.get("donor_end_seconds"))
        if donor_start is None or donor_end is None:
            errors.append(f"{prefix}: borrow_span requires finite donor start/end")
        elif donor_start < 0 or donor_end <= donor_start or donor_end > duration + 0.05:
            errors.append(f"{prefix}: donor span outside source duration")
        elif donor_start < end and donor_end > start:
            errors.append(f"{prefix}: donor span overlaps replacement target")
    return errors


def _validate_verdict_issue_shape(prefix: str, verdict: Any, issues: Any) -> list[str]:
    errors: list[str] = []
    if verdict not in VERDICTS:
        errors.append(f"{prefix}.verdict must be keep|repair|reject")
    if not isinstance(issues, list):
        errors.append(f"{prefix}.issues must be a list")
        return errors
    if verdict == "keep" and issues:
        errors.append(f"{prefix}: keep verdict cannot carry repair/reject issues")
    if verdict == "repair" and not issues:
        errors.append(f"{prefix}: repair verdict requires at least one issue")
    return errors


def validate_review_document(review: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Validate editorial intent without guessing or silently correcting it."""
    errors: list[str] = []
    if review.get("schema_name") != REVIEW_SCHEMA_NAME:
        errors.append("wrong review schema_name")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        errors.append("wrong review schema_version")
    if review.get("review_pack_id") != manifest.get("review_pack_id"):
        errors.append("review_pack_id does not match the exact pack")

    duration = _finite_float((manifest.get("source") or {}).get("duration_seconds"))
    if duration is None or duration <= 0:
        return errors + ["manifest source duration is invalid"]
    candidate_map = _candidate_map(manifest)

    full = review.get("full_sermon")
    full_drop_repairs: list[dict[str, Any]] = []
    if not isinstance(full, dict):
        errors.append("full_sermon must be an object")
        full_issues: list[Any] = []
    else:
        full_issues = full.get("issues") if isinstance(full.get("issues"), list) else []
        errors.extend(
            _validate_verdict_issue_shape(
                "full_sermon",
                full.get("verdict"),
                full.get("issues"),
            )
        )
        seen_full: set[tuple[float, float, str]] = set()
        for index, issue in enumerate(full_issues, 1):
            errors.extend(
                _validate_issue(
                    issue,
                    prefix=f"full_sermon.issue[{index}]",
                    duration=duration,
                    candidate=None,
                )
            )
            if isinstance(issue, dict):
                start = _finite_float(issue.get("start_seconds"))
                end = _finite_float(issue.get("end_seconds"))
                action = issue.get("action") or {}
                if start is not None and end is not None and isinstance(action, dict):
                    action_type = str(action.get("type") or "")
                    key = (start, end, action_type)
                    if key in seen_full:
                        errors.append(f"full_sermon.issue[{index}]: duplicate issue/action span")
                    seen_full.add(key)
                    if action_type == "drop_span":
                        full_drop_repairs.append(
                            {
                                "type": "drop_span",
                                "start_seconds": start,
                                "end_seconds": end,
                            }
                        )
        errors.extend(
            f"full_sermon: {message}"
            for message in validate_drop_span_budget(full_drop_repairs, duration)
        )

    candidate_reviews = review.get("candidate_reviews")
    if not isinstance(candidate_reviews, list):
        errors.append("candidate_reviews must be a list")
        candidate_reviews = []
    seen_candidate_reviews: set[str] = set()
    for index, item in enumerate(candidate_reviews, 1):
        if not isinstance(item, dict):
            errors.append(f"candidate_reviews[{index}] must be an object")
            continue
        candidate_id = str(item.get("candidate_id") or "")
        candidate = candidate_map.get(candidate_id)
        if candidate is None:
            errors.append(f"unknown candidate_id: {candidate_id}")
        if candidate_id in seen_candidate_reviews:
            errors.append(f"duplicate candidate_id: {candidate_id}")
        seen_candidate_reviews.add(candidate_id)
        issues = item.get("issues")
        errors.extend(
            _validate_verdict_issue_shape(
                f"candidate {candidate_id}",
                item.get("verdict"),
                issues,
            )
        )
        if isinstance(issues, list):
            for issue_index, issue in enumerate(issues, 1):
                errors.extend(
                    _validate_issue(
                        issue,
                        prefix=f"candidate {candidate_id}.issue[{issue_index}]",
                        duration=duration,
                        candidate=candidate,
                    )
                )

    missing = sorted(set(candidate_map) - seen_candidate_reviews)
    if missing:
        errors.append("missing candidate reviews: " + ", ".join(missing))
    return errors


def collect_executable_repairs(review: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only explicitly approved full-sermon v1 actions."""
    full = review.get("full_sermon") or {}
    issues = full.get("issues") if isinstance(full, dict) else []
    if not isinstance(issues, list):
        return []
    out: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        action = issue.get("action") or {}
        if action.get("type") in EXECUTABLE_ACTIONS:
            out.append(
                {
                    "start_seconds": float(issue["start_seconds"]),
                    "end_seconds": float(issue["end_seconds"]),
                    "type": str(action["type"]),
                }
            )
    return out


def _merge_drop_spans(repairs: Iterable[dict[str, Any]]) -> list[tuple[float, float]]:
    spans = sorted(
        (
            (float(item["start_seconds"]), float(item["end_seconds"]))
            for item in repairs
            if item.get("type") == "drop_span"
        ),
        key=lambda pair: pair[0],
    )
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def remap_after_drops(seconds: float, drop_spans: Iterable[tuple[float, float]]) -> float:
    value = float(seconds)
    removed = 0.0
    for start, end in drop_spans:
        if value >= end:
            removed += end - start
        elif value > start:
            removed += value - start
            break
        else:
            break
    return max(0.0, value - removed)


def build_drop_filter(duration: float, drop_spans: Iterable[tuple[float, float]]) -> tuple[str, int]:
    """Return an A/V concat filter that removes exact original-time spans."""
    source_duration = _finite_float(duration)
    if source_duration is None or source_duration <= 0:
        raise ValueError("drop-filter duration must be finite and positive")
    drops = _merge_drop_spans(
        {
            "start_seconds": start,
            "end_seconds": end,
            "type": "drop_span",
        }
        for start, end in drop_spans
    )
    for start, end in drops:
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("drop-filter contains invalid span")
        if end > source_duration + 0.05:
            raise ValueError("drop-filter span exceeds source duration")
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in drops:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < source_duration:
        keep.append((cursor, source_duration))
    keep = [(start, end) for start, end in keep if end - start >= 0.02]
    if not keep:
        raise ValueError("drop spans remove the entire source")

    parts: list[str] = []
    concat_inputs: list[str] = []
    for index, (start, end) in enumerate(keep):
        parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]"
        )
        parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    parts.append("".join(concat_inputs) + f"concat=n={len(keep)}:v=1:a=1[outv][outa]")
    return ";".join(parts), len(keep)


def _publish_new_file(temp_path: Path, final_path: Path) -> None:
    try:
        os.link(temp_path, final_path)
    except FileExistsError:
        raise
    except OSError:
        created = False
        try:
            with final_path.open("xb") as target, temp_path.open("rb") as source:
                created = True
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except FileExistsError:
            raise
        except Exception:
            if created:
                final_path.unlink(missing_ok=True)
            raise
    finally:
        temp_path.unlink(missing_ok=True)


async def apply_safe_repairs(
    *,
    source_video_path: Path,
    output_path: Path,
    duration: float,
    repairs: Iterable[dict[str, Any]],
) -> Path:
    """Execute only v1 drop/mute operations with exact source/output safety."""
    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    if source_video_path.resolve(strict=False) == output_path.resolve(strict=False):
        raise RuntimeError("refusing to overwrite source video")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing repair output: {output_path}")
    repair_list = list(repairs)
    unsupported = [item for item in repair_list if item.get("type") not in EXECUTABLE_ACTIONS]
    if unsupported:
        raise ValueError("review contains non-executable v1 repair actions")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    if not source_video_path.exists() or source_video_path.stat().st_size <= 1024:
        raise FileNotFoundError(source_video_path)
    declared_duration = _finite_float(duration)
    if declared_duration is None or declared_duration <= 0:
        raise ValueError("repair duration must be finite and positive")
    source_probe = await probe_media_async(source_video_path)
    if not media_probe_is_deliverable(source_probe):
        raise RuntimeError("repair source failed video+audio media probe")
    assert source_probe is not None
    actual_duration = float(source_probe.duration)
    if abs(actual_duration - declared_duration) > 1.0:
        raise RuntimeError(
            f"repair source duration drift: manifest={declared_duration:.3f}s probe={actual_duration:.3f}s"
        )
    for index, item in enumerate(repair_list, 1):
        start = _finite_float(item.get("start_seconds"))
        end = _finite_float(item.get("end_seconds"))
        if start is None or end is None or start < 0 or end <= start or end > actual_duration + 0.05:
            raise ValueError(f"repair[{index}] has invalid/out-of-range span")
    drop_budget_errors = validate_drop_span_budget(repair_list, actual_duration)
    if drop_budget_errors:
        raise ValueError("unsafe automatic drop_span repair: " + "; ".join(drop_budget_errors))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    drops = _merge_drop_spans(repair_list)
    mutes = [item for item in repair_list if item.get("type") == "mute_span"]
    intermediate: Path | None = None
    final_temp: Path | None = None
    current_source = source_video_path
    expected_duration = actual_duration - sum(end - start for start, end in drops)

    try:
        if drops:
            filter_complex, _ = build_drop_filter(actual_duration, drops)
            fd, temp_name = tempfile.mkstemp(
                prefix=output_path.stem + "_drop_",
                suffix=".mp4",
                dir=output_path.parent,
            )
            os.close(fd)
            intermediate = Path(temp_name)
            intermediate.unlink(missing_ok=True)
            command = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(source_video_path),
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
                str(intermediate),
            ]
            result = await run_cancellable_process(command, timeout=7200, text=True)
            if result.returncode != 0 or not intermediate.exists() or intermediate.stat().st_size <= 1024:
                raise RuntimeError("ffmpeg drop-span repair failed")
            current_source = intermediate

        fd, final_name = tempfile.mkstemp(
            prefix=output_path.stem + "_final_",
            suffix=".mp4",
            dir=output_path.parent,
        )
        os.close(fd)
        final_temp = Path(final_name)
        final_temp.unlink(missing_ok=True)
        if mutes:
            filters: list[str] = []
            for item in mutes:
                start = remap_after_drops(float(item["start_seconds"]), drops)
                end = remap_after_drops(float(item["end_seconds"]), drops)
                if end <= start:
                    continue
                filters.append(f"volume=enable='between(t,{start:.3f},{end:.3f})':volume=0")
            audio_filter = ",".join(filters) if filters else "anull"
            command = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(current_source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "copy",
                "-af",
                audio_filter,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-n",
                str(final_temp),
            ]
        else:
            command = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(current_source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-n",
                str(final_temp),
            ]
        result = await run_cancellable_process(command, timeout=7200, text=True)
        if result.returncode != 0 or not final_temp.exists() or final_temp.stat().st_size <= 1024:
            raise RuntimeError("ffmpeg final editorial repair failed")
        final_probe = await probe_media_async(final_temp)
        if not media_probe_is_deliverable(final_probe):
            raise RuntimeError("final editorial repair failed media probe")
        assert final_probe is not None
        tolerance = max(0.35, min(1.0, expected_duration * 0.01))
        if abs(float(final_probe.duration) - expected_duration) > tolerance:
            raise RuntimeError(
                f"final editorial repair duration mismatch: expected={expected_duration:.3f}s "
                f"actual={final_probe.duration:.3f}s"
            )
        _publish_new_file(final_temp, output_path)
        final_temp = None
        return output_path
    finally:
        if intermediate is not None:
            intermediate.unlink(missing_ok=True)
        if final_temp is not None:
            final_temp.unlink(missing_ok=True)


def _heard_text(value: Any) -> str:
    """Preserve ASR evidence; normalize whitespace only, never typo-rewrite words."""
    return " ".join(str(value or "").strip().split())


async def transcribe_russian_whisper(
    video_path: Path,
    *,
    srt_output: Path,
    words_output: Path,
    ai_data: dict[str, Any] | None = None,
    model_name: str = "large-v3",
) -> tuple[Path, Path]:
    """Transcribe the complete Russian translated source with word timestamps."""
    from core.resource_scheduler import scheduler
    from services import shorts_video_impl

    video_path = Path(video_path)
    if not video_path.exists() or video_path.stat().st_size <= 1024:
        raise FileNotFoundError(video_path)
    if not shorts_video_impl.HAS_FASTER_WHISPER:
        raise RuntimeError("faster-whisper is unavailable")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    srt_output = Path(srt_output)
    words_output = Path(words_output)
    srt_output.parent.mkdir(parents=True, exist_ok=True)
    words_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        wav_path = Path(handle.name)
    try:
        result = await run_cancellable_process(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(video_path),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                "-y",
                str(wav_path),
            ],
            timeout=1800,
            text=True,
        )
        if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size <= 1024:
            raise RuntimeError("could not extract Russian audio for Whisper")

        initial_prompt = shorts_video_impl.build_whisper_initial_prompt(
            ai_data,
            use_gemini_hints=True,
        )

        def _run_whisper() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            model = shorts_video_impl._get_whisper_model(model_name)
            segments, _info = model.transcribe(
                str(wav_path),
                language="ru",
                initial_prompt=initial_prompt,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=True,
            )
            segment_rows: list[dict[str, Any]] = []
            word_rows: list[dict[str, Any]] = []
            for segment in segments:
                text = _heard_text(segment.text)
                if text:
                    segment_rows.append(
                        {
                            "start": float(segment.start),
                            "end": float(segment.end),
                            "text": text,
                        }
                    )
                for word in segment.words or []:
                    heard = _heard_text(word.word)
                    if not heard or word.start is None or word.end is None:
                        continue
                    word_rows.append(
                        {
                            "word": heard,
                            "start": round(float(word.start), 3),
                            "end": round(float(word.end), 3),
                        }
                    )
            return segment_rows, word_rows

        async with scheduler.whisper:
            segment_rows, word_rows = await await_owned_coroutine(asyncio.to_thread(_run_whisper))
        if not segment_rows:
            raise RuntimeError("Whisper returned no Russian speech")

        srt_payload = "".join(
            f"{index}\n{_srt_time(row['start'])} --> {_srt_time(row['end'])}\n{row['text']}\n\n"
            for index, row in enumerate(segment_rows, 1)
        )
        words_payload = json.dumps(word_rows, ensure_ascii=False, indent=2, allow_nan=False)
        fd, srt_temp_name = tempfile.mkstemp(prefix=f".{srt_output.name}.", dir=srt_output.parent)
        os.close(fd)
        fd, words_temp_name = tempfile.mkstemp(prefix=f".{words_output.name}.", dir=words_output.parent)
        os.close(fd)
        srt_temp = Path(srt_temp_name)
        words_temp = Path(words_temp_name)
        try:
            srt_temp.write_text(srt_payload, encoding="utf-8")
            words_temp.write_text(words_payload, encoding="utf-8")
            os.replace(srt_temp, srt_output)
            os.replace(words_temp, words_output)
        finally:
            srt_temp.unlink(missing_ok=True)
            words_temp.unlink(missing_ok=True)
        return srt_output, words_output
    finally:
        wav_path.unlink(missing_ok=True)


__all__ = [
    "ACTION_TYPES",
    "EXECUTABLE_ACTIONS",
    "ISSUE_SEVERITIES",
    "MAX_DROP_SPAN_SECONDS",
    "PACK_SCHEMA_NAME",
    "PACK_SCHEMA_VERSION",
    "REVIEW_SCHEMA_NAME",
    "REVIEW_SCHEMA_VERSION",
    "VERDICTS",
    "SrtCue",
    "apply_safe_repairs",
    "build_drop_filter",
    "build_review_pack",
    "collect_executable_repairs",
    "drop_span_budget_seconds",
    "find_donor_cues",
    "load_pack_manifest",
    "parse_srt",
    "parse_srt_text",
    "remap_after_drops",
    "sha256_file",
    "transcribe_russian_whisper",
    "validate_drop_span_budget",
    "validate_review_document",
]
