#!/usr/bin/env python3
"""Deterministic editorial QA exchange for Yandex LiveDub material.

The module deliberately keeps AI outside the execution boundary.  It builds a
small immutable review pack from the real source transcript, the Russian
Whisper transcript and Factory candidate metadata.  ChatGPT, Gemini or a human
editor may return a versioned review document, but only deterministic actions
validated against the exact pack may reach FFmpeg.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from services.async_process import run_cancellable_process
from services.async_worker import await_owned_coroutine

PACK_SCHEMA_NAME = "mp3telegrambot.translation-editorial-review-pack"
PACK_SCHEMA_VERSION = 1
REVIEW_SCHEMA_NAME = "mp3telegrambot.translation-editorial-review"
REVIEW_SCHEMA_VERSION = 1

VERDICTS = {"keep", "repair", "reject"}
ISSUE_SEVERITIES = {"roughness", "minor", "major", "critical"}
ACTION_TYPES = {"drop_span", "mute_span", "borrow_span", "reject_region"}
EXECUTABLE_ACTIONS = {"drop_span", "mute_span"}

_SRT_TS_RE = re.compile(
    r"(?P<h>\d{1,3}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})"
)
_WORD_KEY_RE = re.compile(r"[^\wёЁ]+", re.UNICODE)


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


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _srt_seconds(value: str) -> float:
    match = _SRT_TS_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000.0
    )


def _srt_time(seconds: float) -> str:
    value = max(0.0, float(seconds))
    millis = int(round(value * 1000.0))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def parse_srt_text(raw: str) -> list[SrtCue]:
    cues: list[SrtCue] = []
    for block in re.split(r"\n\s*\n", str(raw or "").replace("\r\n", "\n")):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_index = 1 if lines[0].isdigit() else 0
        if time_index >= len(lines) or "-->" not in lines[time_index]:
            continue
        left, right = [part.strip() for part in lines[time_index].split("-->", 1)]
        try:
            start = _srt_seconds(left.split()[0])
            end = _srt_seconds(right.split()[0])
        except ValueError:
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
    return " ".join(
        part for part in _WORD_KEY_RE.sub(" ", str(text or "").casefold()).split() if part
    )


def find_donor_cues(
    srt_path: Path,
    phrase: str,
    *,
    exclude_start: float | None = None,
    exclude_end: float | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return grounded same-voice donor cue candidates; never edits media."""
    needle = _word_key(phrase)
    if not needle:
        return []
    out: list[dict[str, Any]] = []
    for cue in parse_srt(srt_path):
        if needle not in _word_key(cue.text):
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
        if len(out) >= max(1, int(limit)):
            break
    return out


def _candidate_payload(kind: str, candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, 1):
        item = dict(candidate)
        item["candidate_id"] = str(item.get("candidate_id") or f"{kind}:{index}")
        out.append(item)
    return out


def _file_entry(path: Path, *, role: str) -> dict[str, Any]:
    path = Path(path)
    return {
        "file": path.name,
        "role": role,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


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
) -> Path:
    """Build one small ZIP suitable for ChatGPT/Gemini/human review.

    The video itself is intentionally not copied into the ZIP.  Its local path,
    size and SHA-256 bind any later repair to the exact translated source bytes.
    """
    source_video_path = Path(source_video_path)
    original_srt_path = Path(original_srt_path)
    russian_whisper_srt_path = Path(russian_whisper_srt_path)
    for required in (source_video_path, original_srt_path, russian_whisper_srt_path):
        if not required.exists() or required.stat().st_size <= 0:
            raise FileNotFoundError(f"review pack input missing/empty: {required}")

    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(media_id or "media"))[:100]
    root = Path(output_dir) / f"{safe_id}_translation_editorial_v1"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

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
    (root / "candidates.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    source_entry = {
        "url": str(source_url or ""),
        "media_id": str(media_id or ""),
        "title": str(title or ""),
        "performer": str(performer or ""),
        "duration_seconds": round(float(duration or 0.0), 3),
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

    identity_payload = {
        "schema_name": PACK_SCHEMA_NAME,
        "schema_version": PACK_SCHEMA_VERSION,
        "source": source_entry,
        "transcripts": transcripts,
        "candidates": candidates,
    }
    review_pack_id = _canonical_sha256(identity_payload)
    manifest = {
        **identity_payload,
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
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    instructions = (
        "# Translation Editorial Review v1\n\n"
        "Upload this ZIP to the editor. Compare `original.srt` with the words actually "
        "heard in `russian_whisper.srt`. Minor stylistic roughness is not a defect. "
        "Return a `review.json` bound to the exact `review_pack_id` in manifest.json.\n\n"
        "Safe automatic repairs in v1: `drop_span` and `mute_span`. `borrow_span` may "
        "identify a same-voice donor candidate, but requires explicit human/editor approval "
        "and is intentionally not auto-executed.\n"
    )
    (root / "REVIEW_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")

    zip_path = Path(output_dir) / f"{safe_id}_translation_editorial_v1.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.iterdir()):
            if path.is_file():
                archive.write(path, arcname=path.name)
    return zip_path


def load_pack_manifest(pack_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(Path(pack_path), "r") as archive:
        raw = archive.read("manifest.json").decode("utf-8")
    data = json.loads(raw)
    if data.get("schema_name") != PACK_SCHEMA_NAME or data.get("schema_version") != PACK_SCHEMA_VERSION:
        raise ValueError("unsupported translation editorial pack schema")
    return data


def validate_review_document(review: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Validate editorial intent without guessing or silently correcting it."""
    errors: list[str] = []
    if review.get("schema_name") != REVIEW_SCHEMA_NAME:
        errors.append("wrong review schema_name")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        errors.append("wrong review schema_version")
    if review.get("review_pack_id") != manifest.get("review_pack_id"):
        errors.append("review_pack_id does not match the exact pack")

    duration = float((manifest.get("source") or {}).get("duration_seconds") or 0.0)
    full = review.get("full_sermon")
    if not isinstance(full, dict) or full.get("verdict") not in VERDICTS:
        errors.append("full_sermon.verdict must be keep|repair|reject")

    candidate_ids = {
        str(item.get("candidate_id"))
        for group in (manifest.get("candidates") or {}).values()
        if isinstance(group, list)
        for item in group
        if isinstance(item, dict) and item.get("candidate_id")
    }
    seen_candidate_reviews: set[str] = set()
    for item in review.get("candidate_reviews") or []:
        if not isinstance(item, dict):
            errors.append("candidate review must be an object")
            continue
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id not in candidate_ids:
            errors.append(f"unknown candidate_id: {candidate_id}")
        if candidate_id in seen_candidate_reviews:
            errors.append(f"duplicate candidate_id: {candidate_id}")
        seen_candidate_reviews.add(candidate_id)
        if item.get("verdict") not in VERDICTS:
            errors.append(f"candidate {candidate_id}: invalid verdict")

    issues = []
    if isinstance(full, dict):
        issues.extend(full.get("issues") or [])
    for item in review.get("candidate_reviews") or []:
        if isinstance(item, dict):
            issues.extend(item.get("issues") or [])

    for index, issue in enumerate(issues, 1):
        prefix = f"issue[{index}]"
        if not isinstance(issue, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        try:
            start = float(issue.get("start_seconds"))
            end = float(issue.get("end_seconds"))
        except (TypeError, ValueError):
            errors.append(f"{prefix}: invalid start/end")
            continue
        if start < 0 or end <= start or (duration > 0 and end > duration + 0.25):
            errors.append(f"{prefix}: span outside source duration")
        if issue.get("severity") not in ISSUE_SEVERITIES:
            errors.append(f"{prefix}: invalid severity")
        action = issue.get("action")
        if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
            errors.append(f"{prefix}: invalid action")
            continue
        if action.get("type") == "borrow_span":
            try:
                donor_start = float(action.get("donor_start_seconds"))
                donor_end = float(action.get("donor_end_seconds"))
            except (TypeError, ValueError):
                errors.append(f"{prefix}: borrow_span requires donor start/end")
                continue
            if donor_start < 0 or donor_end <= donor_start or (duration > 0 and donor_end > duration + 0.25):
                errors.append(f"{prefix}: donor span outside source duration")
            if donor_start < end and donor_end > start:
                errors.append(f"{prefix}: donor span overlaps replacement target")
    return errors


def collect_executable_repairs(review: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only explicitly approved v1 actions; unresolved donor/reject stays blocking."""
    full = review.get("full_sermon") or {}
    issues = full.get("issues") or [] if isinstance(full, dict) else []
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
    drops = list(drop_spans)
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in drops:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < float(duration):
        keep.append((cursor, float(duration)))
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
    parts.append(
        "".join(concat_inputs) + f"concat=n={len(keep)}:v=1:a=1[outv][outa]"
    )
    return ";".join(parts), len(keep)


async def apply_safe_repairs(
    *,
    source_video_path: Path,
    output_path: Path,
    duration: float,
    repairs: Iterable[dict[str, Any]],
) -> Path:
    """Execute only v1 drop/mute operations with FFmpeg.

    Borrowed speech is intentionally not synthesized here.  A review containing
    such an action must be handled in a later explicitly approved pass.
    """
    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    repair_list = list(repairs)
    unsupported = [item for item in repair_list if item.get("type") not in EXECUTABLE_ACTIONS]
    if unsupported:
        raise ValueError("review contains non-executable v1 repair actions")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    if not source_video_path.exists():
        raise FileNotFoundError(source_video_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    drops = _merge_drop_spans(repair_list)
    mutes = [item for item in repair_list if item.get("type") == "mute_span"]
    intermediate: Path | None = None
    current_source = source_video_path
    current_duration = float(duration)

    try:
        if drops:
            filter_complex, _ = build_drop_filter(current_duration, drops)
            temp_dir = output_path.parent
            fd, temp_name = tempfile.mkstemp(
                prefix=output_path.stem + "_drop_",
                suffix=".mp4",
                dir=temp_dir,
            )
            os.close(fd)
            intermediate = Path(temp_name)
            intermediate.unlink(missing_ok=True)
            command = [
                ffmpeg,
                "-hide_banner",
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
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-y",
                str(intermediate),
            ]
            result = await run_cancellable_process(command, timeout=7200, text=True)
            if result.returncode != 0 or not intermediate.exists() or intermediate.stat().st_size <= 1024:
                raise RuntimeError("ffmpeg drop-span repair failed")
            current_source = intermediate
            current_duration -= sum(end - start for start, end in drops)

        if mutes:
            filters: list[str] = []
            for item in mutes:
                start = remap_after_drops(float(item["start_seconds"]), drops)
                end = remap_after_drops(float(item["end_seconds"]), drops)
                if end <= start:
                    continue
                filters.append(
                    f"volume=enable='between(t,{start:.3f},{end:.3f})':volume=0"
                )
            audio_filter = ",".join(filters) if filters else "anull"
            command = [
                ffmpeg,
                "-hide_banner",
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
                "-y",
                str(output_path),
            ]
        else:
            command = [
                ffmpeg,
                "-hide_banner",
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
                "-y",
                str(output_path),
            ]
        result = await run_cancellable_process(command, timeout=7200, text=True)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 1024:
            raise RuntimeError("ffmpeg final editorial repair failed")
        return output_path
    finally:
        if intermediate is not None:
            intermediate.unlink(missing_ok=True)


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
                text = shorts_video_impl._polish_subtitle_text(segment.text)
                if text:
                    segment_rows.append(
                        {
                            "start": float(segment.start),
                            "end": float(segment.end),
                            "text": text,
                        }
                    )
                for word in segment.words or []:
                    polished = shorts_video_impl._polish_subtitle_text(word.word)
                    if not polished or word.start is None or word.end is None:
                        continue
                    word_rows.append(
                        {
                            "word": polished,
                            "start": round(float(word.start), 3),
                            "end": round(float(word.end), 3),
                        }
                    )
            return segment_rows, word_rows

        async with scheduler.whisper:
            segment_rows, word_rows = await await_owned_coroutine(
                asyncio.to_thread(_run_whisper)
            )
        if not segment_rows:
            raise RuntimeError("Whisper returned no Russian speech")

        with srt_output.open("w", encoding="utf-8") as stream:
            for index, row in enumerate(segment_rows, 1):
                stream.write(
                    f"{index}\n{_srt_time(row['start'])} --> {_srt_time(row['end'])}\n"
                    f"{row['text']}\n\n"
                )
        words_output.write_text(
            json.dumps(word_rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return srt_output, words_output
    finally:
        wav_path.unlink(missing_ok=True)


__all__ = [
    "ACTION_TYPES",
    "EXECUTABLE_ACTIONS",
    "ISSUE_SEVERITIES",
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
    "find_donor_cues",
    "load_pack_manifest",
    "parse_srt",
    "parse_srt_text",
    "remap_after_drops",
    "sha256_file",
    "transcribe_russian_whisper",
    "validate_review_document",
]
