#!/usr/bin/env python3
"""PowerShell-friendly CLI for translation editorial QA and safe repairs."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.translation_editorial import (
    EXECUTABLE_ACTIONS,
    REVIEW_SCHEMA_NAME,
    REVIEW_SCHEMA_VERSION,
    apply_safe_repairs,
    build_review_pack,
    collect_executable_repairs,
    find_donor_cues,
    load_pack_manifest,
    sha256_file,
    transcribe_russian_whisper,
    validate_review_document,
)


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _candidate_groups(path: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path is None:
        return [], []
    data = _json(path)
    shorts = data.get("shorts")
    longs = data.get("long_clips")
    if shorts is None:
        shorts = data.get("shorts_candidates")
    if longs is None:
        longs = data.get("long_candidates")
    return (
        [dict(item) for item in (shorts or []) if isinstance(item, dict)],
        [dict(item) for item in (longs or []) if isinstance(item, dict)],
    )


async def _download_original_srt(
    video_url: str,
    output_dir: Path,
    *,
    language: str = "en",
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("editorial_original*.srt"):
        old.unlink(missing_ok=True)

    lang_root = (language or "en").split("-", 1)[0].lower()
    languages = f"{lang_root}.*,{lang_root},en.*,en"
    template = output_dir / "editorial_original_%(id)s.%(ext)s"

    async def attempt(auto: bool) -> Path | None:
        command = [
            *YTDLP_BASE_ARGS,
            "--skip-download",
            "--write-auto-subs" if auto else "--write-subs",
            "--sub-langs",
            languages,
            "--sub-format",
            "srt/best",
            "--convert-subs",
            "srt",
            "--output",
            str(template),
            video_url,
        ]
        result = await run_cancellable_process(command, timeout=300, text=True)
        if result.returncode != 0:
            return None
        candidates = sorted(
            output_dir.glob("editorial_original*.srt"),
            key=lambda item: item.stat().st_size,
            reverse=True,
        )
        return candidates[0] if candidates else None

    for auto in (False, True):
        candidate = await attempt(auto)
        if candidate is not None and candidate.stat().st_size > 0:
            return candidate
    raise RuntimeError("YouTube original SRT is unavailable")


def _review_template(manifest: dict[str, Any]) -> dict[str, Any]:
    candidates = manifest.get("candidates") or {}
    return {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_pack_id": manifest.get("review_pack_id"),
        "reviewer": "chatgpt|gemini|human",
        "full_sermon": {
            "verdict": "keep",
            "issues": [],
        },
        "candidate_reviews": [
            {
                "candidate_id": item.get("candidate_id"),
                "verdict": "keep",
                "issues": [],
            }
            for group in candidates.values()
            if isinstance(group, list)
            for item in group
            if isinstance(item, dict) and item.get("candidate_id")
        ],
    }


async def _cmd_prepare(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    input_dir = output_dir / f"{args.media_id}_translation_editorial_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    original_srt = await _download_original_srt(
        args.url,
        input_dir,
        language=args.original_language,
    )
    russian_srt = input_dir / "russian_whisper.srt"
    russian_words = input_dir / "russian_whisper_words.json"
    await transcribe_russian_whisper(
        Path(args.source_video),
        srt_output=russian_srt,
        words_output=russian_words,
        model_name=args.whisper_model,
    )
    shorts, longs = _candidate_groups(Path(args.candidates) if args.candidates else None)
    pack = build_review_pack(
        output_dir=output_dir,
        media_id=args.media_id,
        source_url=args.url,
        title=args.title,
        performer=args.performer,
        duration=args.duration,
        source_video_path=Path(args.source_video),
        original_srt_path=original_srt,
        russian_whisper_srt_path=russian_srt,
        russian_words_path=russian_words,
        shorts_candidates=shorts,
        long_candidates=longs,
    )
    manifest = load_pack_manifest(pack)
    template_path = output_dir / f"{args.media_id}_review_template.json"
    template_path.write_text(
        json.dumps(_review_template(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(pack)
    print(template_path)
    return 0


async def _cmd_transcribe(args: argparse.Namespace) -> int:
    srt, words = await transcribe_russian_whisper(
        Path(args.video),
        srt_output=Path(args.srt),
        words_output=Path(args.words),
        model_name=args.whisper_model,
    )
    print(srt)
    print(words)
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    shorts, longs = _candidate_groups(Path(args.candidates) if args.candidates else None)
    pack = build_review_pack(
        output_dir=Path(args.output_dir),
        media_id=args.media_id,
        source_url=args.url,
        title=args.title,
        performer=args.performer,
        duration=args.duration,
        source_video_path=Path(args.source_video),
        original_srt_path=Path(args.original_srt),
        russian_whisper_srt_path=Path(args.russian_srt),
        russian_words_path=Path(args.russian_words) if args.russian_words else None,
        shorts_candidates=shorts,
        long_candidates=longs,
    )
    manifest = load_pack_manifest(pack)
    template_path = Path(args.output_dir) / f"{args.media_id}_review_template.json"
    template_path.write_text(
        json.dumps(_review_template(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(pack)
    print(template_path)
    return 0


def _cmd_donors(args: argparse.Namespace) -> int:
    rows = find_donor_cues(
        Path(args.russian_srt),
        args.phrase,
        exclude_start=args.exclude_start,
        exclude_end=args.exclude_end,
        limit=args.limit,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _load_and_validate(pack_path: Path, review_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_pack_manifest(pack_path)
    review = _json(review_path)
    errors = validate_review_document(review, manifest)
    if errors:
        raise ValueError("review validation failed:\n- " + "\n- ".join(errors))
    return manifest, review


def _cmd_validate(args: argparse.Namespace) -> int:
    manifest, review = _load_and_validate(Path(args.pack), Path(args.review))
    print(
        json.dumps(
            {
                "valid": True,
                "review_pack_id": manifest.get("review_pack_id"),
                "full_verdict": (review.get("full_sermon") or {}).get("verdict"),
                "executable_repairs": len(collect_executable_repairs(review)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def _cmd_repair(args: argparse.Namespace) -> int:
    manifest, review = _load_and_validate(Path(args.pack), Path(args.review))
    full = review.get("full_sermon") or {}
    if full.get("verdict") == "reject":
        raise RuntimeError("full sermon is rejected; repair execution is blocked")

    unresolved: list[str] = []
    for issue in full.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        action = issue.get("action") or {}
        action_type = action.get("type")
        if action_type not in EXECUTABLE_ACTIONS:
            unresolved.append(str(action_type or "missing"))
    if unresolved:
        raise RuntimeError(
            "review contains non-executable actions: " + ", ".join(sorted(set(unresolved)))
        )

    source = (manifest.get("source") or {}).get("translated_video") or {}
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise FileNotFoundError(
            "translated source path from the review pack no longer exists: " + str(source_path)
        )
    expected_sha = str(source.get("sha256") or "")
    actual_sha = sha256_file(source_path)
    if actual_sha != expected_sha:
        raise RuntimeError(
            "translated source bytes changed since review pack creation; repair refused"
        )

    repairs = collect_executable_repairs(review)
    if not repairs:
        if Path(args.output).resolve(strict=False) == source_path.resolve(strict=False):
            raise RuntimeError("refusing to overwrite source video")
        shutil.copy2(source_path, Path(args.output))
        print(Path(args.output))
        return 0

    output = await apply_safe_repairs(
        source_video_path=source_path,
        output_path=Path(args.output),
        duration=float((manifest.get("source") or {}).get("duration_seconds") or 0.0),
        repairs=repairs,
    )
    print(output)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translation Editorial QA: review packs, donor lookup and guarded FFmpeg repair"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser(
        "prepare",
        help="download original SRT, transcribe translated video with Whisper and build one review ZIP",
    )
    prepare.add_argument("--url", required=True)
    prepare.add_argument("--source-video", required=True)
    prepare.add_argument("--media-id", required=True)
    prepare.add_argument("--title", default="")
    prepare.add_argument("--performer", default="")
    prepare.add_argument("--duration", type=float, required=True)
    prepare.add_argument("--candidates")
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--original-language", default="en")
    prepare.add_argument("--whisper-model", default="large-v3")

    transcribe = sub.add_parser("transcribe-ru", help="full Russian Whisper SRT + word timestamps")
    transcribe.add_argument("--video", required=True)
    transcribe.add_argument("--srt", required=True)
    transcribe.add_argument("--words", required=True)
    transcribe.add_argument("--whisper-model", default="large-v3")

    pack = sub.add_parser("pack", help="build review ZIP from existing transcripts")
    pack.add_argument("--source-video", required=True)
    pack.add_argument("--original-srt", required=True)
    pack.add_argument("--russian-srt", required=True)
    pack.add_argument("--russian-words")
    pack.add_argument("--media-id", required=True)
    pack.add_argument("--url", default="")
    pack.add_argument("--title", default="")
    pack.add_argument("--performer", default="")
    pack.add_argument("--duration", type=float, required=True)
    pack.add_argument("--candidates")
    pack.add_argument("--output-dir", required=True)

    donors = sub.add_parser("donors", help="find same-voice donor cue candidates")
    donors.add_argument("--russian-srt", required=True)
    donors.add_argument("--phrase", required=True)
    donors.add_argument("--exclude-start", type=float)
    donors.add_argument("--exclude-end", type=float)
    donors.add_argument("--limit", type=int, default=12)

    validate = sub.add_parser("validate", help="validate review.json against exact review ZIP")
    validate.add_argument("--pack", required=True)
    validate.add_argument("--review", required=True)

    repair = sub.add_parser("repair", help="execute only approved drop_span/mute_span actions")
    repair.add_argument("--pack", required=True)
    repair.add_argument("--review", required=True)
    repair.add_argument("--output", required=True)
    return parser


async def _amain(args: argparse.Namespace) -> int:
    if args.command == "prepare":
        return await _cmd_prepare(args)
    if args.command == "transcribe-ru":
        return await _cmd_transcribe(args)
    if args.command == "repair":
        return await _cmd_repair(args)
    if args.command == "pack":
        return _cmd_pack(args)
    if args.command == "donors":
        return _cmd_donors(args)
    if args.command == "validate":
        return _cmd_validate(args)
    raise RuntimeError(f"unknown command: {args.command}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
